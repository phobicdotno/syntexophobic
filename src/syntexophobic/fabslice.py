"""Slice for fabrication: cut one solid into flat parts to laser or CNC from sheet wood or
acrylic, and add what holds them together again.

Five ways to build the model back up:

* stacked  horizontal layers one sheet thick, glued on top of each other. Each layer has
           the outline of the layer above engraved on it, and optional dowel holes that
           line up through the whole stack.
* rod      slices threaded on a rod (or pipe) through all of them, with spacer rings cut
           from the same sheet to set the gap between slices.
* base     upright slices with tabs that sit in slots in a base plate.
* back     upright slices with tabs that sit in slots in a back plate (wall mounted).
* waffle   two sets of upright slices at right angles, slotted half way so they slide
           together.

Every part comes out as flat rings (outline first, then holes) in its own 2D frame, plus
the 3D frame it sits in, so the same result feeds the sheet export, the nesting and the
3D preview of the assembled model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pyclipper
from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Pnt

from . import geometry

Point = tuple[float, float]
Ring = list[Point]
Vec = tuple[float, float, float]

SCALE = 1000          # clipper works in integers: 1 unit = 1 µm
STYLES = ("stacked", "rod", "base", "back", "waffle")
STYLE_NAMES = {"stacked": "Stacked layers", "rod": "Slices on a rod", "base": "Slices on a base plate",
               "back": "Slices on a back plate", "waffle": "Waffle (interlocking grid)"}


class FabError(Exception):
    pass


@dataclass
class FabSettings:
    style: str = "stacked"
    up: str = "+Z"               # which model axis points up (geometry.orient)
    axis: str = "X"              # slicing axis for rod / base / back (stacked is always Z)
    thickness: float = 3.0       # sheet thickness
    spacing: float = 0.0         # centre to centre; 0 = automatic
    count: int = 0               # number of slices; overrides spacing when > 0
    clearance: float = 0.2       # play in slots and holes
    rod_diameter: float = 8.0
    rod_at: Point | None = None  # rod position in slice coordinates; None = middle of the model
    dowels: int = 2              # stacked: alignment holes through every layer (0 = none)
    dowel_diameter: float = 6.0
    wall: float = 4.0            # material kept around holes, and the width of spacer rings
    plate_margin: float = 10.0   # base / back plate reaches this far past the slices
    guide_outline: bool = True   # stacked: engrave the next layer's outline as a placement guide
    min_area: float = 4.0        # mm²: slivers smaller than this are dropped


@dataclass
class Frame:
    """Where a flat part sits in the model: its 2D (x, y) maps to origin + x*u + y*v, and
    the sheet's thickness is centred on that plane along n."""
    origin: Vec
    u: Vec
    v: Vec
    n: Vec

    def to3d(self, x: float, y: float, z: float = 0.0) -> Vec:
        o, u, v, n = self.origin, self.u, self.v, self.n
        return tuple(o[i] + x * u[i] + y * v[i] + z * n[i] for i in range(3))


@dataclass
class FabPart:
    name: str
    kind: str                    # slice | plate | spacer
    rings: list[Ring]            # outline first, then holes
    frame: Frame
    engrave: list[list[Point]] = field(default_factory=list)       # guide lines, open polylines
    marks: list[tuple[str, float, float, float]] = field(default_factory=list)  # text, x, y, height
    copies: int = 1
    places: list[Frame] = field(default_factory=list)   # where each copy goes, for the preview

    def area(self) -> float:
        return abs(_area(self.rings[0])) - sum(abs(_area(h)) for h in self.rings[1:])

    def size(self) -> tuple[float, float]:
        xs = [p[0] for p in self.rings[0]]
        ys = [p[1] for p in self.rings[0]]
        return max(xs) - min(xs), max(ys) - min(ys)


@dataclass
class FabResult:
    settings: FabSettings
    parts: list[FabPart]
    warnings: list[str] = field(default_factory=list)

    def count(self) -> int:
        return sum(p.copies for p in self.parts)


# ---- 2D regions (pyclipper) ---------------------------------------------------------------

