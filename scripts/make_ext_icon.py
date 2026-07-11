"""Generate vscode-extension/icon.png (same eye mark as icon.ico). Needs Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 256

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=(22, 26, 35, 255))
d.ellipse([38, 78, SIZE - 38, SIZE - 78], fill=(214, 218, 227, 255))
cx, cy = SIZE // 2, SIZE // 2
d.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], fill=(91, 140, 255, 255))
d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=(15, 17, 23, 255))
d.ellipse([cx + 6, cy - 26, cx + 26, cy - 6], fill=(255, 255, 255, 230))

out = ROOT / "vscode-extension" / "icon.png"
img.resize((128, 128), Image.LANCZOS).save(out)
print("wrote", out)
