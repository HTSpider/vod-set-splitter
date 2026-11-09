from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .core import (
    ScanProfile,
    SetSegment,
    boundaries_to_segments,
    detect_boundaries,
    export_segment,
    format_time,
    probe_video,
    safe_filename,
)


def scan(source: Path, output: Path, max_duration: float | None) -> None:
    info = probe_video(source)
    profile = ScanProfile()
    print(f"Scanning {source.name} ({format_time(info.duration)})")
    boundaries = detect_boundaries(
        info,
        profile,
        lambda value: print(f"\rProgress: {value:6.1%}", end="", flush=True),
        max_duration=max_duration,
    )
    duration = min(info.duration, max_duration) if max_duration is not None else info.duration
    segments = boundaries_to_segments(boundaries, duration)
    payload = {
        "source": str(source.resolve()),
        "profile": asdict(profile),
        "sets": [asdict(segment) for segment in segments],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nFound {len(segments)} candidate segments:")
    for segment in segments:
        print(f"  {format_time(segment.start)} - {format_time(segment.end)}  {segment.title}")
    print(f"Saved {output}")


def export(project_file: Path, output_folder: Path, exact: bool) -> None:
    payload = json.loads(project_file.read_text(encoding="utf-8"))
    source = Path(payload["source"])
    segments = [SetSegment(**item) for item in payload["sets"]]
    output_folder.mkdir(parents=True, exist_ok=True)
    for index, segment in enumerate(segments, start=1):
        destination = output_folder / f"{index:02d} - {safe_filename(segment.title)}.mp4"
        print(f"Exporting {destination.name}")
        export_segment(source, destination, segment, exact=exact)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect and export sets from tournament VODs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan", help="Detect candidate set boundaries")
    scan_parser.add_argument("source", type=Path)
    scan_parser.add_argument("--output", type=Path, default=Path("sets.json"))
    scan_parser.add_argument("--max-duration", type=float)

    export_parser = subparsers.add_parser("export", help="Export a reviewed project")
    export_parser.add_argument("project", type=Path)
    export_parser.add_argument("output_folder", type=Path)
    export_parser.add_argument("--fast-copy", action="store_true", help="Copy at nearby keyframes instead of encoding exact cuts")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command == "scan":
        scan(arguments.source, arguments.output, arguments.max_duration)
    else:
        export(arguments.project, arguments.output_folder, exact=not arguments.fast_copy)


if __name__ == "__main__":
    main()