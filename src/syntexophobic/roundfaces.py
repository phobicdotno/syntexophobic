"""Round holes and bosses back from a mesh.

An STL (or 3MF) holds only triangles, so a drilled hole comes in as a ring of thin flat
strips and a round pin as a prism. After the flat faces are merged (geometry._flat_faces),
this finds rings of strips that all run along one axis and whose fold lines sit on one
circle, and swaps each for a true cylinder: the strips become one cylindrical face, a
hole's floor or a boss's top its end disc, and the polygon left in the flat face around it
the cylinder's circle. The faces are rebuilt directly, sharing their edges, with no Boolean
operations, so a plate with dozens of holes takes about a second. Each swap is checked: the
solid has to stay valid and its volume may only change by about what the facets cut off.
Anything that does not pass is left as it was.

Only full rings are done; part-round faces (fillets, rounded corners) stay faceted.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line, GeomAbs_Plane
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import (
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_FORWARD,
    TopAbs_REVERSED,
    TopAbs_VERTEX,
    TopAbs_WIRE,
)
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Shell

MIN_FACETS = 8           # fewer: probably a real polygon (a hex boss stays a hex)
MAX_STEP = math.radians(46)
MAX_FEATURES = 300


@dataclass
class Ring:
    faces: list               # the strips
    caps: list                # a hole's floor / a boss's top: faces closed off by the ring
    axis: tuple               # unit direction
    centre: tuple             # a point on the axis
    radius: float
    t0: float                 # extent along the axis
    t1: float
    hole: bool
    facets: int


def _vec(p):
    return (p.X(), p.Y(), p.Z())


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else None


def _volume(shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _faces(shape) -> list:
    out, exp = [], TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        out.append(TopoDS.Face(exp.Current()))
        exp.Next()
    return out


def _normal(face):
    """Outward normal of a planar face, or None."""
    s = BRepAdaptor_Surface(face)
    if s.GetType() != GeomAbs_Plane:
        return None
    n = _vec(s.Plane().Axis().Direction())
    return tuple(-c for c in n) if face.Orientation() == TopAbs_REVERSED else n


def _centroid(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return _vec(g.CentreOfMass())


def _vertices(shape) -> list:
    out, exp = [], TopExp_Explorer(shape, TopAbs_VERTEX)
    while exp.More():
        out.append(_vec(BRep_Tool.Pnt_s(TopoDS.Vertex(exp.Current()))))
        exp.Next()
    return out


def _fit_circle(pts):
    """Least-squares circle through 2D points: (cx, cy, r, worst deviation)."""
    n = len(pts)
    sx = sum(p[0] for p in pts) / n
    sy = sum(p[1] for p in pts) / n
    u = [(p[0] - sx, p[1] - sy) for p in pts]
    suu = sum(a * a for a, _ in u)
    svv = sum(b * b for _, b in u)
    suv = sum(a * b for a, b in u)
    suuu = sum(a ** 3 for a, _ in u)
    svvv = sum(b ** 3 for _, b in u)
    suvv = sum(a * b * b for a, b in u)
    svuu = sum(b * a * a for a, b in u)
    det = suu * svv - suv * suv
    if abs(det) < 1e-18:
        return None
    rhs1, rhs2 = 0.5 * (suuu + suvv), 0.5 * (svvv + svuu)
    uc = (rhs1 * svv - rhs2 * suv) / det
    vc = (suu * rhs2 - suv * rhs1) / det
    r = math.sqrt(uc * uc + vc * vc + (suu + svv) / n)
    cx, cy = uc + sx, vc + sy
    worst = max(abs(math.hypot(p[0] - cx, p[1] - cy) - r) for p in pts)
    return cx, cy, r, worst


def find_rings(shape) -> list[Ring]:
    """Full rings of flat strips around one axis (see the module docstring)."""
    faces = _faces(shape)
    # every edge with the faces it bounds (shape hashes ignore orientation)
    edges: dict[int, list] = {}
    for i, f in enumerate(faces):
        exp = TopExp_Explorer(f, TopAbs_EDGE)
        while exp.More():
            e = exp.Current()
            slot = edges.setdefault(hash(e), [e, set()])
            slot[1].add(i)
            exp.Next()

    normals = [_normal(f) for f in faces]
    # folds: (face a, face b, unit edge direction, a point on the edge)
    folds: dict[int, list] = {}
    for e, owners in edges.values():
        edge = TopoDS.Edge(e)
        if len(owners) != 2:
            continue
        a, b = owners
        if normals[a] is None or normals[b] is None:
            continue
        cos = _dot(normals[a], normals[b])
        if not (math.cos(MAX_STEP) < cos < math.cos(math.radians(0.5))):
            continue
        c = BRepAdaptor_Curve(edge)
        if c.GetType() != GeomAbs_Line:
            continue
        d = _vec(c.Line().Direction())
        p = _vec(c.Value(c.FirstParameter()))
        folds.setdefault(a, []).append((b, d, p))
        folds.setdefault(b, []).append((a, d, p))

    seen, rings = set(), []
    for start in folds:
        if start in seen:
            continue
        axis = folds[start][0][1]
        group, points, stack = {start}, [], [start]
        while stack:                                   # faces folding along the same axis
            f = stack.pop()
            for g, d, p in folds[f]:
                if abs(abs(_dot(d, axis)) - 1) > 1e-6:
                    continue
                points.append(p)
                if g not in group:
                    group.add(g)
                    stack.append(g)
        seen |= group
        if len(group) < MIN_FACETS or any(abs(_dot(normals[g], axis)) > 1e-6 for g in group):
            continue
        # the fold lines, seen along the axis, on one circle
        e1 = _unit(_cross(axis, (1, 0, 0))) or _unit(_cross(axis, (0, 1, 0)))
        e2 = _cross(axis, e1)
        pts2 = list({(round(_dot(p, e1), 7), round(_dot(p, e2), 7)) for p in points})
        if len(pts2) < MIN_FACETS:
            continue
        fit = _fit_circle(pts2)
        if fit is None:
            continue
        cx, cy, r, worst = fit
        if r < 0.05 or worst > max(1e-4, 1e-3 * r):
            continue
        angles = sorted(math.atan2(y - cy, x - cx) for x, y in pts2)
        gaps = [b - a for a, b in itertools.pairwise(angles)] + [angles[0] + 2 * math.pi - angles[-1]]
        if max(gaps) > MAX_STEP or len(pts2) != len(group):
            continue                                   # not a full ring
        centre = tuple(cx * e1[i] + cy * e2[i] for i in range(3))
        ts = [_dot(v, axis) for g in group for v in _vertices(faces[g])]
        t0, t1 = min(ts), max(ts)
        mid = _centroid(faces[next(iter(group))])
        radial = _sub(mid, centre)
        radial = _sub(radial, tuple(_dot(radial, axis) * c for c in axis))
        hole = _dot(normals[next(iter(group))], radial) < 0
        # caps: flat faces across the axis whose every edge borders the ring
        caps = []
        for i, f in enumerate(faces):
            if i in group or normals[i] is None or abs(abs(_dot(normals[i], axis)) - 1) > 1e-6:
                continue
            exp, inside = TopExp_Explorer(f, TopAbs_EDGE), True
            while exp.More() and inside:
                inside = bool(edges[hash(exp.Current())][1] & group)
                exp.Next()
            if inside:
                caps.append(f)
        rings.append(Ring([faces[g] for g in group], caps, axis, centre, r, t0, t1, hole, len(group)))
    return rings


def _frame(ring: Ring, t: float) -> gp_Ax2:
    base = tuple(ring.centre[i] + t * ring.axis[i] for i in range(3))
    x = _unit(_cross(ring.axis, (1, 0, 0))) or _unit(_cross(ring.axis, (0, 1, 0)))
    return gp_Ax2(gp_Pnt(*base), gp_Dir(*ring.axis), gp_Dir(*x))


def _cylinder(ring: Ring):
    return BRepPrimAPI_MakeCylinder(_frame(ring, ring.t0), ring.radius, ring.t1 - ring.t0).Shape()


def _sliver(ring: Ring) -> float:
    """What the facets cut off the true cylinder: the volume the swap should add (boss)
    or take away (hole)."""
    n = ring.facets
    v = (math.pi - n / 2 * math.sin(2 * math.pi / n)) * ring.radius ** 2 * (ring.t1 - ring.t0)
    return -v if ring.hole else v


def _edges(shape) -> list:
    out, exp = [], TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def _wires(face) -> list:
    out, exp = [], TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        out.append(TopoDS.Wire(exp.Current()))
        exp.Next()
    return out


def _end(ring: Ring, shape) -> float | None:
    """t0 or t1, when every corner of the shape lies at that end of the ring."""
    ts = [_dot(v, ring.axis) for v in _vertices(shape)]
    tol = 1e-6 * max(1.0, ring.radius, abs(ring.t1 - ring.t0))
    for t in (ring.t0, ring.t1):
        if all(abs(x - t) < tol for x in ts):
            return t
    return None


class _Rebuild:
    """The new faces for a set of rings: each ring's strips become one cylinder face, its
    cap the cylinder's own end disc, and the polygon it leaves in a neighbouring flat face
    the cylinder's own circle edge, so the faces share their edges without sewing."""

    def __init__(self, ring: Ring):
        cyl = _cylinder(ring)
        self.side = self.ends = None
        self.circles = {}                  # t -> circle edge shared by side and end
        self.discs = {}
        for f in _faces(cyl):
            if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane:
                self.discs[_end(ring, f)] = f
            else:
                self.side = f
        for e in _edges(self.side):
            if BRepAdaptor_Curve(TopoDS.Edge(e)).GetType() == GeomAbs_Circle:
                self.circles[_end(ring, e)] = TopoDS.Edge(e.Oriented(TopAbs_FORWARD))   # anticlockwise about the axis


