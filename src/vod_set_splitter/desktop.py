"""VOD Set Splitter's window: a web page (ui/index.html) talking to this Python side through pywebview.

The page plays the video from a small local web server here, so seeking is instant. Everything about a video
(its sets, names and characters) is saved by itself after every change, in the app's folder in AppData.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, fields, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import webview
from PIL import Image

from . import metadata, windows_shell
from .core import (
    HIDDEN,
    ScanProfile,
    SetSegment,
    boundaries_to_segments,
    detect_boundaries,
    edit_segment_bounds,
    end_previous_at,
    export_segment,
    format_time,
    is_default_title,
    mark_segment_done,
    normalize_default_titles,
    overlapping_segments,
    probe_video,
    remove_segment,
    resolve_media_tool,
    safe_filename,
    split_segment_at,
)
from .metadata import detect_set_title
from .thumbnail import find_character_asset, generate_thumbnail, list_characters, list_skins, parse_title

APP_TITLE = "VOD Set Splitter"
ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent / "ui" / "index.html"
DATA = Path(os.environ.get("APPDATA", Path.home())) / APP_TITLE
PROJECTS = DATA / "projects"
CACHE = DATA / "cache"
SETTINGS_FILE = DATA / "settings.json"
VIDEO_TYPES = (".mp4", ".mkv", ".mov", ".ts", ".m4v", ".webm", ".flv")
DEFAULT_SETTINGS = {
    "characterFolder": "",
    "background": str(ROOT / "assets" / "blue-birch-forest.png"),
    "tesseract": "",
    "exactCuts": True,
    "exportFolder": "",
    "profile": asdict(ScanProfile()),
}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def project_file(video: Path) -> Path:
    key = hashlib.sha1(str(video.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return PROJECTS / f"{key}.json"


def segment_from(item: dict) -> SetSegment:
    known = {f.name for f in fields(SetSegment)}
    return SetSegment(**{k: v for k, v in item.items() if k in known})


def profile_from(values: dict | None) -> ScanProfile:
    known = {f.name for f in fields(ScanProfile)}
    merged = {**asdict(ScanProfile()), **{k: v for k, v in (values or {}).items() if k in known}}
    for key in merged:
        if key != "name":
            merged[key] = float(merged[key])
    return ScanProfile(**merged)


class MediaServer:
    """Serves the open video (and character pictures and thumbnail previews) to the page, on this PC only."""

    def __init__(self, api: "Api") -> None:
        self.api = api
        self.token = secrets.token_urlsafe(12)
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, kind: str) -> str:
        return f"http://127.0.0.1:{self.port}/{kind}/{self.token}"

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) < 2 or parts[1] != server.token:
                    return self.send_error(404)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                try:
                    if parts[0] == "video":
                        return self._file(server.api.playable_path())
                    if parts[0] == "render":
                        return self._bytes(server.api.render_png(query.get("c", ""), query.get("s", "")), "image/png")
                    if parts[0] == "thumb":
                        return self._bytes(server.api.thumbnail_jpeg(int(query.get("i", "-1"))), "image/jpeg")
                except (ConnectionError, OSError):
                    return
                self.send_error(404)

            def _bytes(self, data: bytes | None, kind: str):
                if not data:
                    return self.send_error(404)
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _file(self, path: Path | None):
                if not path or not path.is_file():
                    return self.send_error(404)
                size = path.stat().st_size
                start, end = 0, size - 1
                match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if match:
                    if match.group(1):
                        start = int(match.group(1))
                        end = int(match.group(2)) if match.group(2) else end
                    elif match.group(2):
                        start = max(0, size - int(match.group(2)))
                    end = min(end, size - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                kind = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
                        ".mkv": "video/webm"}.get(path.suffix.lower(), "application/octet-stream")
                self.send_header("Content-Type", kind)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                with path.open("rb") as source:
                    source.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = source.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

        return Handler


class Api:
    """Everything the page can ask for. Each change to the sets is saved straight away."""

    def __init__(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self._settings = {**DEFAULT_SETTINGS, **read_json(SETTINGS_FILE, {})}
        metadata.TESSERACT_PATH = self._settings.get("tesseract") or ""
        self._lock = threading.RLock()
        self._window = None
        self._video = None
        self._preview: Path | None = None  # a copy the page can play, for videos it can't play itself
        self._segments: list[SetSegment] = []
        self._undo_stack: list[tuple[list[SetSegment], str]] = []
        self._tournament = ""
        self._profile = profile_from(self._settings.get("profile"))
        self._generation = 0
        self._scanning = False
        self._exporting = False
        self._renders: dict[tuple[str, str, str], bytes] = {}
        self._server = MediaServer(self)

    # ----- Talking to the page -----

    def _emit(self, kind: str, **values) -> None:
        if self._window:
            try:
                self._window.evaluate_js(f"window.onEvent && onEvent({json.dumps({'kind': kind, **values})})")
            except Exception:
                pass

    def _sets(self) -> list[dict]:
        out = []
        for index, s in enumerate(self._segments):
            one, two, round_name = parse_title(s.title)
            out.append({**asdict(s), "index": index, "length": s.end - s.start,
                        "nameOne": s.player_one or (one if " vs" in s.title.lower() else ""),
                        "nameTwo": s.player_two or (two if " vs" in s.title.lower() else ""),
                        "round": s.round_name or (round_name if round_name != "Round" else "")})
        return out

    def _state(self, **extra) -> dict:
        return {"sets": self._sets(), "canUndo": bool(self._undo_stack), **extra}

    def _remember(self, label: str) -> None:
        self._undo_stack.append(([replace(s) for s in self._segments], label))
        self._undo_stack = self._undo_stack[-100:]

    def _save(self) -> None:
        if not self._video:
            return
        write_json(project_file(self._video.path), {
            "source": str(self._video.path),
            "duration": self._video.duration,
            "profile": asdict(self._profile),
            "tournament_name": self._tournament,
            "sets": [asdict(s) for s in self._segments],
            "updated": time.time(),
        })

    def _save_settings(self) -> None:
        write_json(SETTINGS_FILE, self._settings)

    # ----- Starting up -----

    def init(self) -> dict:
        return {"settings": self.settings_info(), "recent": self.recent(), "urls": self.urls()}

    def recent(self) -> list[dict]:
        items = []
        for file in sorted(PROJECTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
            data = read_json(file, None)
            if not data or not data.get("source"):
                continue
            sets = data.get("sets", [])
            items.append({"path": data["source"], "name": Path(data["source"]).name, "sets": len(sets),
                          "done": sum(1 for s in sets if s.get("locked")), "duration": format_time(data.get("duration", 0)),
                          "exists": Path(data["source"]).is_file(), "tournament": data.get("tournament_name", "")})
        return items

    def forget_recent(self, path: str) -> list[dict]:
        project_file(Path(path)).unlink(missing_ok=True)
        return self.recent()

    # ----- Opening a video -----

    def choose_video(self) -> dict | None:
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Videos (*.mp4;*.mkv;*.mov;*.ts;*.m4v;*.webm;*.flv)", "All files (*.*)"))
        return self.open_video(result[0]) if result else None

    def open_video(self, path: str) -> dict:
        source = Path(path)
        if not source.is_file():
            return {"error": "That video isn't there any more."}
        try:
            info = probe_video(source)
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError):
            return {"error": "That file couldn't be read as a video."}
        with self._lock:
            self._generation += 1
            self._video = info
            self._preview = None
            self._undo_stack.clear()
            saved = read_json(project_file(source), {})
            self._segments = normalize_default_titles([segment_from(s) for s in saved.get("sets", [])])
            self._tournament = saved.get("tournament_name") or source.stem.split(" - ")[-1]
            self._profile = profile_from(saved.get("profile") or self._settings.get("profile"))
            self._save()
        return self._state(video=self._video_info())

    def import_project(self) -> dict | None:
        """Opens a project file saved by the old version, or exported sets.json."""
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Projects (*.json)",))
        if not result:
            return None
        data = read_json(Path(result[0]), None)
        if not isinstance(data, dict) or not data.get("source"):
            return {"error": "That isn't a VOD Set Splitter project."}
        opened = self.open_video(data["source"])
        if opened.get("error"):
            return opened
        with self._lock:
            self._segments = normalize_default_titles([segment_from(s) for s in data.get("sets", [])])
            self._tournament = data.get("tournament_name") or self._tournament
            if data.get("profile"):
                self._profile = profile_from(data["profile"])
            self._save()
        return self._state(video=self._video_info())

    def _video_info(self) -> dict:
        v = self._video
        return {"path": str(v.path), "name": v.path.name, "width": v.width, "height": v.height, "fps": v.fps,
                "duration": v.duration, "url": self._server.url("video") + f"?g={self._generation}",
                "tournament": self._tournament, "area": self._area()}

    def urls(self) -> dict:
        return {"render": self._server.url("render"), "thumb": self._server.url("thumb")}

    def open_url(self, url: str) -> None:
        if url.startswith(("https://github.com/", "https://www.gyan.dev/", "https://ffmpeg.org/")):
            os.startfile(url)

    def playable_path(self) -> Path | None:
        if self._preview:
            return self._preview
        return self._video.path if self._video else None

    def make_playable(self) -> dict:
        """For a video the page can't play (like .ts): copies it into an .mp4 once, without re-encoding."""
        if not self._video:
            return {"error": "Open a video first."}
        source = self._video.path
        stat = source.stat()
        target = CACHE / f"preview-{hashlib.sha1(f'{source}{stat.st_size}{stat.st_mtime}'.encode()).hexdigest()[:16]}.mp4"
        if target.is_file():
            self._preview = target
            return {"url": self._server.url("video") + f"?g={self._generation}&p=1"}
        generation = self._generation
        threading.Thread(target=self._remux, args=(source, target, generation), daemon=True).start()
        return {"working": True}

    def _remux(self, source: Path, target: Path, generation: int) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        for old in CACHE.glob("preview-*.mp4"):  # only one copy is kept
            old.unlink(missing_ok=True)
        partial = target.with_suffix(".partial.mp4")
        command = [resolve_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(partial)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=HIDDEN)
        duration = self._video.duration if self._video else 0
        last = 0.0
        for line in process.stdout:
            if line.startswith("out_time_us=") and duration and time.time() - last > 0.3:
                last = time.time()
                try:
                    self._emit("playable_progress", value=min(int(line.split("=")[1]) / 1e6 / duration, 1))
                except ValueError:
                    pass
        if process.wait() == 0 and generation == self._generation:
            partial.replace(target)
            self._preview = target
            self._emit("playable_ready", url=self._server.url("video") + f"?g={self._generation}&p=1")
        else:
            partial.unlink(missing_ok=True)
            self._emit("toast", text="This video can't be played here.")

    # ----- Finding sets -----

    def find_sets(self) -> dict:
        if not self._video:
            return {"error": "Open a video first."}
        if self._scanning:
            return {"working": True}
        if any(s.locked for s in self._segments):
            return {"error": "Some sets are done. Unlock them first, so Find sets doesn't replace them."}
        self._scanning = True
        info, profile, generation = self._video, self._profile, self._generation

        def work():
            last = [0.0]

            def progress(value):
                if time.time() - last[0] > 0.25:
                    last[0] = time.time()
                    self._emit("scan_progress", value=value)

            try:
                boundaries = detect_boundaries(info, profile, progress)
                with self._lock:
                    if generation != self._generation:
                        return
                    self._remember("Find sets")
                    self._segments = boundaries_to_segments(boundaries, info.duration)
                    self._save()
                    self._emit("sets", state=self._state(), text=f"Found {len(self._segments)} sets.", undo=True, select=0)
            except Exception as exc:
                self._emit("toast", text=f"Find sets stopped: {exc}")
            finally:
                self._scanning = False
                self._emit("scan_done")

        threading.Thread(target=work, daemon=True).start()
        return {"working": True}

    # ----- Changing sets -----

    def _change(self, label: str, change) -> dict:
        """Runs one change with Undo, then saves. A change that isn't allowed is put back."""
        with self._lock:
            self._remember(label)
            try:
                result = change()
            except ValueError as exc:
                self._segments = self._undo_stack.pop()[0]
                return {"error": str(exc)}
            except (IndexError, TypeError):
                self._segments = self._undo_stack.pop()[0]
                return {"error": "Choose a set first."}
            self._segments = normalize_default_titles(self._segments)  # "Set NN" names follow the list
            self._save()
            return self._state(**(result or {}))

    def set_boundary(self, index: int, which: str, seconds: float) -> dict:
        def change():
            s = self._segments[index]
            if s.locked:
                raise ValueError("This set is done. Unlock it to change its times.")
            start, end = (seconds, s.end) if which == "start" else (s.start, seconds)
            self._segments = edit_segment_bounds(self._segments, index, start, end, adjust_neighbors=True)
        return self._change("Set start" if which == "start" else "Set end", change)

    def end_previous_here(self, index: int, seconds: float) -> dict:
        def change():
            if index < 1:
                raise ValueError("There's no set before this one.")
            if self._segments[index - 1].locked:
                raise ValueError("The set before this one is done. Unlock it first.")
            self._segments = end_previous_at(self._segments, index, seconds, self._video.duration if self._video else None)
        return self._change("End previous set", change)

    def toggle_done(self, index: int) -> dict:
        segment = self._segments[index]
        if segment.locked:
            return self._change("Unlock", lambda: setattr(self._segments[index], "locked", False))

        def change():
            self._segments = mark_segment_done(self._segments, index, gap_seconds=1.0)
        result = self._change("Mark done", change)
        if not result.get("error") and is_default_title(self._segments[index].title):
            self._name_in_background([self._segments[index]])
            result["naming"] = True
        return result

    def _name_in_background(self, pending: list[SetSegment]) -> None:
        source, generation = self._video.path, self._generation

        def work():
            for segment in pending:
                original = segment.title
                try:
                    found = detect_set_title(source, replace(segment))
                except FileNotFoundError as exc:
                    self._emit("toast", text=str(exc))
                    return
                except Exception:
                    continue
                with self._lock:
                    if generation != self._generation or segment not in self._segments or segment.title != original or not found.title:
                        continue
                    segment.title = found.title
                    segment.player_one = found.player_one.title() if found.player_one else ""
                    segment.player_two = found.player_two.title() if found.player_two else ""
                    segment.round_name = found.round_name or ""
                    self._save()
                    self._emit("sets", state=self._state(), text=f"Named: {found.title}")

        threading.Thread(target=work, daemon=True).start()

    def name_done_sets(self) -> dict:
        pending = [s for s in self._segments if s.locked and is_default_title(s.title)]
        if pending:
            self._name_in_background(pending)
        return {"count": len(pending)}

    def rename(self, index: int, title: str) -> dict:
        def change():
            s = self._segments[index]
            s.title = " ".join((title or "").split()) or f"Set {index + 1:02d}"
            one, two, round_name = parse_title(s.title)
            s.player_one, s.player_two = (one, two) if " vs" in s.title.lower() else ("", "")
            s.round_name = round_name if round_name != "Round" else ""
        return self._change("Rename", change)

    def set_character(self, index: int, side: int, character: str, skin: str) -> dict:
        def change():
            s = self._segments[index]
            skins = list_skins(self._folder(), character)
            chosen = skin if skin in skins else (skins[0] if skins else "Default")
            if side == 1:
                s.character_one, s.skin_one = character, chosen
            else:
                s.character_two, s.skin_two = character, chosen
        return self._change("Character", change)

    def add_set(self, seconds: float) -> dict:
        def change():
            if not self._segments and self._video:
                self._segments = [SetSegment(0.0, self._video.duration, "Set 01")]
            self._segments, new_index = split_segment_at(self._segments, seconds)
            return {"select": new_index}
        return self._change("Add set", change)

    def delete_set(self, index: int) -> dict:
        def change():
            if self._segments[index].locked:
                raise ValueError("This set is done. Unlock it to delete it.")
            if len(self._segments) < 2:
                raise ValueError("A video needs at least one set.")
            self._segments = remove_segment(self._segments, index)
            return {"select": min(index, len(self._segments) - 1)}
        return self._change("Delete set", change)

    def undo(self) -> dict:
        with self._lock:
            if not self._undo_stack:
                return self._state()
            self._segments, label = self._undo_stack.pop()
            self._save()
            return self._state(undone=label)

    def set_tournament(self, name: str) -> dict:
        with self._lock:
            self._tournament = " ".join((name or "").split())
            self._save()
        return {"tournament": self._tournament}

    # ----- Characters and thumbnails -----

    def _folder(self) -> Path | None:
        folder = self._settings.get("characterFolder") or ""
        return Path(folder) if folder and Path(folder).is_dir() else None

    def characters(self) -> dict:
        folder = self._folder()
        names = list_characters(folder)
        return {"characters": names, "skins": {name: list_skins(folder, name) for name in names}}

    def render_png(self, character: str, skin: str) -> bytes | None:
        """A character's picture, trimmed to what's visible, small enough for the page."""
        key = (str(self._folder()), character, skin)
        if key not in self._renders:
            asset = find_character_asset(self._folder(), character, skin)
            if not asset:
                return None
            with Image.open(asset) as source:
                image = source.convert("RGBA")
            bounds = image.getchannel("A").getbbox()
            if bounds:
                image = image.crop(bounds)
            image.thumbnail((360, 360), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, "PNG")
            self._renders[key] = out.getvalue()
        return self._renders[key]

    def thumbnail_jpeg(self, index: int) -> bytes | None:
        if not 0 <= index < len(self._segments):
            return None
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "thumb.jpg"
            generate_thumbnail(replace(self._segments[index]), self._tournament, path, self._folder(), self._background())
            return path.read_bytes()

    def _background(self) -> Path | None:
        value = self._settings.get("background") or ""
        return Path(value) if value and Path(value).is_file() else None

    # ----- Export -----

    def export(self) -> dict:
        if not self._video or not self._segments:
            return {"error": "There are no sets to export."}
        if self._exporting:
            return {"working": True}
        overlaps = overlapping_segments(self._segments)
        if overlaps:
            first, second = overlaps[0]
            return {"error": f"{first.title} overlaps {second.title}. Fix their times first."}
        start = self._settings.get("exportFolder") or str(self._video.path.parent)
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not result:
            return {"cancelled": True}
        destination = Path(result[0] if isinstance(result, (list, tuple)) else result)
        self._settings["exportFolder"] = str(destination)
        self._save_settings()
        self._exporting = True
        source, segments, exact = self._video.path, [replace(s) for s in self._segments], bool(self._settings.get("exactCuts", True))
        tournament, folder, background = self._tournament or source.stem, self._folder(), self._background()

        def work():
            try:
                for number, segment in enumerate(segments, start=1):
                    self._emit("export_progress", done=number - 1, total=len(segments), title=segment.title)
                    name = f"{number:02d} - {safe_filename(segment.title)}"
                    export_segment(source, destination / f"{name}.mp4", segment, exact=exact)
                    generate_thumbnail(segment, tournament, destination / "thumbnails" / f"{name}.jpg", folder, background)
                write_json(destination / "sets.json", [asdict(s) for s in segments])
                self._emit("export_done", total=len(segments), folder=str(destination))
            except Exception as exc:
                self._emit("toast", text=f"Export stopped: {exc}")
                self._emit("export_done", total=0, folder="")
            finally:
                self._exporting = False

        threading.Thread(target=work, daemon=True).start()
        return {"working": True, "total": len(segments)}

    def open_folder(self, path: str) -> None:
        if path and Path(path).is_dir():
            os.startfile(path)

    # ----- Settings -----

    def _area(self) -> dict:
        p = self._profile
        return {"x": p.crop_x, "y": p.crop_y, "w": p.crop_width, "h": p.crop_height}

    def settings_info(self) -> dict:
        def found(tool):
            try:
                return bool(resolve_media_tool(tool))
            except FileNotFoundError:
                return False
        try:
            tesseract = str(metadata.resolve_tesseract())
        except FileNotFoundError:
            tesseract = ""
        p = profile_from(self._settings.get("profile"))
        return {"characterFolder": self._settings.get("characterFolder", ""), "background": self._settings.get("background", ""),
                "tesseract": tesseract, "ffmpeg": found("ffmpeg") and found("ffprobe"), "exactCuts": self._settings.get("exactCuts", True),
                "every": p.sample_interval, "change": p.change_threshold, "settle": p.settle_seconds, "shortest": p.minimum_set_seconds,
                "characters": len(list_characters(self._folder()))}

    def choose_path(self, key: str) -> dict:
        if key == "characterFolder":
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        elif key == "background":
            result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Pictures (*.jpg;*.jpeg;*.png;*.webp)",))
        elif key == "tesseract":
            result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Tesseract (tesseract.exe)",))
        else:
            return {"error": "Unknown setting."}
        if not result:
            return self.settings_info()
        return self.set_setting(key, result[0] if isinstance(result, (list, tuple)) else result)

    def set_setting(self, key: str, value) -> dict:
        with self._lock:
            if key in ("characterFolder", "background", "tesseract"):
                self._settings[key] = str(value or "")
                if key == "tesseract":
                    metadata.TESSERACT_PATH = self._settings[key]
                self._renders.clear()
            elif key == "exactCuts":
                self._settings[key] = bool(value)
            elif key in ("every", "change", "settle", "shortest"):
                name = {"every": "sample_interval", "change": "change_threshold", "settle": "settle_seconds",
                        "shortest": "minimum_set_seconds"}[key]
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return {"error": "Type a number."}
                if number <= 0 or (key == "change" and number >= 1):
                    return {"error": "That number is out of range."}
                self._settings["profile"] = {**self._settings.get("profile", {}), name: number}
                self._profile = profile_from({**asdict(self._profile), name: number})
                self._save()
            else:
                return {"error": "Unknown setting."}
            self._save_settings()
        return self.settings_info()

    def set_scoreboard_area(self, x: float, y: float, w: float, h: float) -> dict:
        x, y = max(0.0, min(float(x), 1.0)), max(0.0, min(float(y), 1.0))
        w, h = max(0.02, min(float(w), 1 - x)), max(0.02, min(float(h), 1 - y))
        with self._lock:
            area = {"crop_x": round(x, 4), "crop_y": round(y, 4), "crop_width": round(w, 4), "crop_height": round(h, 4)}
            self._profile = profile_from({**asdict(self._profile), **area})
            self._settings["profile"] = {**self._settings.get("profile", {}), **area}
            self._save_settings()
            self._save()
        return {"area": self._area()}

    # ----- Drag and drop -----

    def dropped(self, event) -> None:
        try:
            files = event["dataTransfer"]["files"]
            path = next((f.get("pywebviewFullPath") for f in files if str(f.get("name", "")).lower().endswith(VIDEO_TYPES)), None)
        except (KeyError, TypeError):
            path = None
        if path:
            result = self.open_video(path)
            self._emit("opened", result=result)


def _on_start(window, api):
    windows_shell.set_window_icon(APP_TITLE)
    try:
        from webview.dom import DOMEventHandler
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        window.dom.document.events.drop += DOMEventHandler(api.dropped, True, True)
    except Exception:
        pass
    try:
        windows_shell.make_start_menu_shortcut()
    except Exception:
        pass


def main() -> None:
    windows_shell.use_own_taskbar_identity()
    api = Api()
    window = webview.create_window(APP_TITLE, str(UI), js_api=api, width=1480, height=920, min_size=(1100, 700),
                                   background_color="#121417")
    api._window = window
    webview.start(_on_start, (window, api))
