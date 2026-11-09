from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

# No console window flashing up for each ffmpeg call when the app runs without one.
HIDDEN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float


@dataclass
class SetSegment:
    start: float
    end: float
    title: str
    confidence: float = 1.0
    locked: bool = False
    player_one: str = ""
    player_two: str = ""
    round_name: str = ""
    character_one: str = ""
    skin_one: str = "Default"
    character_two: str = ""
    skin_two: str = "Default"


@dataclass(frozen=True)
class ScanProfile:
    name: str = "Right-side scoreboard"
    crop_x: float = 0.68
    crop_y: float = 0.38
    crop_width: float = 0.31
    crop_height: float = 0.36
    sample_interval: float = 5.0
    change_threshold: float = 0.08
    settle_seconds: float = 30.0
    minimum_set_seconds: float = 180.0


@lru_cache(maxsize=None)
def resolve_media_tool(name: str) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    discovered = shutil.which(executable)
    if discovered:
        return discovered

    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        project_root / "tools" / executable,
        Path(sys.executable).resolve().parent / executable,
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "chocolatey" / "bin" / executable,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    searched = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(f"Could not find {executable}. Searched PATH and:\n{searched}")


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Use SS, MM:SS, or HH:MM:SS")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Time values must be numeric") from exc
    if any(number < 0 for number in numbers):
        raise ValueError("Time values cannot be negative")
    if len(numbers) > 1 and any(number >= 60 for number in numbers[1:]):
        raise ValueError("Minutes and seconds must be below 60")
    return sum(number * (60 ** index) for index, number in enumerate(reversed(numbers)))