def _area(ring: Ring) -> float:
    return 0.5 * sum(ring[i - 1][0] * ring[i][1] - ring[i][0] * ring[i - 1][1] for i in range(len(ring)))


def _ints(rings) -> list:
    return [[(round(x * SCALE), round(y * SCALE)) for x, y in r] for r in rings if len(r) >= 3]


def _mm(paths) -> list[Ring]:
    return [[(x / SCALE, y / SCALE) for x, y in p] for p in paths]


def _clip(subject, clip, op) -> list:
    """Boolean of integer paths; an empty subject or clip is handled the obvious way."""
    if not subject:
        return list(clip) if op == pyclipper.CT_UNION else []
    if not clip:
        return [] if op == pyclipper.CT_INTERSECTION else list(subject)
    pc = pyclipper.Pyclipper()
    pc.AddPaths(_norm(subject), pyclipper.PT_SUBJECT, True)
    pc.AddPaths(_norm(clip), pyclipper.PT_CLIP, True)
    return pc.Execute(op, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _norm(paths) -> list:
    """The region as clipper's own output: outlines CCW, holes CW. A hand-made clockwise
    rectangle would otherwise cancel the material it overlaps under nonzero filling."""
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    return pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _union(*groups) -> list:
    return _norm([p for g in groups for p in _norm(g)])


def _diff(a, b) -> list:
    return _clip(a, b, pyclipper.CT_DIFFERENCE)


def _inter(a, b) -> list:
    return _clip(a, b, pyclipper.CT_INTERSECTION)


def _offset(paths, delta: float, square: bool = False) -> list:
    if not paths:
        return []
    pco = pyclipper.PyclipperOffset(arc_tolerance=0.01 * SCALE)
    pco.AddPaths(paths, pyclipper.JT_MITER if square else pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return pco.Execute(delta * SCALE)


def _rect(x0, y0, x1, y1) -> list:
    return _ints([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]])


def _disk(cx, cy, r, n: int | None = None) -> list:
    n = n or max(24, min(180, int(2 * math.pi * r / 0.5)))
    return _ints([[(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]])


def _islands(paths, min_area: float = 0.0) -> list[list[Ring]]:
    """Split a region into separate pieces, each [outline (CCW), holes (CW)...]."""
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    out = []

    def walk(node):
        for outer in node.Childs:                     # outlines
            rings = [outer.Contour] + [h.Contour for h in outer.Childs]
            piece = _mm(rings)
            if _area(piece[0]) < 0:
                piece[0].reverse()
            for h in piece[1:]:
                if _area(h) > 0:
                    h.reverse()
            if abs(_area(piece[0])) - sum(abs(_area(h)) for h in piece[1:]) >= min_area:
                out.append(_simplify_rings(piece))
            for h in outer.Childs:                    # islands inside holes
                walk(h)
    walk(tree)
    return out


def _simplify_rings(rings: list[Ring], tol: float = 0.01) -> list[Ring]:
    return [r for r in (_rdp_closed(r, tol) for r in rings) if len(r) >= 3]


def _rdp_closed(ring: Ring, tol: float) -> Ring:
    if len(ring) < 8:
        return ring
    far = max(range(len(ring)), key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2)
    a = _rdp(ring[:far + 1], tol)
    b = _rdp(ring[far:] + [ring[0]], tol)
    return a[:-1] + b[:-1]


def _rdp(pts: list[Point], tol: float) -> list[Point]:
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = pts[i], pts[j]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy) or 1e-12
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            d = abs(dy * (pts[k][0] - ax) - dx * (pts[k][1] - ay)) / ln
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


def _bounds(paths) -> tuple[float, float, float, float]:
    xs = [x for p in paths for x, _ in p]
    ys = [y for p in paths for _, y in p]
    return min(xs) / SCALE, min(ys) / SCALE, max(xs) / SCALE, max(ys) / SCALE


def _intervals_at(paths, u: float, lo: float, hi: float) -> list[tuple[float, float]]:
    """The stretches of v where the region has material along the line at u."""
    strip = _rect(u - 0.005, lo - 1, u + 0.005, hi + 1)
    out = []
    for p in _union(_inter(paths, strip)):
        ys = [y / SCALE for _, y in p]
        out.append((min(ys), max(ys)))
    out.sort()
    merged: list[tuple[float, float]] = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _contains(paths, x: float, y: float) -> bool:
    pt = (round(x * SCALE), round(y * SCALE))
    inside = 0
    for p in paths:                                   # nonzero fill: sum of windings
        r = pyclipper.PointInPolygon(pt, p)
        if r == -1:
            return False
        if r:
            inside += 1 if pyclipper.Orientation(p) else -1
    return inside != 0


# ---- sections ------------------------------------------------------------------------------

# slicing axis -> plane normal, slice 2D (u, v) directions; u x v = n so parts are not mirrored
AXES = {
    "Z": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "X": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "Y": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
}
_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _frame(axis: str, c: float) -> Frame:
    n, u, v = AXES[axis]
    origin = [0.0, 0.0, 0.0]
    origin[_INDEX[axis]] = c
    return Frame(tuple(origin), u, v, n)


class Mesh:
    """The model's triangles, cut by planes. Slicing the mesh is fast and works the same
    for every model: OpenCascade's B-rep section misses whole slices of a solid that came
    from an STL, and is slow on one."""

    def __init__(self, shape: TopoDS_Shape, deflection: float = 0.02):
        import numpy as np
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, deflection, False, 0.2, True)
        verts, tris = geometry.mesh(shape, deflection)
        self.v = np.asarray(verts, dtype=float).reshape(-1, 3)
        self.t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)

    def section(self, axis: str, c: float) -> list:
        """The material on the plane axis = c, as a clipper region in slice (u, v)."""
        import numpy as np
        n, u, v = AXES[axis]
        u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
        k = _INDEX[axis]
        d = self.v[:, k] - c
        # a vertex on (or a hair from) the plane makes near-zero segments that chain wrongly,
        # e.g. along a sphere's seam: move the plane off it, by far less than a laser kerf
        for nudge in (0.0, 0.01, -0.01, 0.023, -0.023):
            d = self.v[:, k] - (c + nudge)
            if not np.any(np.abs(d) < 0.002):
                break
        side = d > 0
        tri_side = side[self.t]
        tris = self.t[tri_side.any(axis=1) & ~tri_side.all(axis=1)]
        if not len(tris):
            return []
        segs = []
        for tri in tris.tolist():
            pts = []
            for i, j in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                if side[i] != side[j]:
                    if i > j:                  # same edge, same point from both triangles
                        i, j = j, i
                    f = d[i] / (d[i] - d[j])
                    q = self.v[i] + f * (self.v[j] - self.v[i])
                    pts.append((float(q @ u), float(q @ v)))
            if len(pts) == 2:
                segs.append(pts)
        loops = geometry._chain(segs, tol=1e-5)   # shared edges give bit-identical points
        if not loops:
            return []
        pc = pyclipper.Pyclipper()           # even-odd: holes inside outlines
        pc.AddPaths(_ints(loops), pyclipper.PT_SUBJECT, True)
        return _norm(pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD))