def _rebuild(shape, rings: list[Ring]):
    """The shape with these rings made round, or (None, rings that cannot be) when some
    ring's ends are not plain flat faces across the axis."""
    strip = {}                                         # edge hash -> ring index
    drop = set()
    for k, r in enumerate(rings):
        for f in r.faces:
            drop.add(hash(f))
            for e in _edges(f):
                strip[hash(e)] = k
        drop |= {hash(f) for f in r.caps}
    news = [_Rebuild(r) for r in rings]
    covered = [set() for _ in rings]
    bad, faces = set(), []
    for f in _faces(shape):
        if hash(f) in drop:
            continue
        fwd = TopoDS.Face(f.Oriented(TopAbs_FORWARD))
        wires = _wires(fwd)
        hits = [{strip.get(hash(e)) for e in _edges(w)} for w in wires]
        if all(h == {None} for h in hits):
            faces.append(f)
            continue
        n = _normal(fwd)
        outer = BRepTools.OuterWire_s(fwd)
        rebuilt = []
        for w, h in zip(wires, hits):
            if h == {None}:
                rebuilt.append(w)
                continue
            if None in h or len(h) > 1:                # a polygon that is only partly the ring
                bad |= h - {None}
                continue
            k = h.pop()
            t = _end(rings[k], w)
            if n is None or abs(abs(_dot(n, rings[k].axis)) - 1) > 1e-6 or t is None:
                bad.add(k)
                continue
            covered[k].add(t)
            # a hole in a face runs clockwise seen from its normal, the outline anticlockwise
            ccw = (_dot(n, rings[k].axis) > 0) == w.IsSame(outer)
            e = news[k].circles[t]
            rebuilt.append(BRepBuilderAPI_MakeWire(e if ccw else TopoDS.Edge(e.Reversed())).Wire())
        face = TopoDS_Face()
        builder = BRep_Builder()
        builder.MakeFace(face, BRep_Tool.Surface_s(f), TopLoc_Location(), BRep_Tool.Tolerance_s(f))
        for w in rebuilt:
            builder.Add(face, w)
        faces.append(TopoDS.Face(face.Oriented(f.Orientation())))
    for k, r in enumerate(rings):
        for cap in r.caps:
            covered[k].add(_end(r, cap))
        if covered[k] != {r.t0, r.t1}:
            bad.add(k)
    if bad:
        return None, bad
    for r, new in zip(rings, news):
        extra = [new.side] + [new.discs[_end(r, c)] for c in r.caps]
        faces += [TopoDS.Face(f.Reversed()) if r.hole else f for f in extra]
    shell, builder = TopoDS_Shell(), BRep_Builder()
    builder.MakeShell(shell)
    for f in faces:
        builder.Add(shell, f)
    solid = BRepBuilderAPI_MakeSolid(shell).Solid()
    if solid.IsNull():
        return None, set(range(len(rings)))
    return solid, set()


