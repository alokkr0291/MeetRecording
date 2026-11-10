#!/usr/bin/env python3
"""Renders an .icns app icon from scratch via QPainter, then ``iconutil``.

No external image tools or bundled assets to go stale: a dark rounded-square badge with
a red record dot, drawn at every size macOS expects. Rerun whenever the design changes;
the .iconset intermediate and the .icns output are both build artifacts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

# size -> one or more .iconset filenames it covers (macOS reuses the same pixels for a
# base size and its @2x variant when we do not need separate artwork).
_ICONSET_FILES: dict[int, list[str]] = {
    16: ["icon_16x16.png"],
    32: ["icon_16x16@2x.png", "icon_32x32.png"],
    64: ["icon_32x32@2x.png"],
    128: ["icon_128x128.png"],
    256: ["icon_128x128@2x.png", "icon_256x256.png"],
    512: ["icon_256x256@2x.png", "icon_512x512.png"],
    1024: ["icon_512x512@2x.png"],
}


def render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    painter.setBrush(QColor(28, 28, 30))
    radius = size * 0.22
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    painter.setBrush(QColor(255, 59, 48))
    margin = size * 0.26
    diameter = size - 2 * margin
    painter.drawEllipse(int(margin), int(margin), int(diameter), int(diameter))

    painter.end()
    return pixmap


def build_icns(output: Path) -> None:
    iconset = output.with_suffix(".iconset")
    iconset.mkdir(parents=True, exist_ok=True)
    for size, names in _ICONSET_FILES.items():
        pixmap = render(size)
        for name in names:
            pixmap.save(str(iconset / name), "PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)], check=True)
    shutil.rmtree(iconset)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_app_icon.py <output.icns>", file=sys.stderr)
        return 2
    app = QApplication.instance() or QApplication([])
    _ = app  # kept alive for the duration of rendering; QPainter needs it to exist

    output = Path(sys.argv[1])
    build_icns(output)
    print(f"==> built {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