def section(shape, axis: str, c: float) -> list:
    """The material of the shape (or a Mesh of it) on the plane axis = c."""
    return (shape if isinstance(shape, Mesh) else Mesh(shape)).section(axis, c)


def _extent(box, axis: str) -> tuple[float, float]:
    return {"X": (box.xmin, box.xmax), "Y": (box.ymin, box.ymax), "Z": (box.zmin, box.zmax)}[axis]


def positions(lo: float, hi: float, s: FabSettings, stacked: bool = False) -> list[float]:
    """Centres of the slices along the slicing axis."""
    length = hi - lo
    t = s.thickness
    if stacked:
        n = max(1, round(length / t))
        return [lo + (i + 0.5) * t for i in range(n)]
    if s.count > 0:
        return [lo + length * (i + 0.5) / s.count for i in range(s.count)]
    step = s.spacing if s.spacing > 0 else max(4 * t, length / 8)
    n = max(1, int(math.floor((length - t) / step)) + 1)
    first = (lo + hi) / 2 - (n - 1) / 2 * step
    return [first + i * step for i in range(n)]


# ---- styles --------------------------------------------------------------------------------

def make(shape: TopoDS_Shape, s: FabSettings) -> FabResult:
    if s.style not in STYLES:
        raise FabError(f"Unknown style {s.style!r}")
    if s.thickness <= 0:
        raise FabError("The sheet thickness must be more than 0")
    shape = geometry.orient(shape, s.up)
    res = FabResult(s, [])
    {"stacked": _stacked, "rod": _rod, "base": _plated, "back": _plated, "waffle": _waffle}[s.style](shape, s, res)
    if not res.parts:
        raise FabError("Nothing to cut: the slices miss the model")
    return res


