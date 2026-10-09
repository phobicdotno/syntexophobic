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
    tool_shape: str = "flat"
    side: str = "top"             # bottom: coordinates are in the flipped part's frame
    flip: tuple | None = None     # (cy, cz) of the flip axis, to show bottom paths in place
    vbit_k: float = 1.0           # V-bit: cut width per unit depth on each side, tan(angle/2)

    def positions(self):
        """(kind, x, y, z, f) with every axis filled in from the previous position."""
        x = y = z = 0.0
        for m in self.moves:
            x = x if m.x is None else m.x
            y = y if m.y is None else m.y
            z = z if m.z is None else m.z
            yield m.kind, x, y, z, m.f

    def minutes(self) -> float:
        """Cutting time (feed moves)."""
        total, last = 0.0, None
        for kind, x, y, z, f in self.positions():
            if last and kind == "feed":
                total += ((x - last[0]) ** 2 + (y - last[1]) ** 2 + (z - last[2]) ** 2) ** 0.5 / f
            last = (x, y, z)
        return total

    def rapid_minutes(self, rate: float = 5000.0) -> float:
        """Time spent on rapid moves at `rate` mm/min."""
        total, last = 0.0, None
        for kind, x, y, z, f in self.positions():
            if last and kind == "rapid":
                total += ((x - last[0]) ** 2 + (y - last[1]) ** 2 + (z - last[2]) ** 2) ** 0.5 / rate
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


def _cells_to_region(mask, x0: float, y0: float, res: float):
    """Clipper region covering the True cells of a grid mask (cell centres at x0 + i·res)."""
    import numpy as np
    rows = []
    for j in np.nonzero(mask.any(axis=1))[0]:
        row = mask[j].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], row, [0]])))
        for a, b in zip(edges[::2], edges[1::2]):            # runs of True cells
            xa, xb = x0 + (a - 0.5) * res, x0 + (b - 0.5) * res
            ya, yb = y0 + (j - 0.5) * res, y0 + (j + 0.5) * res
            rows.append([(xa, ya), (xb, ya), (xb, yb), (xa, yb)])
    return _boolean(_to_int(rows), [], pyclipper.CT_UNION) if rows else []


def _segment_inside(region, a, b) -> bool:
    """Is the straight XY line a-b entirely inside the region (clipper paths)?"""
    if not region:
        return False
    seg = _to_int([[a, b]])[0]
    if seg[0] == seg[1]:
        return pyclipper.PointInPolygon(seg[0], region[0]) != 0 if len(region) == 1 else True
    pc = pyclipper.Pyclipper()
    pc.AddPath(seg, pyclipper.PT_SUBJECT, False)
    pc.AddPaths(region, pyclipper.PT_CLIP, True)
    inside = pyclipper.OpenPathsFromPolyTree(pc.Execute2(pyclipper.CT_INTERSECTION,
                                                          pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO))
    length = sum(math.dist(p[i], p[i + 1]) for p in inside for i in range(len(p) - 1))
    return length >= math.dist(seg[0], seg[1]) - 2          # within 2 µm


def _hop(moves, last, nxt, region, clearance, travel):
    """Move to the start of the next pass: lift only to the clearance height (just above
    the level cut before) when the straight hop stays inside the region the tool centre may
    use at this level, otherwise go over the top."""
    if last is not None and _segment_inside(region, last, nxt):
        moves += [Move("rapid", None, None, clearance), Move("rapid", nxt[0], nxt[1], None)]
    else:
        moves += [Move("rapid", None, None, travel), Move("rapid", nxt[0], nxt[1], None),
                  Move("rapid", None, None, clearance)]


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

    def __init__(self, shape, job: Job, cache: dict | None = None, stock_box=None):
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
        st, b = job.stock, self.part
        top = b.zmax + st.top_offset
        cx, cy = (b.xmin + b.xmax) / 2, (b.ymin + b.ymax) / 2
        hx = st.width / 2 if st.width > 0 else (b.xmax - b.xmin) / 2 + st.margin
        hy = st.depth / 2 if st.depth > 0 else (b.ymax - b.ymin) / 2 + st.margin
        bottom = top - st.height if st.height > 0 else b.zmin
        self.stock = stock_box or geometry.Box(cx - hx, cy - hy, bottom, cx + hx, cy + hy, top)
        if st.width and st.width < b.xmax - b.xmin or st.depth and st.depth < b.ymax - b.ymin \
                or st.height and bottom > b.zmin + 1e-6:
            self.warning = "The stock is smaller than the part."
        else:
            self.warning = ""
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


