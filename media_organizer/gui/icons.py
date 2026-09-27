"""SVG icon library for Media Organizer.

Clean line-style icons (Lucide/Feather-inspired), 24x24 viewBox, stroke-based.
Each icon is a path fragment using `currentColor`; the helpers recolor and
rasterize it via QtSvg at 2x for HiDPI crispness. No external assets.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_WRAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

# name -> inner SVG markup (paths/shapes only)
_ICONS: dict[str, str] = {
    # brand
    "diamond": '<path d="M2.7 10.3a2.4 2.4 0 0 0 0 3.4l7.6 7.6a2.4 2.4 0 0 0 '
               '3.4 0l7.6-7.6a2.4 2.4 0 0 0 0-3.4l-7.6-7.6a2.4 2.4 0 0 0-3.4 0Z"/>',
    "sparkles": '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 '
                '1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 '
                '1-1.3-1.3Z"/>',
    # files / media
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 '
              '3h9a2 2 0 0 1 2 2z"/>',
    "folder-open": '<path d="M5 21a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 '
                   '1 2 2v2"/><path d="M2.4 21 5 10.8A1 1 0 0 1 6 10h15.4a1 1 0 0 '
                   '1 .96 1.27l-2.5 9A1 1 0 0 1 18.9 21Z"/>',
    "folder-input": '<path d="M2 9V5a2 2 0 0 1 2-2h4l2 3h9a2 2 0 0 1 2 2v9a2 2 0 '
                    '0 1-2 2H4a2 2 0 0 1-2-2v-3"/><path d="M2 15h9"/><path d="m8 '
                    '11 4 4-4 4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" '
             'cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    "film": '<rect x="2" y="2" width="20" height="20" rx="2.2"/><path d="M7 2v20"/>'
            '<path d="M17 2v20"/><path d="M2 12h20"/><path d="M2 7h5"/>'
            '<path d="M2 17h5"/><path d="M17 17h5"/><path d="M17 7h5"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 '
              '0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 '
                 '2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/>'
                 '<path d="M16 17H8"/><path d="M10 9H8"/>',
    # structure / strategy
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/>'
                '<path d="M8 2v4"/><path d="M3 10h18"/>',
    "calendar-range": '<rect x="3" y="4" width="18" height="18" rx="2"/>'
                      '<path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>'
                      '<path d="M17 14h-6"/><path d="M13 18H7"/>',
    "layers": '<path d="m12.83 2.18 8.42 4.63a1 1 0 0 1 0 1.78l-8.42 4.63a2 2 0 0 '
              '1-1.66 0L2.75 8.59a1 1 0 0 1 0-1.78l8.42-4.63a2 2 0 0 1 1.66 0Z"/>'
              '<path d="m3 12.5 8.42 4.63a2 2 0 0 0 1.66 0L21.5 12.5"/>'
              '<path d="m3 17 8.42 4.63a2 2 0 0 0 1.66 0L21.5 17"/>',
    "tree": '<path d="M21 12h-8"/><path d="M21 6H8"/><path d="M21 18h-8"/>'
            '<path d="M3 6v4c0 1.1.9 2 2 2h3"/><path d="M3 10v6c0 1.1.9 2 2 2h3"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>'
            '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    # places
    "map-pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>'
               '<circle cx="12" cy="10" r="3"/>',
    "map-pin-calendar": '<path d="M20 10.5c0 5.5-8 11.5-8 11.5S4 16 4 10.5a8 8 0 0 '
                        '1 16 0Z"/><rect x="9" y="7.5" width="6" height="5.5" '
                        'rx="1"/><path d="M10.5 6v2"/><path d="M13.5 6v2"/>',
    # actions
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 '
            '0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
            '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
            '<circle cx="12" cy="12" r="3.5"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m8.5 12.2 2.4 2.4 '
                    '4.8-5.2"/>',
    "undo": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "external-link": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 '
                     '2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/>',
    "x": '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
    "arrow-left": '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    # chrome / meta
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "filter": '<polygon points="22 3 2 3 10 12.5 10 19 14 21 14 12.5 22 3"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "alert-triangle": '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 '
                      '21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4"/>'
                      '<path d="M12 17h.01"/>',
}

#: strategy key -> icon name (used by the Step 2 cards)
STRATEGY_ICONS = {
    "year_only": "calendar",
    "year_month": "calendar-range",
    "year_quarter": "layers",
    "year_quarter_month": "tree",
    "monthly_flat": "list",
    "location": "map-pin",
    "location_year": "map-pin-calendar",
}

#: DateSource value -> icon name (used by the "found via" table column)
SOURCE_ICONS = {
    "exif": "camera",
    "png_text": "file-text",
    "video": "film",
    "filename": "file-text",
    "mtime": "clock",
    "none": "alert-triangle",
}


def available_icons() -> tuple[str, ...]:
    return tuple(_ICONS)


def pixmap(name: str, color: str, size: int = 16, stroke: float = 2.0) -> QPixmap:
    """Rasterize an icon to a transparent pixmap (2x for HiDPI)."""
    return _render(name, color, size, stroke)


@lru_cache(maxsize=512)
def _render(name: str, color: str, size: int, stroke: float) -> QPixmap:
    if name not in _ICONS:
        raise KeyError(f"unknown icon: {name}")
    svg = _SVG_WRAP.format(stroke=stroke, body=_ICONS[name]).replace(
        "currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    # explicit target rect: without it QtSvg maps the viewBox to the
    # painter viewport, which double-scales once the pixmap has a DPR
    renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    pm.setDevicePixelRatio(2)
    return pm


def icon(name: str, color: str, size: int = 16, stroke: float = 2.0) -> QIcon:
    """Recolored icon ready for buttons, actions and table items."""
    return QIcon(pixmap(name, color, size, stroke))
