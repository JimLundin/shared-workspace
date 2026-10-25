"""The approved sketch's vector icons, rendered at native Qt resolution."""

from functools import lru_cache

from .qt import Qt, QtCore, QtGui, QtSvg

_PATHS = {'grid': '<rect x="3" y="3" width="7" height="7" rx="1.2"/><rect x="14" y="3" width="7" height="7" rx="1.2"/><rect x="3" '
         'y="14" width="7" height="7" rx="1.2"/><rect x="14" y="14" width="7" height="7" rx="1.2"/>',
 'canvas': '<rect x="2" y="3" width="8" height="11" rx="1.3"/><rect x="14" y="6" width="8" height="15" rx="1.3"/><path '
           'd="M2 19h8"/>',
 'close': '<path d="m6 6 12 12M6 18 18 6"/>',
 'check': '<path d="m4 12 5 5L20 6"/>',
 'plus': '<path d="M12 4v16M4 12h16"/>',
 'minus': '<path d="M4 12h16"/>',
 'expand': '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
 'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
 'external': '<path d="M14 3h7v7M21 3 10 14M10 4H4v16h16v-6"/>',
 'chevron': '<path d="m9 5 7 7-7 7"/>',
 'down': '<path d="m5 9 7 7 7-7"/>',
 'park': '<path d="M3 17v4h18v-4M12 3v12m-5-5 5 5 5-5"/>',
 'link': '<path d="m10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" '
         'transform="translate(1 -1)"/>',
 'mic': '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M9 22h6"/>',
 'keyboard': '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M5 9h1m3 0h1m3 0h1m3 0h2M5 12h1m3 0h1m3 0h1m3 0h2M7 '
             '16h10"/>',
 'search': '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/>',
 'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 'doc': '<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
 'globe': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
 'table': '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18"/>',
 'chat': '<path d="M3 4h18v13H9l-6 4zM7 8h10M7 12h7"/>',
 'mail': '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m3 6 9 7 9-7"/>',
 'refresh': '<path d="M20 7A9 9 0 1 0 21 14M20 2v6h-6"/>',
 'layers': '<path d="m12 2 10 5-10 5L2 7zm-10 10 10 5 10-5M2 17l10 5 10-5"/>',
 'agent': '<circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3M5 19l3-3M16 8l3-3"/>',
 'wifi': '<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M9 16a5 5 0 0 1 6 0M12 20h.01"/>',
 'battery': '<rect x="2" y="6" width="17" height="12" rx="2"/><path d="M22 10v4M5 9h10v6H5z"/>'}


@lru_cache(maxsize=128)
def icon(name: str, color: str = "#adbdce", size: int = 16) -> QtGui.QIcon:
    body = _PATHS.get(name, _PATHS["doc"])
    source = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
              f'stroke="{color}" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(source.encode()))
    pixmap = QtGui.QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QtGui.QIcon(pixmap)