def ball_keep_out(setup: "Setup", z: float, r: float, leave: float, samples: int = 40):
    """Where a ball end's centre line may not go with its tip at height z.

    The ball touches material from its tip up to its equator: at height z + h it is
    sqrt(r² - (r - h)²) wide. So the keep-out is the union, over heights between z and
    z + r, of the material at or above that height widened by the ball's width there.
    Above the equator the full radius applies."""
    zmax = setup.stock.zmax
    out: list = []
    above: list = []
    # sample at equal steps of the ball's width (not height): each band is then widened
    # by at most r/samples more than it needs, from the tip to the equator
    hs = sorted({r - math.sqrt(max(r * r - (r * k / samples) ** 2, 0.0)) for k in range(samples + 1)},
                reverse=True)
    width = lambda h: r if h >= r else math.sqrt(max(r * r - (r - h) ** 2, 0.0))   # noqa: E731
    higher_h = r
    for h in hs:                                      # top down, so `above` accumulates
        zz = z + h
        if zz <= zmax:
            above = _boolean(above, setup.slice_region(zz), pyclipper.CT_UNION)
        if above:
            # material at heights between this sample and the next one up is in `above`
            # too; the ball is widest at the top of that band, so widen by that width
            out = _boolean(out, _offset(above, width(higher_h) + leave), pyclipper.CT_UNION)
        higher_h = h
    # material higher than the equator: everything there, widened by the full radius
    higher = []
    for zz in sorted({lv for lv in setup.flats if lv > z + r} | {zmax - 1e-3}, reverse=True):
        higher = _boolean(higher, setup.slice_region(zz), pyclipper.CT_UNION)
    if higher:
        out = _boolean(out, _offset(higher, r + leave), pyclipper.CT_UNION)
    return out


def _rings(op: Operation, forbidden, setup: "Setup", r: float, diameter: float, z: float | None = None):
    """Closed paths for one level, in cutting order."""
    ball = setup.job.tool(op.tool).shape == "ball"
    if op.type in ("finish", "profile") and ball:
        keep = ball_keep_out(setup, z, r, op.leave)
        if op.type == "profile":
            keep = _outermost(keep)
        return [keep] if keep else []
    if op.type == "finish":
        return [_offset(forbidden, r + op.leave)] if forbidden else []
    if op.type == "profile":
        outer = _outermost(_offset(forbidden, r + op.leave))
        return [outer] if outer else []
    region = _boolean(setup.stock_rect(), _offset(forbidden, r + op.leave), pyclipper.CT_DIFFERENCE)
    if op.rest_from is not None:
        region = _rest_region(setup, op, forbidden, region, r)
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


def _rest_region(setup: Setup, op: Operation, forbidden, region, r: float):
    """Tool-centre area that reaches material the earlier tool `op.rest_from` left behind.

    The earlier tool cleared everything its disc swept: its own centre area grown by its
    radius. What is left (minus what this tool must not touch) is the rest material; this
    tool's centres go wherever they would touch it, with a small overlap.
    """
    big = setup.job.tool(op.rest_from)
    R = big.diameter / 2
    big_centres = _boolean(setup.stock_rect(), _offset(forbidden, R + op.leave), pyclipper.CT_DIFFERENCE)
    cleared = _offset(big_centres, R)
    material = _boolean(_boolean(setup.stock_rect(), _offset(forbidden, op.leave), pyclipper.CT_DIFFERENCE),
                        cleared, pyclipper.CT_DIFFERENCE)
    material = _offset(_offset(material, -0.02), 0.02)        # drop slivers thinner than 40 µm
    if not material:
        return []
    return _boolean(region, _offset(material, r + 0.2), pyclipper.CT_INTERSECTION)


