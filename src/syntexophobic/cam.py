"""2.5D toolpaths by Z-levels.

At each cutting level the tool may not enter anywhere the part exists at or above that
height (a flat endmill comes in from the top). That forbidden area, grown by the tool
radius, is subtracted from the stock outline, and what is left is cleared with
concentric offset rings. Polygon work is done with Clipper in integer micrometres.
"""

from __future__ import annotations

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

    def __init__(self, shape, job: Job):
        self.shape = shape
        self.job = job
        self.part = geometry.bounding_box(shape)
        m = job.stock.margin
        self.stock = geometry.Box(self.part.xmin - m, self.part.ymin - m, self.part.zmin,
                                  self.part.xmax + m, self.part.ymax + m,
                                  self.part.zmax + job.stock.top_offset)
        self.flats = geometry.upward_flat_levels(shape)
        self._slices: dict[float, list] = {}

    def slice_region(self, z: float):
        key = round(z, 4)
        if key not in self._slices:
            self._slices[key] = _region_from_slices(geometry.slice_at(self.shape, z + EPS))
        return self._slices[key]

    def stock_rect(self):
        s = self.stock
        return _to_int([[(s.xmin, s.ymin), (s.xmax, s.ymin), (s.xmax, s.ymax), (s.xmin, s.ymax)]])


def generate(setup: Setup, op: Operation) -> Toolpath:
    tool = setup.job.tool(op.tool)
    r = tool.diameter / 2
    top = setup.stock.zmax
    bottom = setup.part.zmin if op.bottom is None else op.bottom
    levels = cutting_levels(top, bottom, op.stepdown, setup.flats)
    safe = top + setup.job.machine.safe_z
    tp = Toolpath(op, tool.diameter, levels=levels)
    moves = tp.moves
    moves.append(Move("rapid", None, None, safe))

    # XY rapids happen just above the stock. The tool then drops fast to 0.5 mm above the
    # previous level: each level's region lies inside the one above, so that air is cut.
    travel = top + 1.0
    forbidden: list = []
    clearance = top + 0.5
    for z in levels:
        forbidden = _boolean(forbidden, setup.slice_region(z), pyclipper.CT_UNION)
        if op.type == "finish":
            rings = [_offset(forbidden, r)] if forbidden else []
        else:
            region = _boolean(setup.stock_rect(), _offset(forbidden, r + op.leave),
                              pyclipper.CT_DIFFERENCE)
            rings = []
            while region:
                rings.append(region)
                region = _offset(region, -op.stepover * tool.diameter)
        for ring in rings:
            for loop in _to_mm(ring):
                if len(loop) < 2:
                    continue
                x0, y0 = loop[0]
                moves.append(Move("rapid", None, None, travel))
                moves.append(Move("rapid", x0, y0, None))
                moves.append(Move("rapid", None, None, clearance))
                moves.append(Move("feed", None, None, z, op.plunge))
                for x, y in loop[1:] + [loop[0]]:
                    moves.append(Move("feed", x, y, None, op.feed))
        clearance = z + 0.5
    moves.append(Move("rapid", None, None, safe))
    return tp


def generate_all(setup: Setup) -> list[Toolpath]:
    return [generate(setup, op) for op in setup.job.operations if op.enabled]
