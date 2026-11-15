"""Render the Windows application icon (packaging/AppIcon.ico) from the same drawing as the macOS icon."""
import sys
from pathlib import Path

import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import render  # noqa: E402

OUT = Path(__file__).resolve().parent / "AppIcon.ico"


def main() -> int:
    big = render(256)                                   # BGRA
    img = Image.fromarray(cv2.cvtColor(big, cv2.COLOR_BGRA2RGBA))
    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
