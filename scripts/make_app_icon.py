#!/usr/bin/env python3
"""Renders an app icon from scratch via QPainter: ``.icns`` (via ``iconutil``, macOS
only) or ``.ico`` (written directly with the stdlib — PNG-compressed ICO entries, which
every Windows version this project targets supports), chosen by the output suffix.

No external image tools or bundled assets to go stale: a dark rounded-square badge with
a red record dot, drawn at every size the target platform expects. Rerun whenever the
design changes; the .iconset intermediate and the .icns/.ico outputs are all build
artifacts.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, Qt
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


# Standard Windows icon sizes (taskbar/alt-tab/explorer views up to the 256px tile).
_ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def build_ico(output: Path) -> None:
    """Writes a multi-size .ico with PNG-compressed entries using only the stdlib.

    Qt's own ICO writer stores a single image per file, so the ICO container is
    assembled by hand: ICONDIR header, one ICONDIRENTRY per size, then the PNG blobs.
    """
    entries: list[tuple[int, bytes]] = []
    for size in _ICO_SIZES:
        pixmap = render(size)
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not pixmap.save(buffer, "PNG"):
            raise RuntimeError(f"failed to render {size}px PNG for {output}")
        entries.append((size, bytes(buffer.data())))
        buffer.close()

    header = struct.pack("<HHH", 0, 1, len(entries))  # reserved, type=icon, count
    directory = b""
    blobs = b""
    offset = 6 + 16 * len(entries)
    for size, png in entries:
        reported = 0 if size >= 256 else size  # 0 means 256 in ICONDIRENTRY
        directory += struct.pack("<BBBBHHII", reported, reported, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + directory + blobs)


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
        print("usage: make_app_icon.py <output.icns|output.ico>", file=sys.stderr)
        return 2
    app = QApplication.instance() or QApplication([])
    _ = app  # kept alive for the duration of rendering; QPainter needs it to exist

    output = Path(sys.argv[1])
    if output.suffix.lower() == ".ico":
        build_ico(output)
    else:
        build_icns(output)
    print(f"==> built {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
