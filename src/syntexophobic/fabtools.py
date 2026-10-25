"""Finishing flat parts for the machine that cuts them out of sheet stock.

A part sliced from a solid is exact geometry, but neither machine cuts exact geometry.
A laser burns away a kerf, so the part comes out smaller by half the kerf on every
edge and slotted parts fall apart; `kerf` grows the outline and shrinks the holes to
make up for it. A router bit is round, so it leaves a fillet in every inside corner and
the mating part will not seat in a slot; `dogbones` cuts a relief bite into each of
those corners. `label` and `label_inside` add single-stroke part numbers so a stack of
parts can be put back together.

A part is a list of rings in mm: ring 0 the outline (counter-clockwise), the rest holes
(clockwise). Polygon work is done with Clipper in integer micrometres, as in cam.py.
"""

from __future__ import annotations

import math

import numpy as np
import pyclipper

Ring = list[tuple[float, float]]

SCALE = 1000          # 1 unit = 1 µm, as in cam.py
MITER_LIMIT = 4.0     # corners sharper than ~29° are squared off instead of spiking out
MIN_LABEL = 2.0       # smallest label height (mm) still worth engraving


# --- clipper helpers --------------------------------------------------------------

def _to_int(rings):
    return [[(round(x * SCALE), round(y * SCALE)) for x, y in r] for r in rings]


def _to_mm(paths):
    return [[(x / SCALE, y / SCALE) for x, y in p] for p in paths]


def _area(ring) -> float:
    """Signed area (mm²), positive for counter-clockwise."""
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        a += x1 * y2 - x2 * y1
    return a / 2


def _oriented(part: list[Ring]) -> list[Ring]:
    """Outline counter-clockwise, holes clockwise, whatever the caller gave; closing
    duplicate points dropped. With this, the material is always left of travel."""
    out = []
    for i, ring in enumerate(part):
        r = [(float(x), float(y)) for x, y in ring]
        if len(r) > 1 and r[0] == r[-1]:
            r = r[:-1]
        if len(r) < 3:
            continue
        if (_area(r) > 0) != (i == 0):
            r.reverse()
        out.append(r)
    return out


def _material(part: list[Ring]):
    """Clipper paths of the part's material (outline minus holes), as a normalised union."""
    pc = pyclipper.Pyclipper()
    pc.AddPaths(_to_int(_oriented(part)), pyclipper.PT_SUBJECT, True)
    return pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)


def _to_part(paths) -> list[Ring]:
    """Clipper result back to one part: the largest outline (CCW) and its holes (CW).
    If an operation split the material, the smaller pieces are dropped."""
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    if not tree.Childs:
        return []
    top = max(tree.Childs, key=lambda n: abs(pyclipper.Area(n.Contour)))
    rings = [top.Contour] + [h.Contour for h in top.Childs]
    return _oriented(_to_mm(rings))


# --- laser kerf -------------------------------------------------------------------