def _slices(shape, axis, cs, s, res, prefix):
    """(name, centre, region) for every slice that cuts material."""
    mesh = getattr(res, "_mesh", None) or Mesh(shape)
    res._mesh = mesh
    out = []
    for c in cs:
        reg = mesh.section(axis, c)
        if not reg or not _islands(reg, s.min_area):
            res.warnings.append(f"No material at {axis} = {c:.1f} mm: slice left out")
            continue
        out.append((f"{prefix}{len(out) + 1}", c, reg))
    return out


def _add_islands(res, name, kind, reg, frame, s, engrave=None, marks=None):
    pieces = _islands(reg, s.min_area)
    for i, rings in enumerate(sorted(pieces, key=lambda r: -abs(_area(r[0])))):
        nm = name if len(pieces) == 1 else f"{name}{chr(ord('a') + i)}"
        res.parts.append(FabPart(nm, kind, rings, frame, list(engrave or []), list(marks or [])))
    if len(pieces) > 1:
        res.warnings.append(f"{name} falls apart into {len(pieces)} pieces")


def _stacked(shape, s, res):
    box = geometry.bounding_box(shape)
    lo, hi = box.zmin, box.zmax
    layers = _slices(shape, "Z", positions(lo, hi, s, stacked=True), s, res, "L")
    # dowel holes: where every layer has material, away from the edges
    holes = []
    if s.dowels > 0 and layers:
        common = layers[0][2]
        for _, _, reg in layers[1:]:
            common = _inter(common, reg)
        safe = _offset(common, -(s.dowel_diameter / 2 + s.wall))
        holes = _spread_points(safe, s.dowels)
        if len(holes) < s.dowels:
            res.warnings.append("Not enough room shared by every layer for the dowel holes"
                                + (f": {len(holes)} placed" if holes else ": none placed"))
    r = s.dowel_diameter / 2 + s.clearance / 2
    for i, (name, c, reg) in enumerate(layers):
        cut = _diff(reg, _union(*[_disk(x, y, r) for x, y in holes])) if holes else reg
        guide = []
        if s.guide_outline:      # where the smaller neighbour sits: drawn on the bigger layer
            for j in (i - 1, i + 1):
                if 0 <= j < len(layers) and _region_area(layers[j][2]) < _region_area(reg):
                    guide += _open_inside(_mm(layers[j][2]), _offset(cut, -0.3))
        _add_islands(res, name, "slice", cut, _frame("Z", c), s, guide)


def _region_area(paths) -> float:
    return sum(pyclipper.Area(p) for p in _norm(paths)) / SCALE ** 2


def _spread_points(region, n: int) -> list[Point]:
    """n points inside the region, as far apart as possible (grid search, then greedy)."""
    if not region:
        return []
    x0, y0, x1, y1 = _bounds(region)
    step = max((x1 - x0), (y1 - y0)) / 40 or 1.0
    cand = []
    y = y0
    while y <= y1:
        x = x0
        while x <= x1:
            if _contains(region, x, y):
                cand.append((x, y))
            x += step
        y += step
    if not cand:
        return []
    cx = sum(p[0] for p in cand) / len(cand)
    cy = sum(p[1] for p in cand) / len(cand)
    if n == 1:
        return [min(cand, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)]
    picked = [max(cand, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)]
    while len(picked) < n:
        best = max(cand, key=lambda p: min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in picked))
        if min((best[0] - q[0]) ** 2 + (best[1] - q[1]) ** 2 for q in picked) < 1e-6:
            break
        picked.append(best)
    return picked


