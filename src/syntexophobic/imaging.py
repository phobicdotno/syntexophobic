"""Images into geometry: trace a picture into sketch outlines, or turn its brightness into
a relief surface.

Images are read with Qt (PNG, JPG, BMP, GIF, WebP...). Row 0 of an image is its top, so
y is flipped to make the picture upright in the sketch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def load_gray(path: str | Path, max_size: int | None = None) -> np.ndarray:
    """Brightness 0 (black) .. 1 (white), rows top to bottom; transparent pixels count as
    white (background). Optionally scaled so the longer side is at most max_size."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        raise ValueError(f"Could not read the image {path}")
    if max_size and max(img.width(), img.height()) > max_size:
        img = img.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    rgba = arr[:, :w * 4].reshape(h, w, 4).astype(float) / 255.0
    gray = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
    alpha = rgba[..., 3]
    return gray * alpha + (1 - alpha)           # composite over white


def trace(path: str | Path, width: float, threshold: float = 0.5, invert: bool = False,
          smooth: float = 0.8, max_size: int = 600) -> list[list[tuple[float, float]]]:
    """Outlines (closed polygons in mm, y up, starting at the origin) of the dark parts of
    an image (light parts with invert), scaled to `width` mm. Holes come out as separate
    loops inside their outline, as sketches expect."""
    import pyclipper
    from .surface import _keep
    g = load_gray(path, max_size)
    mask = (g > threshold) if invert else (g < threshold)
    h, w = mask.shape
    scale = width / w
    rows = []
    for j in range(h):                                  # runs of set pixels, row by row
        row = mask[j].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], row, [0]])))
        y0, y1 = (h - j - 1), (h - j)
        for a, b in zip(edges[::2], edges[1::2]):
            rows.append([(a, y0), (b, y0), (b, y1), (a, y1)])
    if not rows:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(rows, pyclipper.PT_SUBJECT, True)
    merged = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    out = []
    for poly in merged:
        if abs(pyclipper.Area(poly)) < 4:               # specks of a pixel or two
            continue
        P = np.array([(x, y, 0.0) for x, y in poly] + [(poly[0][0], poly[0][1], 0.0)], float)
        keep = _keep(P, smooth)[:-1]                     # pixel staircase -> straight runs
        pts = [(float(x) * scale, float(y) * scale) for x, y, _ in P[:-1][keep]]
        if len(pts) >= 3:
            out.append(pts)
    return out


def relief_grid(path: str | Path, width: float, depth: float, invert: bool = False,
                cells: int = 120) -> tuple[np.ndarray, float]:
    """Heights (mm, rows from the front/bottom of the picture) for a relief `width` mm wide:
    white is high, black low (or the other way with invert), spanning `depth` mm. Returns
    (heights, cell size in mm)."""
    g = load_gray(path, cells)
    if invert:
        g = 1 - g
    # a light blur: hard pixel edges would make the fitted surface ripple and overshoot
    k = np.array([1.0, 2.0, 1.0]) / 4.0
    for axis in (0, 1):
        pad = np.pad(g, [(1, 1) if a == axis else (0, 0) for a in (0, 1)], mode="edge")
        g = sum(k[i] * np.take(pad, range(i, i + g.shape[axis]), axis=axis) for i in range(3))
    h, w = g.shape
    return depth * g[::-1, :], width / max(w - 1, 1)
