from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "gamepad": '<path d="M7.5 8h9a4 4 0 0 1 3.9 4.9l-.5 2.5a2.6 2.6 0 0 1-4.5 1.2L14.4 15H9.6l-1.5 1.6a2.6 2.6 0 0 1-4.5-1.2l-.5-2.5A4 4 0 0 1 7.5 8Z"/><path d="M7 11.2v2.6M5.7 12.5h2.6"/><circle cx="16.5" cy="11.6" r=".9"/><circle cx="17.8" cy="13.6" r=".9"/>',
    "mods": '<rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><path d="M17 13v8M13 17h8"/>',
    "download": '<path d="M12 3v11m0 0 4-4m-4 4-4-4"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "update": '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
    "translate": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/>',
    "diagnose": '<path d="M3 12h4l2-6 4 12 2.5-7H21"/>',
    "profiles": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
    "settings": '<path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h8M16 18h4"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="14" cy="18" r="2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/>',
    "sun": '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a7 7 0 1 0 10.7 10.7Z"/>',
    "refresh": '<path d="M20 6v5h-5"/><path d="M19.2 15a7.5 7.5 0 1 1-.6-7.6L20 11"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
    "trash": '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13M10 11v5M14 11v5"/>',
    "play": '<path d="m8 5 11 7-11 7V5Z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "check": '<path d="m4 12 5 5 11-11"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "shield": '<path d="M12 3 5 6v6c0 4.4 3 8.2 7 9 4-.8 7-4.6 7-9V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>',
    "package": '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "sparkles": '<path d="m12 3 1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    "alert": '<path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
    "chevron": '<path d="m9 6 6 6-6 6"/>',
    "save": '<path d="M5 3h11l4 4v14H5V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/>',
    "backup": '<path d="M4 8a8 8 0 1 1 1.5 8.5"/><path d="M4 3v5h5"/><circle cx="12" cy="12" r="2.5"/>',
    "edit": '<path d="M4 20h4L20 8l-4-4L4 16v4Z"/><path d="m13 6 5 5"/>',
    "filter": '<path d="M4 5h16l-6 7v7l-4-2v-5L4 5Z"/>',
    "open": '<path d="M4 5h7M4 5v14h14v-7M14 4h6v6M20 4l-9 9"/>',
}


def _svg_bytes(name: str, color: str) -> bytes:
    body = _SVG.get(name, _SVG["info"])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    return svg.encode("utf-8")


@lru_cache(maxsize=512)
def render_pixmap(name: str, color: str = "#E6EAF2", size: int = 20) -> QPixmap:
    # Retina-friendly rendering.
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(_svg_bytes(name, color)))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def icon(name: str, color: str = "#E6EAF2", size: int = 20) -> QIcon:
    return QIcon(render_pixmap(name, color, size))
