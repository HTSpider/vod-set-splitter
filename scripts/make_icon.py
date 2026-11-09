"""Draws assets/icon.ico: a timeline cut into sets, the last one done."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(image)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=(28, 31, 36, 255))
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, outline=(58, 65, 80, 255), width=14)
# three sets on a timeline: two gold, one green (done)
y0, y1 = 430, 594
for x0, x1, colour in ((170, 400, (232, 184, 74)), (440, 640, (232, 184, 74)), (680, 854, (126, 226, 160))):
    d.rounded_rectangle((x0, y0, x1, y1), radius=40, fill=colour + (255,))
# playhead
d.rounded_rectangle((506, 300, 530, 724), radius=12, fill=(242, 244, 245, 255))
d.polygon([(470, 270), (566, 270), (518, 330)], fill=(242, 244, 245, 255))
out = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
image.resize((256, 256), Image.LANCZOS).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
image.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
image.resize((128, 128), Image.LANCZOS).save(out.parents[1] / "src" / "vod_set_splitter" / "ui" / "icon.png")
print("wrote", out)