def _adaptive(setup: Setup, op: Operation) -> Toolpath:
    """Constant-engagement roughing level by level (see adaptive.py), then a wall pass."""
    from . import adaptive
    tool = setup.job.tool(op.tool)
    r = tool.diameter / 2
    top = setup.stock.zmax
    bottom = setup.part.zmin if op.bottom is None else op.bottom
    levels = cutting_levels(top, bottom, op.stepdown, setup.flats)
    safe = top + setup.job.machine.safe_z
    travel, clearance = top + 1.0, top + 0.5
    tp = Toolpath(op, tool.diameter, levels=levels)
    moves = tp.moves
    moves.append(Move("rapid", None, None, safe))
    res = max(0.05, tool.diameter / 40)
    s = setup.stock
    forbidden: list = []
    for z in levels:
        forbidden = _boolean(forbidden, setup.slice_region(z), pyclipper.CT_UNION)
        keep_out = _offset(forbidden, r + op.leave)
        # the centre may leave the stock (to start in the open), never come near the part;
        # shrunk by a cell so grid rounding cannot step over the line
        allowed = _boolean(_offset(setup.stock_rect(), r + 2.0), keep_out, pyclipper.CT_DIFFERENCE)
        allowed = _offset(allowed, -res * 1.5)
        material = _boolean(setup.stock_rect(), _offset(forbidden, op.leave), pyclipper.CT_DIFFERENCE)
        # only what a disc on an allowed centre can reach; the wall pass takes the slivers
        material = _boolean(material, _offset(allowed, r - res), pyclipper.CT_INTERSECTION)
        paths, left, (gx0, gy0, gres) = adaptive.clear_level(_to_mm(allowed), _to_mm(material),
                                                             (s.xmin, s.ymin, s.xmax, s.ymax), r, op.stepover, res)
        inside = _offset(allowed, -res)               # a hair inside, for the hop test
        last = None
        for pts, helix in paths:
            if len(pts) < 2:
                continue
            _hop(moves, last, pts[0], inside, clearance, travel)
            last = pts[-1]
            if helix:
                circle = pts[:25]
                ramp = _ramp_and_lap(circle[:-1], clearance, z, op.ramp_angle or 3.0)
                moves += [Move("feed", x, y, zz, op.feed) for x, y, zz in ramp[1:]]
                moves.append(Move("feed", circle[-1][0], circle[-1][1], z, op.feed))
                rest = pts[25:]
            else:
                moves.append(Move("feed", None, None, z, op.plunge))        # in the open
                rest = pts[1:]
            moves += [Move("feed", x, y, None, op.feed) for x, y in rest]
        # Whatever the steered walk left (it gives up on awkward spots) is cleared with
        # ordinary offset rings, so every level is fully cut before the next one: later
        # rapids and hops rely on that.
        if left.any():
            leftover = _cells_to_region(left, gx0, gy0, gres)
            centres = _boolean(_offset(leftover, r), keep_out, pyclipper.CT_DIFFERENCE)
            centres = _boolean(centres, allowed, pyclipper.CT_INTERSECTION)
            while centres:
                for loop in _to_mm(centres):
                    if len(loop) < 2:
                        continue
                    pts = _ramp_and_lap(loop, clearance, z, op.ramp_angle)
                    moves += [Move("rapid", None, None, travel), Move("rapid", pts[0][0], pts[0][1], None),
                              Move("rapid", None, None, pts[0][2])]
                    moves += [Move("feed", x, y, zz, op.feed) for x, y, zz in pts[1:]]
                    last = pts[-1][:2]
                centres = _offset(centres, -op.stepover * tool.diameter)
        # wall pass: the exact boundary, ramped like any other ring
        for loop in _to_mm(_boolean(setup.stock_rect(), keep_out, pyclipper.CT_DIFFERENCE)):
            if len(loop) < 2:
                continue
            pts = _ramp_and_lap(loop, clearance, z, op.ramp_angle)
            _hop(moves, last, pts[0][:2], inside, pts[0][2], travel)
            moves += [Move("feed", x, y, zz, op.feed) for x, y, zz in pts[1:]]
            last = pts[-1][:2]
        clearance = z + 0.5
    moves.append(Move("rapid", None, None, safe))
    return tp


def vcarve_regions(setup: Setup, op: Operation):
    """Closed shapes to carve, as clipper paths: from a sketch of the designed part, or a
    DXF file in the project folder."""
    from pathlib import Path
    from .model import _loop_polygon
    src = op.source.strip()
    if not src:
        raise ValueError("V-carve: set Source to a sketch name or a .dxf file in the project folder")
    if src.lower().endswith(".dxf"):
        from . import dxf
        folder = Path(setup.job_folder) if getattr(setup, "job_folder", None) else Path(".")
        sk = dxf.import_dxf(folder / src)
    else:
        sk = setup.sketches.get(src) if getattr(setup, "sketches", None) else None
        if sk is None:
            raise ValueError(f"V-carve: no sketch called {src!r} in this design")
    loops = [_loop_polygon(sk, loop, 48) for loop in sk.profiles()]
    if not loops:
        raise ValueError(f"V-carve: {src!r} has no closed shapes")
    return _boolean(_to_int(loops), [], pyclipper.CT_UNION, pyclipper.PFT_EVENODD)