def kerf(part: list[Ring], width: float) -> list[Ring]:
    """Compensate a laser kerf of `width` mm: the outline grows and the holes shrink by
    width/2, so the part is true size once the beam has burnt its line. Mitred joins keep
    corners sharp, so a slot stays square and its mate still slides in. A negative width
    shrinks the part (a press fit that came out loose)."""
    if not width or not part:
        return [list(r) for r in part]
    pco = pyclipper.PyclipperOffset(miter_limit=MITER_LIMIT)
    pco.AddPaths(_material(part), pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    return _to_part(pco.Execute(width / 2 * SCALE))


# --- router dogbones --------------------------------------------------------------

def _circle(centre, r: float, through, step: float = 0.01) -> Ring:
    """Polygon of a circle that has the point `through` as an exact vertex, so the bite
    really reaches the corner. Segments short enough to stay within `step` of the arc."""
    cx, cy = centre
    a0 = math.atan2(through[1] - cy, through[0] - cx)
    n = max(24, math.ceil(math.pi / math.acos(max(-1.0, 1 - step / r))))
    pts = [through] + [(cx + r * math.cos(a0 + 2 * math.pi * i / n),
                        cy + r * math.sin(a0 + 2 * math.pi * i / n)) for i in range(1, n)]
    return pts


def dogbone_centres(part: list[Ring], tool_diameter: float, min_angle_deg: float = 0,
                    max_angle_deg: float = 135, style: str = "dogbone"):
    """(corner, centre) for every bite `dogbones` would cut. An inside corner is one where
    the boundary turns right with the material on the left: a reflex vertex of the
    outline or a convex vertex of a hole. Its angle is the opening angle of the empty
    side (90° in a square slot); only corners within [min, max] get a bite."""
    r = tool_diameter / 2
    out = []
    for ring in _oriented(part):
        n = len(ring)
        for i in range(n):
            p, v, q = np.array(ring[i - 1]), np.array(ring[i]), np.array(ring[(i + 1) % n])
            e1, e2 = v - p, q - v
            l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            d1, d2 = e1 / l1, e2 / l2
            cross = d1[0] * d2[1] - d1[1] * d2[0]
            if cross > -1e-9:                       # left turn or straight: outside corner
                continue
            turn = math.degrees(math.acos(max(-1.0, min(1.0, float(d1 @ d2)))))
            angle = 180.0 - turn                    # opening angle of the empty side
            if not (min_angle_deg - 1e-6 <= angle <= max_angle_deg + 1e-6):
                continue
            if style == "tbone":
                # centre on the longer wall, r from the corner: the bite goes sideways into
                # that wall and leaves the shorter one (a slot bottom) at full depth
                c = v - r * d1 if l1 >= l2 else v + r * d2
            elif style == "dogbone":
                b = d2 - d1                         # bisector, pointing into the empty side
                c = v + r * b / float(np.hypot(*b))
            else:
                raise ValueError(f"Unknown dogbone style {style!r}; use 'dogbone' or 'tbone'")
            out.append(((float(v[0]), float(v[1])), (float(c[0]), float(c[1]))))
    return out


def dogbones(part: list[Ring], tool_diameter: float, min_angle_deg: float = 0,
             max_angle_deg: float = 135, style: str = "dogbone") -> list[Ring]:
    """Cut a relief circle of the tool radius into every inside corner whose angle is in
    range, so a round bit clears the corner and a mating part seats fully. The circle
    passes exactly through the corner point; its centre lies on the corner bisector
    ("dogbone") or on the longer wall ("tbone"). Outside corners are left alone."""
    if not tool_diameter or not part:
        return [list(r) for r in part]
    r = tool_diameter / 2
    bites = [_circle(c, r, v) for v, c in
             dogbone_centres(part, tool_diameter, min_angle_deg, max_angle_deg, style)]
    if not bites:
        return _oriented(part)
    pc = pyclipper.Pyclipper()
    pc.AddPaths(_material(part), pyclipper.PT_SUBJECT, True)
    pc.AddPaths(_to_int(bites), pyclipper.PT_CLIP, True)
    return _to_part(pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO))


# --- labels -----------------------------------------------------------------------

def _bbox(polylines):
    xs = [x for s in polylines for x, _ in s]
    ys = [y for s in polylines for _, y in s]
    return min(xs), min(ys), max(xs), max(ys)


def label(text: str, x: float, y: float, height: float, angle: float = 0) -> list[list[tuple[float, float]]]:
    """Single-stroke engraving of `text`, capital letters `height` mm tall, the lower-left
    of its bounding box at (x, y) and turned `angle` degrees counter-clockwise about it.
    Descenders count as part of the box, so nothing lands below y."""
    from .fonttext import strokes
    lines = strokes(text, height)
    if not lines:
        return []
    x0, y0, _, _ = _bbox(lines)
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [[(x + (px - x0) * c - (py - y0) * s, y + (px - x0) * s + (py - y0) * c)
             for px, py in line] for line in lines]


def _edges(paths_mm):
    """All boundary segments as two (N, 2) arrays of start and end points."""
    a = np.array([p for ring in paths_mm for p in ring], float)
    b = np.array([p for ring in paths_mm for p in ring[1:] + ring[:1]], float)
    return a, b


