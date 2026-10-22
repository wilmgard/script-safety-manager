"""Draw the app icon (a shield with a check mark) and write script_safety/gui/icon.ico.

Run once after changing the design: python packaging/make_icon.py  (needs Pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "script_safety" / "gui" / "icon.ico"
BLUE, WHITE = (31, 143, 214, 255), (255, 255, 255, 255)


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    shield = [(128, 14), (226, 50), (218, 140), (180, 206), (128, 244), (76, 206), (38, 140), (30, 50)]
    d.polygon([(x * s, y * s) for x, y in shield], fill=BLUE)
    d.line([(78 * s, 132 * s), (114 * s, 170 * s), (182 * s, 92 * s)], fill=WHITE, width=round(26 * s),
           joint="curve")
    return img


if __name__ == "__main__":
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")
