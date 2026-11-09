from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

from .core import SetSegment, resolve_media_tool


TESSERACT_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
)
TESSERACT_PATH = ""  # chosen in Settings; found automatically when empty
SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass(frozen=True)
class DetectedTitle:
    title: str | None
    player_one: str | None
    player_two: str | None
    round_name: str | None
    confidence: float


def resolve_tesseract() -> Path:
    if TESSERACT_PATH and Path(TESSERACT_PATH).is_file():
        return Path(TESSERACT_PATH)
    found = shutil.which("tesseract")
    if found:
        return Path(found)
    for candidate in TESSERACT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Tesseract isn't installed, so set names can't be read. Choose it in Settings.")


def clean_ocr_text(value: str) -> str | None:
    words = re.findall(r"[A-Z0-9][A-Z0-9_' -]*", value.upper())
    cleaned = " ".join(words).strip(" -_")
    return cleaned if len(cleaned) >= 2 else None


def normalize_round(value: str) -> str | None:
    cleaned = clean_ocr_text(value)
    if not cleaned:
        return None
    cleaned = re.sub(r"\bWINNERS\s+BANALS\b", "WINNERS FINALS", cleaned)
    cleaned = re.sub(r"\bGRANDS?\s+RESET\b", "GRAND FINALS RESET", cleaned)
    cleaned = re.sub(r"\bGRANDS?\s+FINALS?\b", "GRAND FINALS", cleaned)
    cleaned = re.sub(r"\bLOSS+E+R+S*\b", "LOSERS", cleaned)
    tokens = cleaned.split()
    vocabulary = {
        "WINNERS": "Winners",
        "LOSERS": "Losers",
        "GRAND": "Grand",
        "ROUND": "Round",
        "QUARTERS": "Quarters",
        "QUARTERFINALS": "Quarterfinals",
        "SEMIS": "Semis",
        "SEMIFINALS": "Semifinals",
        "FINAL": "Final",
        "FINALS": "Finals",
        "RESET": "Reset",
        "POOL": "Pool",
        "POOLS": "Pools",
        "TOP": "Top",
    }

    def canonical(token: str, choices: tuple[str, ...]) -> str | None:
        if token in choices:
            return vocabulary[token]
        match = get_close_matches(token, choices, n=1, cutoff=0.72)
        return vocabulary[match[0]] if match else None

    for index, token in enumerate(tokens):
        side = canonical(token, ("WINNERS", "LOSERS", "GRAND"))
        if not side:
            continue
        tail = tokens[index + 1:index + 4]
        if not tail:
            continue
        stage = canonical(
            tail[0],
            ("ROUND", "QUARTERS", "QUARTERFINALS", "SEMIS", "SEMIFINALS", "FINAL", "FINALS"),
        )
        if not stage:
            continue
        if stage == "Round" and len(tail) > 1:
            number = re.sub(r"\D", "", tail[1])
            if number:
                return f"{side} Round {number}"
        if side == "Grand" and stage in ("Final", "Finals"):
            reset = canonical(tail[1], ("RESET",)) if len(tail) > 1 else None
            return f"Grand Finals{' Reset' if reset else ''}"
        return f"{side} {stage}"

    top_match = re.search(r"TOP\s*(8|16|32|64)", cleaned)
    if top_match:
        return f"Top {top_match.group(1)}"
    pool_match = re.search(r"POOLS?\s*([A-Z0-9]+)?", cleaned)
    if pool_match:
        suffix = f" {pool_match.group(1)}" if pool_match.group(1) else ""
        return f"Pool{suffix}"
    return None


def parse_scoreboard_text(value: str) -> tuple[str | None, str | None, str | None]:
    lines = [clean_ocr_text(line) for line in value.splitlines()]
    round_name = normalize_round(value)
    excluded = ("WINNERS", "LOSERS", "GRAND", "ROUND", "BEST", " OF ")
    names = [
        line
        for line in lines
        if line and not any(token in line for token in excluded) and not line.isdigit()
    ]
    if not round_name:
        round_lines = [
            line
            for line in lines[2:]
            if line and "BEST" not in line and not re.fullmatch(r"\d+", line)
        ]
        if round_lines:
            fallback = re.sub(r"\bLOSS+E+R+S*\b", "LOSERS", round_lines[0])
            letters = re.sub(r"[^A-Z]", "", fallback)
            word_count = len(fallback.split())
            if fallback == "LOSERS" or (len(letters) >= 8 and word_count >= 2):
                round_name = fallback.title()
    return (
        names[0] if names else None,
        names[1] if len(names) > 1 else None,
        round_name,
    )


def _ocr_scoreboard(source: Path, timestamp: float) -> tuple[str | None, str | None, str | None]:
    ffmpeg = resolve_media_tool("ffmpeg")
    tesseract = resolve_tesseract()
    filters = (
        "split=3[a][b][c];"
        "[a]crop=iw*0.115:ih*0.065:iw*0.68:ih*0.55,scale=1000:220,format=gray,"
        "lut=y='if(gt(val,160),255,0)'[left];"
        "[b]crop=iw*0.105:ih*0.11:iw*0.89:ih*0.63,scale=1000:220,format=gray,"
        "eq=contrast=2[right];"
        "[c]crop=iw*0.32:ih*0.22:iw*0.68:ih*0.31,scale=1000:300,format=gray,"
        "eq=contrast=1.5[round];"
        "[left][right][round]vstack=inputs=3"
    )
    frame = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
            "-i", str(source), "-frames:v", "1", "-vf", filters, "-f", "image2pipe", "-vcodec", "png", "-",
        ],
        check=True,
        capture_output=True,
        creationflags=SUBPROCESS_FLAGS,
    ).stdout
    text = subprocess.run(
        [str(tesseract), "stdin", "stdout", "--psm", "6"],
        input=frame,
        check=True,
        capture_output=True,
        creationflags=SUBPROCESS_FLAGS,
        text=False,
    ).stdout.decode(errors="replace")
    return parse_scoreboard_text(text)


def _vote(values: list[str | None]) -> tuple[str | None, float]:
    valid = [value for value in values if value]
    if not valid:
        return None, 0.0
    value, count = Counter(valid).most_common(1)[0]
    return value, count / len(values)


def detect_set_title(source: Path, segment: SetSegment) -> DetectedTitle:
    offsets = (30.0, 15.0, 5.0)
    timestamps = [max(segment.start, segment.end - offset) for offset in offsets]
    left_values: list[str | None] = []
    right_values: list[str | None] = []
    round_values: list[str | None] = []

    for timestamp in timestamps:
        player_one, player_two, round_name = _ocr_scoreboard(source, timestamp)
        left_values.append(player_one)
        right_values.append(player_two)
        round_values.append(round_name)

    player_one, left_confidence = _vote(left_values)
    player_two, right_confidence = _vote(right_values)
    round_name, round_confidence = _vote(round_values)
    parts: list[str] = []
    if player_one and player_two:
        parts.append(f"{player_one.title()} vs {player_two.title()}")
    if round_name:
        parts.append(round_name)
    title = " - ".join(parts) if parts else None
    confidence_values = [value for value in (left_confidence, right_confidence, round_confidence) if value > 0]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return DetectedTitle(title, player_one, player_two, round_name, confidence)