def _dist_to_edges(pts, a, b):
    """Distance from each point to the nearest boundary segment."""
    ab = b - a
    ll = np.maximum((ab ** 2).sum(1), 1e-12)
    ap = pts[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab[None]).sum(2) / ll[None], 0, 1)
    d = ap - t[..., None] * ab[None]
    return np.sqrt((d ** 2).sum(2)).min(1)


def _inside(region, pts):
    """Which points lie inside the clipper region (holes by even-odd)."""
    ip = _to_int([pts])[0]
    return np.array([sum(pyclipper.PointInPolygon(p, path) != 0 for path in region) % 2 == 1
                     for p in ip])


def _rect_fits(region, rect) -> bool:
    pc = pyclipper.Pyclipper()
    pc.AddPath(_to_int([rect])[0], pyclipper.PT_SUBJECT, True)
    pc.AddPaths(region, pyclipper.PT_CLIP, True)
    left = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return sum(abs(pyclipper.Area(p)) for p in left) < 100      # < 0.0001 mm² sticks out


def label_inside(part: list[Ring], text: str, height: float, margin: float = 1.0) -> list[list[tuple[float, float]]]:
    """Engrave `text` where it fits in the part's material, clear of every edge and hole
    by `margin`. Tries horizontal, then vertical, and steps the height down towards
    MIN_LABEL; [] when even that does not fit. Candidate centres are tried deepest first
    (the pole-of-inaccessibility idea on a grid), so the label sits well inside."""
    if not text.strip() or not part or height <= 0:
        return []
    region = _material(part)
    if margin:
        pco = pyclipper.PyclipperOffset(miter_limit=MITER_LIMIT)
        pco.AddPaths(region, pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
        region = pco.Execute(-margin * SCALE)
    if not region:
        return []
    a, b = _edges(_to_mm(region))
    x0, y0 = a.min(0)
    x1, y1 = a.max(0)

    heights, h = [], height
    while h > MIN_LABEL + 1e-9:
        heights.append(h)
        h *= 0.85
    heights.append(min(height, MIN_LABEL))

    unit = {ang: label(text, 0, 0, 1.0, ang) for ang in (0, 90)}   # lettering scales linearly
    if not unit[0]:
        return []
    for h in heights:
        for angle in (0, 90):
            strokes = [[(px * h, py * h) for px, py in s] for s in unit[angle]]
            bx0, by0, bx1, by1 = _bbox(strokes)
            w, t = bx1 - bx0, by1 - by0
            if w > x1 - x0 or t > y1 - y0:
                continue
            # grid of candidate centres where the box could stand, finer for small labels
            step = max(min(w, t, 5.0) / 2, min(x1 - x0, y1 - y0) / 200, 0.25)
            gx = np.arange(x0 + w / 2, x1 - w / 2 + 1e-9, step)
            gy = np.arange(y0 + t / 2, y1 - t / 2 + 1e-9, step)
            if not len(gx) or not len(gy):
                continue
            pts = np.stack(np.meshgrid(gx, gy), -1).reshape(-1, 2)
            if len(pts) > 6000:                      # keep the search quick on big sheets
                pts = pts[np.random.default_rng(0).choice(len(pts), 6000, replace=False)]
            pts = pts[_inside(region, pts)]
            if not len(pts):
                continue
            depth = _dist_to_edges(pts, a, b)
            need = min(w, t) / 2
            for k in np.argsort(-depth)[:2000]:
                if depth[k] < need:
                    break                            # deeper points are all used up
                cx, cy = pts[k]
                rect = [(cx - w / 2, cy - t / 2), (cx + w / 2, cy - t / 2),
                        (cx + w / 2, cy + t / 2), (cx - w / 2, cy + t / 2)]
                if _rect_fits(region, rect):
                    dx, dy = cx - w / 2 - bx0, cy - t / 2 - by0
                    return [[(px + dx, py + dy) for px, py in s] for s in strokes]
    return []
