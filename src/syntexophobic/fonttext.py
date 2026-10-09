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


STROKE_FONTS = ("futural", "futuram", "scripts", "scriptc", "cursive", "timesr", "rowmans", "rowmand", "gothiceng")


def strokes(text: str, height: float, font: str = "futural") -> list[list[tuple[float, float]]]:
    """Single-line (Hershey) lettering for engraving: open polylines in mm, baseline at
    y = 0 from x = 0, capital letters `height` tall. Each stroke is cut once, where outline
    text would cut both sides of every letter."""
    from HersheyFonts import HersheyFonts
    if font not in STROKE_FONTS:
        raise ValueError(f"Unknown single-line font {font!r}; fonts: {', '.join(STROKE_FONTS)}")
    hf = HersheyFonts()
    hf.load_default_font(font)
    hf.normalize_rendering(100)
    ys = [y for seg in hf.lines_for_text("H") for _, y in seg]
    base, cap = min(ys), max(ys)
    k = height / (cap - base)
    out: list[list[tuple[float, float]]] = []
    for (x1, y1), (x2, y2) in hf.lines_for_text(text):
        a, b = (x1 * k, (y1 - base) * k), (x2 * k, (y2 - base) * k)
        if out and abs(out[-1][-1][0] - a[0]) < 1e-9 and abs(out[-1][-1][1] - a[1]) < 1e-9:
            out[-1].append(b)                       # the stroke goes on
        else:
            out.append([a, b])
    if out:
        x0 = min(x for s in out for x, _ in s)
        out = [[(x - x0, y) for x, y in s] for s in out]
    return out