def _swap(shape, rings: list[Ring]):
    """The shape with these rings made round, or None when that does not work out: the
    result has to be a valid solid whose volume changed by about what the facets cut off."""
    while rings:
        out, bad = _rebuild(shape, rings)
        if out is not None:
            break
        rings = [r for k, r in enumerate(rings) if k not in bad]
    if not rings:
        return None, 0
    if not BRepCheck_Analyzer(out).IsValid():
        return None, 0
    expected = sum(_sliver(r) for r in rings)
    slack = 0.5 * sum(abs(_sliver(r)) for r in rings) + 1e-6 * abs(_volume(shape)) + 1e-9
    if abs(_volume(out) - _volume(shape) - expected) > slack:
        return None, 0
    return out, len(rings)


def round_faces(shape: TopoDS_Shape) -> tuple[TopoDS_Shape, int]:
    """The shape with every faceted hole and boss it can find made truly round, and how many.
    All at once first; if that fails, one by one, keeping those that work."""
    rings = find_rings(shape)[:MAX_FEATURES]
    if not rings:
        return shape, 0
    out, n = _swap(shape, rings)
    if out is not None:
        return out, n
    done = 0
    for ring in rings:                     # faces of the original shape: find each again
        again = [r for r in find_rings(shape) if _same_ring(r, ring)]
        out, n = _swap(shape, again[:1]) if again else (None, 0)
        if out is not None:
            shape, done = out, done + n
    return shape, done


def _same_ring(a: Ring, b: Ring) -> bool:
    return (abs(a.radius - b.radius) < 1e-6 and abs(a.t0 - b.t0) < 1e-6
            and all(abs(x - y) < 1e-6 for x, y in zip(a.centre, b.centre)))
