"""Generate icon.ico (a simple eye mark) using PIL. Run with a Python that has Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 256

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded dark tile
d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=(22, 26, 35, 255))
# eye outline (almond = two arcs approximated with an ellipse clipped by the tile)
d.ellipse([38, 78, SIZE - 38, SIZE - 78], fill=(214, 218, 227, 255))
# iris + pupil + glint
cx, cy = SIZE // 2, SIZE // 2
d.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], fill=(91, 140, 255, 255))
d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=(15, 17, 23, 255))
d.ellipse([cx + 6, cy - 26, cx + 26, cy - 6], fill=(255, 255, 255, 230))

img.save(ROOT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", ROOT / "icon.ico")
