"""DXF import into sketches and export from them (via ezdxf).

Import reads the model space: LINE, ARC, CIRCLE, LWPOLYLINE / POLYLINE (bulges become
arcs), SPLINE (through its fit points, or sampled from its control points) and ELLIPSE
(sampled into a spline). End points that touch are merged into one sketch point, so an
outline drawn as separate segments becomes a closed profile. Inch drawings are scaled to
millimetres from the header's $INSUNITS.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.math import bulge_to_arc

from .sketch import Sketch

UNITS_TO_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}   # unitless, in, ft, mm, cm, m


class _Points:
    """Point ids by position, so touching end points become one point."""

    def __init__(self, sk: Sketch, tol: float = 1e-6):
        self.sk, self.tol, self.grid = sk, tol, {}

    def get(self, x: float, y: float) -> str:
        key = (round(x / self.tol), round(y / self.tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                pid = self.grid.get((key[0] + dx, key[1] + dy))
                if pid is not None:
                    return pid
        pid = self.sk.point(x, y)
        self.grid[key] = pid
        return pid


def import_dxf(path: str | Path, name: str = "imported", plane: str = "XY", offset: float = 0.0) -> Sketch:
    doc = ezdxf.readfile(str(path))
    scale = UNITS_TO_MM.get(doc.header.get("$INSUNITS", 4), 1.0)
    sk = Sketch(name=name, plane=plane, offset=offset)
    pts = _Points(sk, tol=1e-4)

    def P(v):
        return pts.get(v[0] * scale, v[1] * scale)

    def arc_from(center, radius, a0, a1):
        c = (center[0] * scale, center[1] * scale)
        r = radius * scale
        s = pts.get(c[0] + r * math.cos(a0), c[1] + r * math.sin(a0))
        e = pts.get(c[0] + r * math.cos(a1), c[1] + r * math.sin(a1))
        sk.arc(sk.point(*c, construction=True), s, e)

    for e in doc.modelspace():
        kind = e.dxftype()
        # circles, arcs and 2D polylines are in object coordinates; mirrored ones (extrusion
        # 0,0,-1, common from other CAD programs) have x flipped and run clockwise in the world
        flip = kind in ("CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE") and e.dxf.get("extrusion", (0, 0, 1))[2] < 0
        if kind == "LINE":
            a, b = P(e.dxf.start), P(e.dxf.end)
            if a != b:
                sk.line(a, b)
        elif kind == "CIRCLE":
            c = e.dxf.center
            if flip:
                c = (-c[0], c[1])
            sk.circle(sk.point(c[0] * scale, c[1] * scale, construction=True), e.dxf.radius * scale)
        elif kind == "ARC":
            c, a0, a1 = e.dxf.center, math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if flip:
                c, a0, a1 = (-c[0], c[1]), math.pi - a1, math.pi - a0
            arc_from(c, e.dxf.radius, a0, a1)
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            if kind == "LWPOLYLINE":
                verts = [(x, y, b) for x, y, _s, _e, b in e.get_points("xyseb")]
                closed = e.closed
            else:
                verts = [(v.dxf.location[0], v.dxf.location[1], v.dxf.bulge) for v in e.vertices]
                closed = e.is_closed
            if flip:
                verts = [(-x, y, -b) for x, y, b in verts]
            n = len(verts)
            for i in range(n if closed else n - 1):
                x0, y0, bulge = verts[i]
                x1, y1, _ = verts[(i + 1) % n]
                if abs(bulge) < 1e-12:
                    a, b = P((x0, y0)), P((x1, y1))
                    if a != b:
                        sk.line(a, b)
                else:
                    # ezdxf gives the arc counter-clockwise from a0 to a1 for either sign of
                    # bulge (it swaps the ends for clockwise ones), like our arcs
                    center, a0, a1, r = bulge_to_arc((x0, y0), (x1, y1), bulge)
                    arc_from(center, r, a0, a1)
        elif kind == "SPLINE":
            fit = list(e.fit_points)
            if len(fit) < 2:
                fit = [p for p in e.flattening(0.01)]
            ids = [P(p) for p in fit]
            closed = e.closed or (len(ids) > 2 and ids[0] == ids[-1])
            if closed and ids[0] == ids[-1]:
                ids = ids[:-1]
            if len(ids) >= (3 if closed else 2):
                sk.spline(ids, closed=closed)
        elif kind == "ELLIPSE":
            samples = [P(p) for p in e.flattening(0.01)]
            closed = samples[0] == samples[-1]
            if closed:
                samples = samples[:-1]
            sk.spline(samples, closed=closed)
    return sk


def export_dxf(sk: Sketch, path: str | Path) -> None:
    """Non-construction geometry of a sketch to a DXF in millimetres (R2010)."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    P = sk.points
    for ln in sk.lines.values():
        if not ln.construction:
            msp.add_line((P[ln.a].x, P[ln.a].y), (P[ln.b].x, P[ln.b].y))
    for c in sk.circles.values():
        if not c.construction:
            msp.add_circle((P[c.center].x, P[c.center].y), c.r)
    for a in sk.arcs.values():
        if not a.construction:
            c, s, e = P[a.center], P[a.start], P[a.end]
            msp.add_arc((c.x, c.y), a.r, math.degrees(math.atan2(s.y - c.y, s.x - c.x)),
                        math.degrees(math.atan2(e.y - c.y, e.x - c.x)))
    for sp in sk.splines.values():
        if not sp.construction:
            fit = [(P[p].x, P[p].y) for p in sp.points]
            if sp.closed:
                fit.append(fit[0])
            msp.add_spline(fit_points=fit)
    doc.saveas(str(path))