def _vcarve(setup: Setup, op: Operation) -> Toolpath:
    """Rings inwards from every outline; a ring at distance d from the outline is cut at
    depth d / tan(half angle), so the V-bit's cone just reaches the outline. Where that
    would go deeper than max_depth the floor is flat, cleared with close rings (a V-bit's
    point cuts only a line)."""
    tool = setup.job.tool(op.tool)
    if tool.shape != "vbit":
        raise ValueError("V-carve needs a V-bit tool (shape = vbit)")
    k = math.tan(math.radians(tool.angle / 2))            # width per depth on each side
    top = setup.stock.zmax
    region = vcarve_regions(setup, op)
    step = 0.1                                             # mm between rings along the walls
    safe = top + setup.job.machine.safe_z
    tp = Toolpath(op, 2 * k * op.max_depth, tool_shape="vbit")
    tp.vbit_k = k
    moves = tp.moves
    moves.append(Move("rapid", None, None, safe))
    d, last_step, refining = 0.0, 0.0, False
    for _ in range(100000):
        rings = _offset(region, -d) if d > 0 else region
        if not rings:
            # stepped past the middle: go back halfway (and keep halving), so the last ring
            # runs along the centre line instead of a whole step short of it
            if last_step > 0.01:
                refining = True
                d -= last_step / 2
                last_step /= 2
                continue
            break
        depth = min(d / k, op.max_depth)
        z = top - depth
        for loop in _to_mm(rings):
            if len(loop) < 2:
                continue
            moves += [Move("rapid", None, None, top + 1.0), Move("rapid", loop[0][0], loop[0][1], None),
                      Move("rapid", None, None, top + 0.5), Move("feed", None, None, z, op.plunge)]
            moves += [Move("feed", x, y, None, op.feed) for x, y in loop[1:] + loop[:1]]
        tp.levels.append(z)
        # at full depth the V-bit's point only cuts a line: rings 2·k·0.05 apart leave
        # ridges no higher than 0.05 mm on the flat floor
        if not refining:
            last_step = step if depth < op.max_depth else max(0.02, 2 * k * 0.05)
        d += last_step
    moves.append(Move("rapid", None, None, safe))
    return tp


def generate(setup: Setup, op: Operation) -> Toolpath:
    tp = _generate(setup, op)
    tool = setup.job.tool(op.tool)
    tp.tool_shape = tool.shape
    if tool.shape == "vbit":
        tp.vbit_k = math.tan(math.radians(tool.angle / 2))
    return tp


def _generate(setup: Setup, op: Operation) -> Toolpath:
    if op.type in ("drill", "bore"):
        return _holes(setup, op)
    if op.type == "adaptive":
        return _adaptive(setup, op)
    if op.type == "parallel":
        from .surface import parallel
        return parallel(setup, op)
    if op.type == "vcarve":
        return _vcarve(setup, op)
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
    stay_low = op.type == "rough" and op.rest_from is None
    for z in levels:
        forbidden = _boolean(forbidden, setup.slice_region(z), pyclipper.CT_UNION)
        allowed = (_boolean(setup.stock_rect(), _offset(forbidden, r + op.leave), pyclipper.CT_DIFFERENCE)
                   if stay_low else [])
        last = None
        for ring in _rings(op, forbidden, setup, r, tool.diameter, z):
            for loop in _to_mm(ring):
                if len(loop) < 2:
                    continue
                pts = _ramp_and_lap(loop, clearance, z, op.ramp_angle)
                if tabs and z < tab_top:
                    pts = _lift_over_tabs(pts, tabs, reach, tab_top)
                _hop(moves, last, pts[0][:2], allowed, pts[0][2], travel)
                for x, y, zz in pts[1:]:
                    moves.append(Move("feed", x, y, zz, op.feed))
                last = pts[-1][:2]
        clearance = z + 0.5
    moves.append(Move("rapid", None, None, safe))
    return tp


def generate_all(setup: Setup) -> list[Toolpath]:
    return [generate(setup, op) for op in setup.job.operations if op.enabled]
