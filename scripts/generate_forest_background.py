from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


WIDTH = 2560
HEIGHT = 1440
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "blue-birch-forest.png"


def trunk_polygon(center: int, top_width: int, bottom_width: int, lean: int) -> list[tuple[int, int]]:
    return [
        (center - top_width // 2, -40),
        (center + top_width // 2, -40),
        (center + lean + bottom_width // 2, HEIGHT + 40),
        (center + lean - bottom_width // 2, HEIGHT + 40),
    ]


def draw_forest() -> Image.Image:
    random.seed(161)
    image = Image.new("RGB", (WIDTH, HEIGHT), "#123aa8")
    draw = ImageDraw.Draw(image)

    # Distant trunks add depth while keeping the center readable.
    for center in range(-100, WIDTH + 160, 260):
        width = random.randint(55, 85)
        lean = random.randint(-100, 100)
        draw.polygon(trunk_polygon(center, width, width + 35, lean), fill="#0d318e")

    positions = [-60, 280, 650, 1040, 1510, 1880, 2250, 2510]
    for tree_index, center in enumerate(positions):
        top_width = random.randint(75, 115)
        bottom_width = top_width + random.randint(25, 60)
        lean = random.randint(-130, 130)
        color = "#06235f" if tree_index % 3 else "#08286f"
        polygon = trunk_polygon(center, top_width, bottom_width, lean)
        draw.polygon(polygon, fill=color)

        # Birch-like horizontal notches.
        for y in range(random.randint(70, 145), HEIGHT, random.randint(105, 145)):
            progress = y / HEIGHT
            x = center + round(lean * progress)
            half_width = round((top_width + (bottom_width - top_width) * progress) / 2)
            notch_width = random.randint(18, 45)
            notch_height = random.randint(8, 15)
            side = -1 if random.random() < 0.5 else 1
            if side < 0:
                points = [(x - half_width, y), (x - half_width + notch_width, y + 4), (x - half_width, y + notch_height)]
            else:
                points = [(x + half_width, y), (x + half_width - notch_width, y + 4), (x + half_width, y + notch_height)]
            draw.polygon(points, fill="#1644b4")

        # Sparse angular branches, mostly toward the outside.
        for branch_number in range(2):
            y = random.randint(170, 930)
            progress = y / HEIGHT
            x = center + round(lean * progress)
            direction = -1 if center < WIDTH // 2 else 1
            if branch_number == 1:
                direction *= -1
            length = random.randint(120, 270)
            tip_y = y - random.randint(90, 190)
            width = random.randint(22, 38)
            draw.polygon(
                [(x, y), (x + direction * length, tip_y), (x + direction * (length + 15), tip_y + width), (x, y + width)],
                fill=color,
            )

    # Subtle texture prevents compression banding while retaining the flat poster look.
    noise = Image.new("RGBA", image.size, (0, 0, 0, 0))
    noise_draw = ImageDraw.Draw(noise)
    for _ in range(9000):
        x = random.randrange(WIDTH)
        y = random.randrange(HEIGHT)
        shade = random.choice(((255, 255, 255, 4), (0, 0, 0, 4)))
        noise_draw.point((x, y), fill=shade)
    image = Image.alpha_composite(image.convert("RGBA"), noise).convert("RGB")
    return image.filter(ImageFilter.GaussianBlur(0.18)).resize((1280, 720), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    draw_forest().save(OUTPUT, "PNG", optimize=True)
    print(OUTPUT)