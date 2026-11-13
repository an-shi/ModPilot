#!/usr/bin/env python
"""Generate the ModPilot application icon (ICO with PNG frames)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PySide6.QtSvg import QSvgRenderer


def render(size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#2563EB"))
    gradient.setColorAt(0.55, QColor("#5B9DFF"))
    gradient.setColorAt(1.0, QColor("#38D6B0"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.02 * size, 0.02 * size, 0.96 * size, 0.96 * size), 0.24 * size, 0.24 * size)
    painter.fillPath(path, gradient)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="m12 3 1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z"/>'
        '<path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    icon_size = size * 0.62
    renderer.render(painter, QRectF((size - icon_size) / 2, (size - icon_size) / 2, icon_size, icon_size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def main() -> int:
    QGuiApplication(sys.argv[:1])
    root = Path(__file__).resolve().parents[1]
    resources = root / "resources"
    resources.mkdir(exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [(size, render(size)) for size in sizes]
    png = render(512)
    (resources / "modpilot.png").write_bytes(png)

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    for size, data in frames:
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    (resources / "modpilot.ico").write_bytes(header + bytes(entries) + bytes(payload))
    print(f"Wrote {resources / 'modpilot.ico'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