def format_time(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def probe_video(path: Path) -> VideoInfo:
    command = [
        resolve_media_tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=width,height,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, creationflags=HIDDEN)
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    numerator, denominator = (int(part) for part in stream["r_frame_rate"].split("/"))
    return VideoInfo(
        path=path,
        duration=float(payload["format"]["duration"]),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=numerator / denominator,
    )


def select_sample_indices(timestamps: Sequence[float], interval: float) -> set[int]:
    selected: set[int] = set()
    next_sample = 0.0
    for index, timestamp in enumerate(timestamps):
        if timestamp >= next_sample:
            selected.add(index)
            next_sample = timestamp + interval
    return selected


def _keyframe_times(path: Path, max_duration: float | None) -> list[float]:
    command = [
        resolve_media_tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_packets",
        "-show_entries",
        "packet=pts_time,flags",
        "-of",
        "csv=p=0",
        *(["-read_intervals", f"0%+{max_duration}"] if max_duration is not None else []),
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, creationflags=HIDDEN)
    timestamps: list[float] = []
    for line in result.stdout.splitlines():
        fields = line.split(",")
        if len(fields) >= 2 and "K" in fields[1]:
            try:
                timestamps.append(float(fields[0]))
            except ValueError:
                continue
    if not timestamps:
        raise RuntimeError("No video keyframes were found")
    return timestamps


def _iter_gray_frames(
    path: Path, profile: ScanProfile, size: int = 64, max_duration: float | None = None
) -> Iterator[tuple[float, bytes]]:
    timestamps = _keyframe_times(path, max_duration)
    selected_indices = select_sample_indices(timestamps, profile.sample_interval)
    crop = (
        f"crop=iw*{profile.crop_width}:ih*{profile.crop_height}:"
        f"iw*{profile.crop_x}:ih*{profile.crop_y}"
    )
    filters = f"{crop},scale={size}:{size},format=gray"
    command = [
        resolve_media_tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-skip_frame",
        "nokey",
        "-i",
        str(path),
        *(["-t", str(max_duration)] if max_duration is not None else []),
        "-vf",
        filters,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-fps_mode",
        "passthrough",
        "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=HIDDEN)
    assert process.stdout is not None
    frame_size = size * size
    try:
        for index, timestamp in enumerate(timestamps):
            frame = process.stdout.read(frame_size)
            if len(frame) == frame_size:
                if index in selected_indices:
                    yield timestamp, frame
            else:
                break
    finally:
        process.stdout.close()
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code:
            raise RuntimeError(stderr.strip() or f"FFmpeg exited with code {return_code}")


def frame_difference(previous: bytes, current: bytes) -> float:
    if len(previous) != len(current) or not previous:
        raise ValueError("Frames must be non-empty and have equal lengths")
    changed = sum(abs(left - right) >= 24 for left, right in zip(previous, current))
    return changed / len(previous)


def collapse_change_times(
    times: Iterable[float], settle_seconds: float, minimum_set_seconds: float
) -> list[float]:
    groups: list[list[float]] = []
    for time in sorted(times):
        if not groups or time - groups[-1][-1] > settle_seconds:
            groups.append([time])
        else:
            groups[-1].append(time)

    collapsed = [group[len(group) // 2] for group in groups]
    accepted: list[float] = []
    for time in collapsed:
        if not accepted or time - accepted[-1] >= minimum_set_seconds:
            accepted.append(time)
    return accepted


def detect_boundaries(
    info: VideoInfo,
    profile: ScanProfile,
    progress: Callable[[float], None] | None = None,
    max_duration: float | None = None,
) -> list[float]:
    previous: bytes | None = None
    candidates: list[float] = []
    scan_duration = min(info.duration, max_duration) if max_duration is not None else info.duration
    for timestamp, frame in _iter_gray_frames(info.path, profile, max_duration=max_duration):
        if previous is not None and frame_difference(previous, frame) >= profile.change_threshold:
            candidates.append(timestamp)
        previous = frame
        if progress:
            progress(min(timestamp / scan_duration, 1.0))
    if progress:
        progress(1.0)
    return collapse_change_times(candidates, profile.settle_seconds, profile.minimum_set_seconds)


def boundaries_to_segments(boundaries: Sequence[float], duration: float) -> list[SetSegment]:
    points = [0.0, *(point for point in boundaries if 0 < point < duration), duration]
    points = sorted(set(points))
    return [
        SetSegment(start=start, end=end, title=f"Set {index:02d}", confidence=0.65)
        for index, (start, end) in enumerate(zip(points, points[1:]), start=1)
    ]


def edit_segment_bounds(
    segments: Sequence[SetSegment],
    index: int,
    start: float,
    end: float,
    adjust_neighbors: bool = True,
) -> list[SetSegment]:
    if not 0 <= index < len(segments):
        raise IndexError("Set index is out of range")
    if start < 0:
        raise ValueError("Set start cannot be negative")
    if end <= start:
        raise ValueError("Set end must be after its start")

    updated = [replace(segment) for segment in segments]
    current = updated[index]
    if current.locked:
        raise ValueError(f"{current.title} is done. Unlock it before changing it")

    if index > 0 and start < updated[index - 1].end:
        previous = updated[index - 1]
        if not adjust_neighbors:
            raise ValueError(f"Start overlaps {previous.title}")
        if previous.locked:
            raise ValueError(f"Start overlaps done set {previous.title}. Unlock it first")
        if start <= previous.start:
            raise ValueError(f"That would remove all of {previous.title}")
        previous.end = start

    if index + 1 < len(updated) and end > updated[index + 1].start:
        following = updated[index + 1]
        if not adjust_neighbors:
            raise ValueError(f"End overlaps {following.title}")
        if following.locked:
            raise ValueError(f"End overlaps done set {following.title}. Unlock it first")
        if end >= following.end:
            raise ValueError(f"That would remove all of {following.title}")
        following.start = end

    updated[index] = replace(current, start=start, end=end)
    overlaps = overlapping_segments(updated)
    if overlaps:
        left, right = overlaps[0]
        raise ValueError(f"{left.title} overlaps {right.title}. Leave dead time as a gap instead")
    return updated


def overlapping_segments(segments: Sequence[SetSegment]) -> list[tuple[SetSegment, SetSegment]]:
    return [
        (left, right)
        for left, right in zip(segments, segments[1:])
        if left.end > right.start
    ]


def end_previous_at(
    segments: Sequence[SetSegment],
    selected_index: int,
    playhead: float,
    video_duration: float | None = None,
) -> list[SetSegment]:
    if not 0 < selected_index < len(segments):
        raise IndexError("Select Set 2 or later")
    updated = [replace(segment) for segment in segments]
    previous = updated[selected_index - 1]
    current = updated[selected_index]
    if previous.locked:
        raise ValueError(f"{previous.title} is done. Unlock it first")
    if playhead <= previous.start:
        raise ValueError(f"The playhead must be after {previous.title} starts")
    if playhead >= current.end:
        raise ValueError(f"The playhead must be before {current.title} ends")
    if playhead > current.start:
        if current.locked:
            raise ValueError(f"The boundary overlaps done set {current.title}. Unlock it first")
        current.start = playhead
    previous.end = playhead
    if selected_index == len(updated) - 1 and video_duration is not None and current.end < video_duration:
        updated.append(SetSegment(current.end, video_duration, "Set 00"))
    return updated


def mark_segment_done(segments: Sequence[SetSegment], index: int, gap_seconds: float = 1.0) -> list[SetSegment]:
    if not 0 <= index < len(segments):
        raise IndexError("Set index is out of range")
    updated = [replace(segment) for segment in segments]
    current = updated[index]
    current.locked = True
    if index + 1 >= len(updated):
        return updated
    following = updated[index + 1]
    new_start = current.end + gap_seconds
    if following.locked:
        if following.start < new_start:
            raise ValueError(f"The next set {following.title} is done and overlaps this boundary")
        return updated
    if new_start >= following.end:
        raise ValueError(f"The next set {following.title} would have no footage left")
    following.start = new_start
    return updated


def remove_segment(segments: Sequence[SetSegment], index: int) -> list[SetSegment]:
    if not 0 <= index < len(segments):
        raise IndexError("Set index is out of range")
    if segments[index].locked:
        raise ValueError(f"{segments[index].title} is done. Unlock it before deleting it")
    updated = [replace(segment) for position, segment in enumerate(segments) if position != index]
    return normalize_default_titles(updated)


def split_segment_at(segments: Sequence[SetSegment], timestamp: float) -> tuple[list[SetSegment], int]:
    updated = [replace(segment) for segment in segments]
    index = next(
        (position for position, segment in enumerate(updated) if segment.start < timestamp < segment.end),
        None,
    )
    if index is None:
        raise ValueError("The playhead must be inside an existing set")
    segment = updated[index]
    if segment.locked:
        raise ValueError(f"{segment.title} is done. Unlock it before splitting it")
    first = replace(segment, end=timestamp)
    second = replace(
        segment,
        start=timestamp,
        title="Set 00",
        confidence=1.0,
        locked=False,
        player_one="",
        player_two="",
        round_name="",
        character_one="",
        skin_one="Default",
        character_two="",
        skin_two="Default",
    )
    updated[index:index + 1] = [first, second]
    return normalize_default_titles(updated), index + 1


def normalize_default_titles(segments: Sequence[SetSegment]) -> list[SetSegment]:
    updated = [replace(segment) for segment in segments]
    for position, segment in enumerate(updated, start=1):
        if re.fullmatch(r"Set \d+", segment.title):
            segment.title = f"Set {position:02d}"
    return updated


def is_default_title(title: str) -> bool:
    return re.fullmatch(r"Set \d+", title) is not None


def safe_filename(value: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("-" if char in invalid else char for char in value).strip(" .")
    return cleaned or "Untitled set"


def export_segment(source: Path, destination: Path, segment: SetSegment, exact: bool = True) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    temporary.unlink(missing_ok=True)
    duration = segment.end - segment.start
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Segment end must be after its start")
    codecs = (
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
        if exact
        else ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    )
    command = [
        resolve_media_tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{segment.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        *codecs,
        "-movflags",
        "+faststart",
        "-y",
        str(temporary),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, creationflags=HIDDEN)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)