def _open_inside(rings: list[Ring], clip) -> list[list[Point]]:
    """The parts of closed rings (drawn as lines) that lie inside a region."""
    if not rings or not clip:
        return []
    pc = pyclipper.Pyclipper()
    for r in _ints(rings):
        pc.AddPath(r + [r[0]], pyclipper.PT_SUBJECT, False)
    pc.AddPaths(clip, pyclipper.PT_CLIP, True)
    tree = pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return [line for line in _mm(pyclipper.OpenPathsFromPolyTree(tree)) if len(line) >= 2]


def _rod(shape, s, res):
    axis = s.axis if s.axis in AXES else "Z"
    box = geometry.bounding_box(shape)
    lo, hi = _extent(box, axis)
    cs = positions(lo, hi, s)
    slices = _slices(shape, axis, cs, s, res, "S")
    if not slices:
        return
    if s.rod_at is not None:
        ru, rv = s.rod_at
    else:
        x0, y0, x1, y1 = _bounds([p for _, _, reg in slices for p in reg])
        ru, rv = (x0 + x1) / 2, (y0 + y1) / 2
    hole = _disk(ru, rv, s.rod_diameter / 2 + s.clearance / 2)
    hub_r = s.rod_diameter / 2 + s.wall
    for name, c, reg in slices:
        need = not _contains(_offset(reg, -(hub_r - 0.01)), ru, rv)
        if need:            # the rod misses this slice's material: add a hub and a bridge to it
            reg = _union(reg, _disk(ru, rv, hub_r), _bridges(reg, ru, rv, max(2 * s.wall, s.rod_diameter)))
            res.warnings.append(f"{name}: the rod misses it, so it gets a hub around the rod")
        _add_islands(res, name, "slice", _diff(reg, hole), _frame(axis, c), s)
    # spacer rings fill each gap between slices
    gaps = [b - a - s.thickness for a, b in zip([c for _, c, _ in slices], [c for _, c, _ in slices][1:])]
    ring = _islands(_diff(_disk(0, 0, hub_r), _disk(0, 0, s.rod_diameter / 2 + s.clearance / 2)))
    total = sum(max(0, round(g / s.thickness)) for g in gaps)
    if total and ring:
        places = []
        for (_, a, _), g in zip(slices, gaps):
            n = max(0, round(g / s.thickness))
            for r in range(n):            # rings stacked from one slice towards the next
                f = _frame(axis, a + s.thickness / 2 + (r + 0.5) * (g / n))
                places.append(Frame(f.to3d(ru, rv), f.u, f.v, f.n))
        part = FabPart("Spacer", "spacer", ring[0], places[0], places=places)
        part.copies = total
        res.parts.append(part)
    off = [g - round(g / s.thickness) * s.thickness for g in gaps]
    if any(abs(o) > 0.5 for o in off):
        res.warnings.append("The gaps are not a whole number of sheets: pick a spacing that is "
                            f"a multiple of {s.thickness:g} mm for exact spacers")


