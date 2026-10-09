"""Text as outlines: any installed font through Qt's font engine, flattened to polygons."""

from __future__ import annotations

import numpy as np


def _app():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:                      # no window (tests, command line): render off-screen
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QGuiApplication([])
    elif not isinstance(app, QGuiApplication):
        # a console-only Qt application exists; fonts need a GUI one and Qt allows only one
        raise RuntimeError("Text needs a Qt GUI application (QGuiApplication), not QCoreApplication")
    return app


def outlines(text: str, height: float, font: str = "DejaVu Sans", tol: float = 0.01):
    """Closed polygons (lists of (x, y) in mm) of `text`, baseline at y = 0, starting at
    x = 0, capital letters about `height` tall."""
    from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath
    from .surface import _keep
    _app()
    f = QFont(font)
    f.setPointSizeF(100.0)
    cap = QFontMetricsF(f).capHeight() or 70.0
    path = QPainterPath()
    path.addText(0, 0, f, text)
    scale = height / cap
    polys = []
    for poly in path.toSubpathPolygons():
        pts = np.array([(p.x() * scale, -p.y() * scale, 0.0) for p in poly])   # Qt's y points down
        if len(pts) < 4:
            continue
        if np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) >= 3:
            keep = _keep(np.vstack([pts, pts[:1]]), tol)[:-1]
            polys.append([(float(x), float(y)) for x, y, _ in pts[keep]])
    return [p for p in polys if len(p) >= 3]
