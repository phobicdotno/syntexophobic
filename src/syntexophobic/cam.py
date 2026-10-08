"""2.5D toolpaths by Z-levels.

At each cutting level the tool may not enter anywhere the part exists at or above that
height (a flat endmill comes in from the top). That forbidden area, grown by the tool
radius, is subtracted from the stock outline, and what is left is cleared with
concentric offset rings. Polygon work is done with Clipper in integer micrometres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pyclipper

from . import geometry
from .job import Job, Operation

SCALE = 1000  # clipper works in integers: 1 unit = 1 µm
EPS = 0.01    # sample the part this far above each level


@dataclass
class Move:
    kind: str           # "rapid" or "feed"
    x: float | None
    y: float | None
    z: float | None
    f: float | None = None


@dataclass
class Toolpath:
    op: Operation
    tool_diameter: float
    moves: list[Move] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)
    tabs: list = field(default_factory=list)

    def positions(self):
        """(kind, x, y, z, f) with every axis filled in from the previous position."""
        x = y = z = 0.0
        for m in self.moves:
            x = x if m.x is None else m.x
            y = y if m.y is None else m.y
            z = z if m.z is None else m.z
            yield m.kind, x, y, z, m.f

    def minutes(self) -> float:
        total, last = 0.0, None
        for kind, x, y, z, f in self.positions():
            if last and kind == "feed":
                total += ((x - last[0]) ** 2 + (y - last[1]) ** 2 + (z - last[2]) ** 2) ** 0.5 / f
            last = (x, y, z)
        return total


# ---- polygon helpers ---------------------------------------------------------------

def _to_int(loops):
    return [[(round(x * SCALE), round(y * SCALE)) for x, y in loop] for loop in loops]


def _to_mm(paths):
    return [[(x / SCALE, y / SCALE) for x, y in p] for p in paths]


def _offset(paths, delta_mm: float):
    if not paths:
        return []
    pco = pyclipper.PyclipperOffset(arc_tolerance=0.005 * SCALE)
    pco.AddPaths(paths, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return pco.Execute(delta_mm * SCALE)


def _boolean(subject, clip, op, fill=pyclipper.PFT_NONZERO):
    if not subject:
        return list(clip) if op == pyclipper.CT_UNION else []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    if clip:
        pc.AddPaths(clip, pyclipper.PT_CLIP, True)
    return pc.Execute(op, fill, fill)


def _outermost(paths):
    """Only the top-level outlines: no holes and no islands standing inside holes."""
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return [child.Contour for child in tree.Childs]


def _region_from_slices(per_solid):
    """Material area at one height: even-odd per solid (outline + holes), then union."""
    region = []
    for loops in per_solid:
        solid = _boolean(_to_int(loops), [], pyclipper.CT_UNION, pyclipper.PFT_EVENODD)
        region = _boolean(region + solid, [], pyclipper.CT_UNION) if region else solid
    return region


# ---- levels ------------------------------------------------------------------------

def cutting_levels(top: float, bottom: float, stepdown: float, flats: list[float]) -> list[float]:
    levels, z = [], top - stepdown
    while z > bottom + 1e-6:
        levels.append(z)
        z -= stepdown
    levels.append(bottom)
    levels += [f for f in flats if bottom < f < top]   # always land exactly on floors
    out = []
    for z in sorted(levels, reverse=True):
        if not out or out[-1] - z > 0.01:
            out.append(round(z, 4))
    return out


# ---- the generator -----------------------------------------------------------------

class Setup:
    """Model + stock, with Z-slices cached so several operations can share them."""

    def __init__(self, shape, job: Job, cache: dict | None = None):
        """`cache` holds geometry that only depends on the model (bounding box, flat
        levels, slices); pass the same dict while the model is unchanged."""
        self.shape = shape
        self.job = job
        cache = {} if cache is None else cache
        if "part" not in cache:
            cache["part"] = geometry.bounding_box(shape)
            cache["flats"] = geometry.upward_flat_levels(shape)
            cache["slices"] = {}
            cache["slicer"] = geometry.MeshSlicer(shape)
            cache["holes"] = geometry.find_holes(shape)
        self.part = cache["part"]
        m = job.stock.margin
        self.stock = geometry.Box(self.part.xmin - m, self.part.ymin - m, self.part.zmin,
                                  self.part.xmax + m, self.part.ymax + m,
                                  self.part.zmax + job.stock.top_offset)
        self.flats = cache["flats"]
        self._slices: dict[float, list] = cache["slices"]
        self.slicer: geometry.MeshSlicer = cache["slicer"]
        self.holes: list[geometry.Hole] = cache["holes"]

    def slice_region(self, z: float):
        key = round(z, 4)
        if key not in self._slices:
            self._slices[key] = _region_from_slices(self.slicer.slice(z + EPS))
        return self._slices[key]

    def stock_rect(self):
        s = self.stock
        return _to_int([[(s.xmin, s.ymin), (s.xmax, s.ymin), (s.xmax, s.ymax), (s.xmin, s.ymax)]])


def _ramp_and_lap(loop, z_from: float, z_to: float, ramp_angle: float):
    """Points for one closed loop: ramp down along the loop itself, then a full lap at depth.

    The ramp follows the path (several laps on short loops, like a helix), so the tool
    never plunges straight into material. The full lap at depth starts and ends where
    the ramp reached depth, which recuts the ramped stretch at full depth.
    """
    n = len(loop)
    perimeter = sum(math.dist(loop[i], loop[(i + 1) % n]) for i in range(n))
    if ramp_angle <= 0 or perimeter < 0.5:
        return [(*loop[0], z_from), (*loop[0], z_to)] + [(*p, z_to) for p in loop[1:] + loop[:1]]
    slope = math.tan(math.radians(ramp_angle))
    out = [(*loop[0], z_from)]
    cur, z, i = loop[0], z_from, 0
    while True:
        nxt = loop[(i + 1) % n]
        seg = math.dist(cur, nxt)
        if z - seg * slope <= z_to:
            t = (z - z_to) / slope / seg if seg else 0.0
            hit = (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
            out.append((*hit, z_to))
            out += [(*loop[(i + 1 + k) % n], z_to) for k in range(n)]
            out.append((*hit, z_to))
            return out
        z -= seg * slope
        cur, i = nxt, (i + 1) % n
        out.append((*cur, z))


def _tab_centres(region, r: float, count: int, width: float):
    """Evenly spaced tab positions on the tool-centre path around each outer outline."""
    if count <= 0:
        return []
    centres = []
    for loop in _to_mm(_outermost(_offset(region, r))):
        n = len(loop)
        lengths = [math.dist(loop[i], loop[(i + 1) % n]) for i in range(n)]
        perimeter = sum(lengths)
        if perimeter < count * (width + 2 * r) * 1.5:
            continue                      # too small to hold tabs
        targets = [perimeter * (k + 0.5) / count for k in range(count)]
        acc, k = 0.0, 0
        for i, seg in enumerate(lengths):
            while k < count and targets[k] <= acc + seg:
                t = (targets[k] - acc) / seg
                a, b = loop[i], loop[(i + 1) % n]
                centres.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                k += 1
            acc += seg
    return centres


def _seg_dist(p, a, b) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
    return math.dist(p, (a[0] + ax * t, a[1] + ay * t))


def _lift_over_tabs(points, centres, reach: float, tab_top: float):
    """Raise the path to tab_top wherever the tool would cut into a tab.

    The path steps straight up just before a tab and straight down just after it, so
    no slanted move crosses into the tab.
    """
    if not centres:
        return points

    def in_tab(x, y, z):
        return z < tab_top and any(math.dist(c, (x, y)) < reach for c in centres)

    out = [points[0]]
    lifted = in_tab(*points[0])
    if lifted:
        out[0] = (points[0][0], points[0][1], tab_top)
    for a, b in zip(points, points[1:]):
        near = min(b[2], a[2]) < tab_top and any(_seg_dist(c, a, b) < reach + 0.25 for c in centres)
        steps = max(1, math.ceil(math.dist(a[:2], b[:2]) / 0.1)) if near else 1
        for k in range(1, steps + 1):
            t = k / steps
            x, y, z = (a[i] + (b[i] - a[i]) * t for i in range(3))
            now = near and in_tab(x, y, z)
            if now and not lifted:
                px, py, pz = out[-1]
                out.append((px, py, max(pz, tab_top)))      # step up before the tab
            if lifted and not now:
                out.append((x, y, tab_top))                  # clear of the tab: step down here
            out.append((x, y, tab_top if now else z))
            lifted = now
    return out


def _rings(op: Operation, forbidden, setup: "Setup", r: float, diameter: float):
    """Closed paths for one level, in cutting order."""
    if op.type == "finish":
        return [_offset(forbidden, r + op.leave)] if forbidden else []
    if op.type == "profile":
        outer = _outermost(_offset(forbidden, r + op.leave))
        return [outer] if outer else []
    region = _boolean(setup.stock_rect(), _offset(forbidden, r + op.leave), pyclipper.CT_DIFFERENCE)
    rings = []
    while region:
        rings.append(region)
        region = _offset(region, -op.stepover * diameter)
    return rings


def hole_matches(op: Operation, tool_diameter: float, d: float) -> bool:
    """Which holes a drill or bore operation takes, unless hole_min/hole_max say otherwise."""
    if op.type == "drill":
        lo, hi = tool_diameter - 0.05, tool_diameter + 0.3
    else:
        lo, hi = tool_diameter + 0.2, float("inf")
    lo = lo if op.hole_min is None else op.hole_min
    hi = hi if op.hole_max is None else op.hole_max
    return lo <= d <= hi


def _nearest_order(holes):
    """Visit holes nearest-first from the origin corner: short rapids, no clever routing."""
    left, out, at = list(holes), [], (-1e9, -1e9)
    while left:
        h = min(left, key=lambda h: math.dist(at, (h.x, h.y)))
        left.remove(h)
        out.append(h)
        at = (h.x, h.y)
    return out


def _holes(setup: Setup, op: Operation) -> Toolpath:
    tool = setup.job.tool(op.tool)
    r = tool.diameter / 2
    top = setup.stock.zmax
    safe = top + setup.job.machine.safe_z
    travel, clearance = top + 1.0, top + 0.5
    tp = Toolpath(op, tool.diameter)
    moves = tp.moves
    moves.append(Move("rapid", None, None, safe))
    holes = [h for h in setup.holes if hole_matches(op, tool.diameter, h.diameter)]
    for h in _nearest_order(holes):
        through = h.bottom <= setup.part.zmin + 1e-6
        bottom = h.bottom - (0.3 * tool.diameter if through and op.type == "drill" else 0.0)
        if op.bottom is not None:
            bottom = max(bottom, op.bottom)
        moves += [Move("rapid", None, None, travel), Move("rapid", h.x, h.y, None),
                  Move("rapid", None, None, clearance)]
        if op.type == "drill":
            # Full-retract pecks (GRBL has no G83): down one peck, out to clear chips, back.
            depth = clearance
            while depth > bottom + 1e-6:
                nxt = max(bottom, depth - op.stepdown)   # counted from the stock top: safe before roughing too
                moves.append(Move("feed", None, None, nxt, op.plunge))
                if nxt > bottom + 1e-6:
                    moves.append(Move("rapid", None, None, clearance))
                    moves.append(Move("rapid", None, None, nxt + 0.5))
                depth = nxt
            moves.append(Move("rapid", None, None, clearance))
        else:
            # Helical bore: circles from the centre outwards, each ramped down its whole depth.
            outer = h.diameter / 2 - r - op.leave
            radii, rad = [], outer
            while rad > 0.05:
                radii.append(rad)
                rad -= op.stepover * tool.diameter
            for rad in reversed(radii):
                n = max(24, math.ceil(2 * math.pi * rad / 0.2))
                loop = [(h.x + rad * math.cos(2 * math.pi * k / n), h.y + rad * math.sin(2 * math.pi * k / n))
                        for k in range(n)]
                pts = _ramp_and_lap(loop, clearance, bottom, op.ramp_angle or 3.0)
                moves.append(Move("rapid", pts[0][0], pts[0][1], None))
                moves += [Move("feed", x, y, z, op.feed) for x, y, z in pts[1:]]
                moves.append(Move("rapid", None, None, clearance))
                moves.append(Move("rapid", h.x, h.y, None))
        tp.levels.append(bottom)
    moves.append(Move("rapid", None, None, safe))
    return tp


def generate(setup: Setup, op: Operation) -> Toolpath:
    if op.type in ("drill", "bore"):
        return _holes(setup, op)
    tool = setup.job.tool(op.tool)
    r = tool.diameter / 2
    top = setup.stock.zmax
    bottom = setup.part.zmin if op.bottom is None else op.bottom
    levels = cutting_levels(top, bottom, op.stepdown, setup.flats)
    safe = top + setup.job.machine.safe_z
    tp = Toolpath(op, tool.diameter, levels=levels)
    moves = tp.moves
    moves.append(Move("rapid", None, None, safe))

    tab_top = bottom + op.tab_height
    tabs = []
    if op.tabs > 0 and op.type in ("profile", "finish"):
        silhouette: list = []
        for z in levels:
            silhouette = _boolean(silhouette, setup.slice_region(z), pyclipper.CT_UNION)
        tabs = _tab_centres(silhouette, r + op.leave, op.tabs, op.tab_width)
    tp.tabs = tabs
    reach = op.tab_width / 2 + r

    # XY rapids happen just above the stock. The tool then drops fast to 0.5 mm above the
    # previous level: each level's region lies inside the one above, so that air is cut.
    travel = top + 1.0
    forbidden: list = []
    clearance = top + 0.5
    for z in levels:
        forbidden = _boolean(forbidden, setup.slice_region(z), pyclipper.CT_UNION)
        for ring in _rings(op, forbidden, setup, r, tool.diameter):
            for loop in _to_mm(ring):
                if len(loop) < 2:
                    continue
                pts = _ramp_and_lap(loop, clearance, z, op.ramp_angle)
                if tabs and z < tab_top:
                    pts = _lift_over_tabs(pts, tabs, reach, tab_top)
                moves.append(Move("rapid", None, None, travel))
                moves.append(Move("rapid", pts[0][0], pts[0][1], None))
                moves.append(Move("rapid", None, None, pts[0][2]))
                for x, y, zz in pts[1:]:
                    moves.append(Move("feed", x, y, zz, op.feed))
        clearance = z + 0.5
    moves.append(Move("rapid", None, None, safe))
    return tp


def generate_all(setup: Setup) -> list[Toolpath]:
    return [generate(setup, op) for op in setup.job.operations if op.enabled]