def _bridges(reg, x, y, width) -> list:
    """A straight strip from (x, y) to the nearest point of every piece of the region."""
    out = []
    for piece in _islands(reg):
        best = min(piece[0], key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
        dx, dy = best[0] - x, best[1] - y
        ln = math.hypot(dx, dy)
        if ln < 1e-6:
            continue
        ex, ey = dx / ln, dy / ln
        far = (best[0] + ex * width, best[1] + ey * width)   # bite into the piece for a solid join
        nx, ny = -ey * width / 2, ex * width / 2
        out += _ints([[(x + nx, y + ny), (far[0] + nx, far[1] + ny), (far[0] - nx, far[1] - ny), (x - nx, y - ny)]])
    return out


def _plated(shape, s, res):
    """Upright slices with tabs in slots of a base plate (style base) or back plate (back)."""
    axis = s.axis if s.axis in ("X", "Y") else "X"
    box = geometry.bounding_box(shape)
    lo, hi = _extent(box, axis)
    slices = _slices(shape, axis, positions(lo, hi, s), s, res, "S")
    if not slices:
        return
    t, cl = s.thickness, s.clearance
    ux0, vy0, ux1, vy1 = _bounds([p for _, _, reg in slices for p in reg])
    # the plate goes against the model itself, not just its outermost slice
    vy0 = box.zmin
    ux1 = box.ymax if axis == "X" else box.xmax
    back = s.style == "back"
    # local frame: p runs along the plate, q is the height above it (q < 0 is inside the plate)
    if back:
        to_l = lambda x, y: (y, ux1 - x)          # noqa: E731
        to_s = lambda p, q: (ux1 - q, p)          # noqa: E731
    else:
        to_l = lambda x, y: (x, y - vy0)          # noqa: E731
        to_s = lambda p, q: (p, q + vy0)          # noqa: E731

    def conv(paths, f):
        return [[(round(a * SCALE), round(b * SCALE)) for a, b in (f(x / SCALE, y / SCALE) for x, y in p)] for p in paths]

    slots = []                                     # (slice number, a centre, p0, p1)
    for k, (name, c, reg) in enumerate(slices):
        loc = conv(reg, to_l)
        p0, q0, p1, q1 = _bounds(loc)
        # a foot down to the plate, as wide as the part a little way up: a round part
        # touches the plate only at a point, and one floating above it not at all
        band = max(3 * t, 0.15 * (q1 - q0))
        foot = _inter(loc, _rect(p0 - 1, q0 - 1, p1 + 1, q0 + band))
        fa, _, fb, _ = _bounds(foot)
        if fb - fa < 2 * t:
            mid = (fa + fb) / 2
            fa, fb = mid - t, mid + t
        loc = _union(loc, _rect(fa, 0, fb, q0 + band / 2))
        tabs = []
        for a, b in _feet(loc):
            w = b - a
            if w >= 6 * t:
                tw = min(max(2 * t, 0.2 * w), 40)
                tabs += [(a + w * 0.25 - tw / 2, a + w * 0.25 + tw / 2), (a + w * 0.75 - tw / 2, a + w * 0.75 + tw / 2)]
            elif w >= 2 * t:
                tabs.append(((a + b) / 2 - w / 4, (a + b) / 2 + w / 4))
        if not tabs:
            res.warnings.append(f"{name}: too narrow where it meets the plate for a tab")
        loc = _union(loc, *[_rect(a, -t, b, 0.01) for a, b in tabs])
        slots += [(k + 1, c, a, b) for a, b in tabs]
        _add_islands(res, name, "slice", conv(loc, to_s), _frame(axis, c), s)
    # the plate, in (a, p): a along the slicing axis, p along the plate
    m = s.plate_margin
    cs = [c for _, c, _ in slices]
    pl = [to_l(x, y)[0] for x, y in ((ux0, vy0), (ux1, vy1), (ux0, vy1), (ux1, vy0))]
    plate = _rect(min(cs) - t / 2 - m, min(pl) - m, max(cs) + t / 2 + m, max(pl) + m)
    cuts, marks = [], []
    for k, c, a, b in slots:
        cuts += _rect(c - (t + cl) / 2, a - cl / 2, c + (t + cl) / 2, b + cl / 2)
    for k, c in enumerate(cs):
        lows = [a for kk, _, a, _ in slots if kk == k + 1]
        if lows:
            marks.append((str(k + 1), c, min(lows) - 4, 3.0))
    plate = _diff(plate, cuts)
    # plate frame
    if back:      # vertical, behind the slices; plate (a, p) -> 3D
        if axis == "X":     # slices at x = c, u = y, v = z: plate in y = ymax + t/2, (x, z) = (a, p)
            f = Frame((0, ux1 + t / 2, 0), (1, 0, 0), (0, 0, 1), (0, -1, 0))
        else:               # slices at y = c, u = x: plate in x = xmax + t/2, (y, z) = (a, p)
            f = Frame((ux1 + t / 2, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0))
        mapped, mk = plate, marks
    else:         # horizontal, under the slices
        f = Frame((0, 0, vy0 - t / 2), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        if axis == "X":     # a = x, p = u = y
            mapped, mk = plate, marks
        else:               # a = y, p = u = x: plate (x, y) = (p, a)
            mapped = [[(y, x) for x, y in path] for path in plate]
            mapped = [list(reversed(p)) for p in mapped]
            mk = [(tx, y, x, h) for tx, x, y, h in marks]
    _add_islands(res, "Back plate" if back else "Base plate", "plate", mapped, f, s, marks=mk)


def _feet(loc) -> list[tuple[float, float]]:
    """Stretches of p where the part touches the plate (q = 0)."""
    band = _inter(loc, _rect(-1e6, -0.01, 1e6, 0.5))
    spans = []
    for p in _union(band):
        xs = [x / SCALE for x, _ in p]
        spans.append((min(xs), max(xs)))
    spans.sort()
    out: list[tuple[float, float]] = []
    for a, b in spans:
        if out and a <= out[-1][1] + 0.5:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _waffle(shape, s, res):
    box = geometry.bounding_box(shape)
    t, cl = s.thickness, s.clearance
    w = t + cl
    xs = _slices(shape, "X", positions(box.xmin, box.xmax, s), s, res, "X")   # (u, v) = (y, z)
    ys = _slices(shape, "Y", positions(box.ymin, box.ymax, s), s, res, "Y")   # (u, v) = (x, z)
    lo, hi = box.zmin - 1, box.zmax + 1
    cut_x = [[] for _ in xs]
    cut_y = [[] for _ in ys]
    for i, (_, xc, xreg) in enumerate(xs):
        for j, (_, yc, yreg) in enumerate(ys):
            a = _intervals_at(xreg, yc, lo, hi)          # X slice along the line at y = yc
            b = _intervals_at(yreg, xc, lo, hi)          # Y slice along the line at x = xc
            common = [(max(p, r), min(q, u)) for p, q in a for r, u in b if min(q, u) - max(p, r) > 2 * t]
            for z0, z1 in common:
                zm = (z0 + z1) / 2
                above = min([p for p, _ in a if p > z1 - 1e-6] + [hi])
                below = max([q for _, q in b if q < z0 + 1e-6] + [lo])
                cut_x[i] += _rect(yc - w / 2, zm - cl / 2, yc + w / 2, (z1 + above) / 2 if above < hi else hi)
                cut_y[j] += _rect(xc - w / 2, (z0 + below) / 2 if below > lo else lo, xc + w / 2, zm + cl / 2)
    for (name, c, reg), cut in zip(xs, cut_x):
        _add_islands(res, name, "slice", _diff(reg, cut), _frame("X", c), s)
    for (name, c, reg), cut in zip(ys, cut_y):
        _add_islands(res, name, "slice", _diff(reg, cut), _frame("Y", c), s)
    if not cut_x or not any(cut_x):
        res.warnings.append("The two sets of slices never cross: nothing holds them together")


# ---- 3D preview ----------------------------------------------------------------------------

def solid(part: FabPart, thickness: float):
    """The part as a 3D plate in its place in the model (for the preview)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    f = part.frame

    def wire(ring):
        poly = BRepBuilderAPI_MakePolygon()
        for x, y in ring:
            poly.Add(gp_Pnt(*f.to3d(x, y, -thickness / 2)))
        poly.Close()
        return poly.Wire()

    mf = BRepBuilderAPI_MakeFace(wire(part.rings[0]), True)
    for h in part.rings[1:]:
        mf.Add(wire(h))
    face = mf.Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(*[c * thickness for c in f.n])).Shape()


def assembly(res: FabResult):
    """Every part in place, as one compound (every spacer ring in its gap)."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    from dataclasses import replace
    for p in res.parts:
        for frame in p.places or [p.frame]:
            try:
                b.Add(comp, solid(replace(p, frame=frame), res.settings.thickness))
            except Exception:           # a degenerate sliver must not stop the preview
                continue
    return comp
