"""Carbide Create (.c2d) drawings into sketches.

A .c2d is an SQLite file. Its `items` table holds one row per element (type 'element'),
the element being zlib-compressed JSON. Shapes and paths share one point model: `points`
with a `point_type` each (0 = start of a contour, 1 = line to it, 3 = cubic bezier to it
through `cp1`/`cp2`, 4 = close the contour), all offset by `position`. Text carries its
glyph outlines ready-made in `rendered`, placed by a 3x3 `transform`. Coordinates are
millimetres, Y up, origin at the stock's bottom-left: the same as a sketch on XY.

Straight segments become lines and circles become circles; runs of bezier segments become
splines through points sampled along them. Toolpaths, stock and everything else in the file
are ignored. Construction elements come in as construction geometry.
"""

from __future__ import annotations

import json
import math
import sqlite3
import zlib
from pathlib import Path

from .dxf import _Points
from .sketch import Sketch

SUFFIX = ".c2d"


class FormatError(ValueError):
    pass


def elements(path: str | Path) -> list[dict]:
    """The drawing's elements as JSON objects, in the file's order."""
    if Path(path).stat().st_size == 0:
        raise FormatError(f"{Path(path).name} is empty (0 bytes)")
    try:
        db = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise FormatError(f"{Path(path).name}: {e}") from e
    try:
        rows = db.execute("SELECT data FROM items WHERE type='element' ORDER BY id").fetchall()
    except sqlite3.DatabaseError as e:
        raise FormatError(f"{Path(path).name} is not a Carbide Create file ({e})") from e
    finally:
        db.close()
    out = []
    for (blob,) in rows:
        try:
            obj = json.loads(zlib.decompress(blob))
        except (zlib.error, ValueError, TypeError):
            continue                        # one damaged element should not lose the rest
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _xy(v, origin=(0.0, 0.0)) -> tuple[float, float]:
    return (origin[0] + float(v[0]), origin[1] + float(v[1]))


def _cubic(p0, c1, c2, p1) -> list[tuple[float, float]]:
    """Points along a cubic bezier after p0, ending on p1, about 2 mm apart (4..48 of them)."""
    rough = math.dist(p0, c1) + math.dist(c1, c2) + math.dist(c2, p1)
    n = max(4, min(48, math.ceil(rough / 2.0)))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        out.append((a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
                    a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1]))
    return out


def _contours(obj: dict):
    """Each contour as (start, [(kind, end, cp1, cp2)], closed), absolute mm."""
    if obj.get("geometryType") == "text":
        t = obj.get("transform") or [1, 0, 0, 0, 1, 0, 0, 0, 1]
        if len(t) != 9:
            t = [1, 0, 0, 0, 1, 0, 0, 0, 1]
        for contour in obj.get("rendered") or []:
            pts = [(t[0] * x + t[3] * y + t[6], t[1] * x + t[4] * y + t[7]) for x, y in contour]
            if len(pts) >= 2:
                yield pts[0], [("line", p, None, None) for p in pts[1:]], True
        return
    origin = _xy(obj.get("position") or (0, 0))
    points, kinds = obj.get("points") or [], obj.get("point_type") or []
    cp1, cp2 = obj.get("cp1") or [], obj.get("cp2") or []
    start, segs = None, []
    for i, p in enumerate(points):
        kind = kinds[i] if i < len(kinds) else 1
        p = _xy(p, origin)
        if kind == 0 or start is None:
            if start is not None and segs:
                yield start, segs, False
            start, segs = p, []
        elif kind == 4:
            yield start, segs, True
            start, segs = None, []
        elif kind == 3 and i < len(cp1) and i < len(cp2):
            segs.append(("cubic", p, _xy(cp1[i], origin), _xy(cp2[i], origin)))
        else:
            segs.append(("line", p, None, None))
    if start is not None and segs:
        yield start, segs, False


def import_c2d(path: str | Path, name: str = "imported", plane: str = "XY", offset: float = 0.0) -> Sketch:
    sk = Sketch(name=name, plane=plane, offset=offset)
    pts = _Points(sk, tol=1e-4)
    for obj in elements(path):
        cons = bool(obj.get("construction", False))
        if obj.get("geometryType") == "circle" and obj.get("radius"):
            c = _xy(obj.get("position") or obj.get("center") or (0, 0))
            sk.circle(sk.point(*c, construction=True), float(obj["radius"]), construction=cons)
            continue
        for start, segs, closed in _contours(obj):
            if closed and math.dist(start, segs[-1][1]) > 1e-6:
                segs = segs + [("line", start, None, None)]
            if closed and segs and all(s[0] == "cubic" for s in segs):
                ids, cur = [], start
                for _k, end, c1, c2 in segs:
                    ids += [pts.get(*q) for q in _cubic(cur, c1, c2, end)]
                    cur = end
                ids = ids[:-1]              # the last sample is the start again
                if len(ids) >= 3:
                    sk.spline(ids, closed=True, construction=cons)
                continue
            cur, run = start, []
            for kind, end, c1, c2 in segs:
                if kind == "cubic":
                    if not run:
                        run = [pts.get(*cur)]
                    run += [pts.get(*q) for q in _cubic(cur, c1, c2, end)]
                else:
                    if len(run) >= 2:
                        sk.spline(run, construction=cons)
                    run = []
                    a, b = pts.get(*cur), pts.get(*end)
                    if a != b:
                        sk.line(a, b, construction=cons)
                cur = end
            if len(run) >= 2:
                sk.spline(run, construction=cons)
    return sk
