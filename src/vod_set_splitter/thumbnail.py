from __future__ import annotations

import re
import os
from pathlib import Path
from functools import lru_cache

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .core import SetSegment


WIDTH = 1280
HEIGHT = 720
BACKGROUND = "#0b4b81"
TEXT = "#ffffff"
HEADER_FOOTER = "#031a3a"
SEPARATOR = "#5eb8ef"


def parse_title(title: str) -> tuple[str, str, str]:
    match = re.match(r"\s*(.+?)\s+vs\.?\s+(.+?)(?:\s+-\s+(.+))?\s*$", title, re.IGNORECASE)
    if not match:
        return title.strip() or "Player 1", "Player 2", "Round"
    return match.group(1).strip(), match.group(2).strip(), (match.group(3) or "Round").strip()


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        Path(r"C:\Windows\Fonts\bahnschrift.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\impact.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, starting_size: int) -> ImageFont.FreeTypeFont:
    size = starting_size
    while size > 24:
        font = _font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(size)


def _asset_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=8)
def _asset_files(folder_text: str) -> tuple[Path, ...]:
    folder = Path(folder_text)
    supported = {".png", ".webp", ".jpg", ".jpeg"}
    return tuple(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in supported and "old" not in {part.lower() for part in path.parts}
    )


def list_characters(folder: Path | None) -> list[str]:
    if not folder or not folder.is_dir():
        return []
    return sorted(
        child.name
        for child in folder.iterdir()
        if child.is_dir() and child.name.lower() != "old"
    )


def list_skins(folder: Path | None, character: str) -> list[str]:
    if not folder or not character:
        return ["Default"]
    character_folder = next(
        (child for child in folder.iterdir() if child.is_dir() and _asset_key(child.name) == _asset_key(character)),
        None,
    )
    if not character_folder:
        return ["Default"]
    choices: list[tuple[int, str]] = []
    for path in character_folder.glob("*.png"):
        match = re.search(r"_(Standard|Daisy)_(\d+)_Render$", path.stem, re.IGNORECASE)
        if match:
            index = int(match.group(2))
            label = "Default" if index == 1 and match.group(1).lower() == "standard" else f"{match.group(1).title()} {index}"
            choices.append((index, label))
    return [label for _, label in sorted(set(choices))] or ["Default"]


def find_character_asset(folder: Path | None, character: str, skin: str) -> Path | None:
    if not folder or not folder.is_dir() or not character:
        return None
    character_key = _asset_key(character)
    skin_key = _asset_key(skin or "Default")
    candidates = _asset_files(str(folder.resolve()))
    if skin_key in ("default", "standard", "standard1", "1"):
        preferred_skin_keys = ("standard1render", "standard1")
    elif skin_key.isdigit():
        preferred_skin_keys = (f"standard{skin_key}render", f"{skin_key}render")
    else:
        preferred_skin_keys = (skin_key,)
    exact = [
        path
        for path in candidates
        if character_key in _asset_key(str(path.relative_to(folder)))
        and any(key in _asset_key(path.stem) for key in preferred_skin_keys)
    ]
    if exact:
        return sorted(exact, key=lambda path: len(str(path)))[0]
    character_only = [path for path in candidates if character_key in _asset_key(str(path.relative_to(folder)))]
    return sorted(character_only, key=lambda path: len(str(path)))[0] if character_only else None


def _place_character(canvas: Image.Image, asset: Path | None, box: tuple[int, int, int, int], label: str, mirror: bool) -> None:
    left, top, right, bottom = box
    if asset:
        with Image.open(asset) as source:
            image = source.convert("RGBA")
        alpha_bounds = image.getchannel("A").getbbox()
        if alpha_bounds:
            image = image.crop(alpha_bounds)
        if mirror:
            image = ImageOps.mirror(image)
        target_width = right - left
        target_height = bottom - top
        scale = min(target_width / image.width, target_height / image.height)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        x = left + ((right - left) - image.width) // 2
        y = bottom - image.height
        canvas.alpha_composite(image, (x, y))
        return
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=8, fill="#b83f0c", outline="#ff9d59", width=3)
    font = _fit_font(draw, label or "Choose character", right - left - 30, 42)
    draw.text(((left + right) / 2, (top + bottom) / 2), label or "Choose character", font=font, fill="#ffd8bd", anchor="mm")


@lru_cache(maxsize=4)
def _prepared_background(path: str, _modified: float) -> Image.Image:
    """The background, sized and toned once, then reused for every thumbnail."""
    with Image.open(path) as source:
        background = source.convert("RGB")
    # Stretch intentionally so the forest always reaches both horizontal edges.
    background = background.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    background = ImageEnhance.Color(background).enhance(0.82)
    background = ImageEnhance.Contrast(background).enhance(1.08)
    background = background.filter(ImageFilter.GaussianBlur(0.8))
    return background.convert("RGBA")


def generate_thumbnail(
    segment: SetSegment,
    tournament_name: str,
    destination: Path,
    asset_folder: Path | None = None,
    background_path: Path | None = None,
) -> Path:
    player_one, player_two, parsed_round = parse_title(segment.title)
    player_one = segment.player_one or player_one
    player_two = segment.player_two or player_two
    round_name = segment.round_name or parsed_round

    if background_path and background_path.is_file():
        canvas = _prepared_background(str(background_path), background_path.stat().st_mtime).copy()
    else:
        canvas = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    blue_tint = Image.new("RGBA", (WIDTH, HEIGHT), (6, 48, 103, 92))
    canvas.alpha_composite(blue_tint)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, 96), fill=HEADER_FOOTER)
    draw.rectangle((0, 636, WIDTH, HEIGHT), fill=HEADER_FOOTER)
    draw.line((0, 96, WIDTH, 96), fill=SEPARATOR, width=2)
    draw.line((0, 636, WIDTH, 636), fill=SEPARATOR, width=2)
    draw.line((WIDTH // 2, 96, WIDTH // 2, 636), fill=SEPARATOR, width=3)

    left_asset = find_character_asset(asset_folder, segment.character_one, segment.skin_one)
    right_asset = find_character_asset(asset_folder, segment.character_two, segment.skin_two)
    _place_character(canvas, left_asset, (5, 98, 625, 636), segment.character_one, mirror=False)
    _place_character(canvas, right_asset, (655, 98, 1275, 636), segment.character_two, mirror=True)

    draw = ImageDraw.Draw(canvas)
    name_one_font = _fit_font(draw, player_one.upper(), 540, 54)
    name_two_font = _fit_font(draw, player_two.upper(), 540, 54)
    draw.text((40, 48), player_one.upper(), font=name_one_font, fill=TEXT, anchor="lm", stroke_width=2, stroke_fill="#010d20")
    draw.text((1240, 48), player_two.upper(), font=name_two_font, fill=TEXT, anchor="rm", stroke_width=2, stroke_fill="#010d20")
    draw.text((640, 350), "VS.", font=_font(82), fill=TEXT, anchor="mm", stroke_width=4, stroke_fill="#031d45")

    round_font = _fit_font(draw, round_name.upper(), 570, 40)
    tournament = (tournament_name or "Tournament").upper()
    tournament_font = _fit_font(draw, tournament, 570, 40)
    draw.text((35, 678), round_name.upper(), font=round_font, fill=TEXT, anchor="lm")
    draw.text((1245, 678), tournament, font=tournament_font, fill=TEXT, anchor="rm")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        canvas.convert("RGB").save(temporary, "JPEG", quality=94, optimize=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination