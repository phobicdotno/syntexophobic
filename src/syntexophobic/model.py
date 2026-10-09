"""Parametric 3D parts: a list of features built from sketches, evaluated with OpenCascade.

The part lives in model.toml next to its sketches (sketches/<name>.toml). Each feature
is one table, so a design change is a small readable diff in the project's git history:

    [[features]]
    type = "extrude"           # extrude | revolve | fillet | chamfer | shell | mirror
    sketch = "base"
    distance = 20.0
    operation = "new"          # new | join | cut | intersect

Edges and faces are picked with short selectors instead of fragile ids:

    all      every edge
    |Z       edges parallel to Z (also |X, |Y)
    #Z       edges perpendicular to Z, i.e. horizontal (also #X, #Y)
    >Z <Z    edges on the topmost / bottommost faces (also X, Y); for shell: that face
    %LINE %CIRCLE   by curve type
    A&B      both selectors (e.g. ">Z&%CIRCLE")
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line, GeomAbs_Plane
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.collections import List_TopoDS_Shape
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

from . import geometry
from .sketch import Sketch

PLANES = {           # origin is the global origin; (x axis, y axis, normal) in model space
    "XY": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "XZ": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "YZ": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}


class ModelError(RuntimeError):
    pass


# ---- sketch -> faces --------------------------------------------------------------------

def _to3d(sk: Sketch, x: float, y: float) -> gp_Pnt:
    u, v, n = PLANES[sk.plane]
    o = sk.offset
    return gp_Pnt(*(x * u[i] + y * v[i] + o * n[i] for i in range(3)))


def _normal(sk: Sketch) -> gp_Dir:
    return gp_Dir(*PLANES[sk.plane][2])


def spline_curve(sk: Sketch, sp, to3d: bool = False):
    """Interpolating cubic B-spline through the spline's points (2D, or placed in 3D)."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt as TColgp_HArray1OfPnt
    pts = [sk.points[p] for p in sp.points]
    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, p in enumerate(pts, 1):
        arr.SetValue(i, _to3d(sk, p.x, p.y) if to3d else gp_Pnt(p.x, p.y, 0))
    interp = GeomAPI_Interpolate(arr, bool(sp.closed), 1e-6)
    interp.Perform()
    if not interp.IsDone():
        raise ModelError(f"Spline {sp.id} could not be built (points too close together?)")
    return interp.Curve()


def _edges(sk: Sketch, loop):
    P = sk.points
    out = []
    for seg in loop:
        if seg[0] == "line":
            a, b = P[seg[1]], P[seg[2]]
            out.append(BRepBuilderAPI_MakeEdge(_to3d(sk, a.x, a.y), _to3d(sk, b.x, b.y)).Edge())
        elif seg[0] == "circle":
            c = sk.circles[seg[1]]
            ctr = P[c.center]
            circ = gp_Circ(gp_Ax2(_to3d(sk, ctr.x, ctr.y), _normal(sk)), c.r)
            out.append(BRepBuilderAPI_MakeEdge(circ).Edge())
        elif seg[0] == "spline":
            sp = sk.splines[seg[1]]
            crv = spline_curve(sk, sp, to3d=True)
            out.append(BRepBuilderAPI_MakeEdge(crv).Edge())
        else:
            a = sk.arcs[seg[1]]
            ctr, s, e = P[a.center], P[a.start], P[a.end]
            circ = gp_Circ(gp_Ax2(_to3d(sk, ctr.x, ctr.y), _normal(sk)), a.r)
            arc = GC_MakeArcOfCircle(circ, _to3d(sk, s.x, s.y), _to3d(sk, e.x, e.y), True).Value()
            out.append(BRepBuilderAPI_MakeEdge(arc).Edge())
    return out


def _segment_points(sk: Sketch, seg, n: int):
    P = sk.points
    if seg[0] == "line":
        return [(P[seg[1]].x, P[seg[1]].y), (P[seg[2]].x, P[seg[2]].y)]
    if seg[0] == "spline":
        return sk.spline_samples(seg[1], n)
    if seg[0] == "circle":
        c = sk.circles[seg[1]]
        cx, cy = P[c.center].x, P[c.center].y
        return [(cx + c.r * math.cos(2 * math.pi * k / n), cy + c.r * math.sin(2 * math.pi * k / n))
                for k in range(n + 1)]
    a = sk.arcs[seg[1]]
    cx, cy = P[a.center].x, P[a.center].y
    t0 = math.atan2(P[a.start].y - cy, P[a.start].x - cx)
    t1 = math.atan2(P[a.end].y - cy, P[a.end].x - cx)
    span = (t1 - t0) % (2 * math.pi) or 2 * math.pi
    return [(cx + a.r * math.cos(t0 + span * k / n), cy + a.r * math.sin(t0 + span * k / n)) for k in range(n + 1)]


def _loop_polygon(sk: Sketch, loop, n_arc: int = 32):
    """2D polygon of a profile loop, for area and containment tests. Each segment is
    turned around when needed so it continues from where the previous one ended."""
    pts: list = []
    for seg in loop:
        seg_pts = _segment_points(sk, seg, n_arc)
        if pts and math.dist(pts[-1], seg_pts[-1]) < math.dist(pts[-1], seg_pts[0]):
            seg_pts = seg_pts[::-1]
        elif len(pts) == 0 and len(loop) > 1:
            nxt = _segment_points(sk, loop[1], 2)
            far = min(math.dist(seg_pts[0], q) for q in (nxt[0], nxt[-1]))
            near = min(math.dist(seg_pts[-1], q) for q in (nxt[0], nxt[-1]))
            if far < near:                   # the first segment's start touches the second
                seg_pts = seg_pts[::-1]
        pts += seg_pts[:-1] if len(loop) > 1 or seg[0] in ("circle", "spline") else seg_pts
    return pts


def _area(poly) -> float:
    return 0.5 * sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly)))


def _inside(pt, poly) -> bool:
    x, y = pt
    hit = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i - 1], poly[i]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _first_edge(wire):
    from OCP.BRepTools import BRepTools_WireExplorer
    return BRepTools_WireExplorer(wire).Current()


def _last_edge(wire):
    from OCP.BRepTools import BRepTools_WireExplorer
    exp, last = BRepTools_WireExplorer(wire), None
    while exp.More():
        last = exp.Current()
        exp.Next()
    return last


def sketch_faces(sk: Sketch) -> list:
    """Planar faces from the sketch's closed profiles; loops inside others become holes."""
    loops = sk.profiles()
    if not loops:
        raise ModelError(f"Sketch {sk.name!r} has no closed profile")
    polys = [_loop_polygon(sk, l) for l in loops]
    order = sorted(range(len(loops)), key=lambda i: -abs(_area(polys[i])))
    depth = {i: sum(1 for j in order if j != i and abs(_area(polys[j])) > abs(_area(polys[i]))
                    and _inside(polys[i][0], polys[j])) for i in order}
    faces = []
    for i in order:
        if depth[i] % 2:
            continue                                    # a hole: added to its outer face
        wire = BRepBuilderAPI_MakeWire()
        for e in _edges(sk, loops[i]):
            wire.Add(e)
        mk = BRepBuilderAPI_MakeFace(wire.Wire(), True)
        for j in order:
            if depth[j] == depth[i] + 1 and _inside(polys[j][0], polys[i]):
                hw = BRepBuilderAPI_MakeWire()
                for e in _edges(sk, loops[j]):
                    hw.Add(e)
                mk.Add(TopoDS.Wire(hw.Wire().Reversed()))
        if not mk.IsDone():
            raise ModelError(f"Could not make a face from sketch {sk.name!r}")
        from OCP.ShapeFix import ShapeFix_Face
        fix = ShapeFix_Face(mk.Face())
        fix.Perform()
        faces.append(fix.Face())
    return faces


# ---- selectors -----------------------------------------------------------------------------

def _items(shape, kind):
    """Unique edges or faces."""
    return geometry.unique(shape, kind)


PICK_TOL = 0.75     # mm: how far a stored @x,y,z pick may be from the edge or face it means


def _nearest(items, point):
    """The item closest to point, if it is within PICK_TOL."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
    best, bd = None, PICK_TOL
    for it in items:
        d = BRepExtrema_DistShapeShape(v, it)
        if d.IsDone() and d.Value() < bd:
            best, bd = it, d.Value()
    return best


def _picks(selector: str):
    """'@1,2,3;@4,5,6' -> [(1, 2, 3), (4, 5, 6)]"""
    return [tuple(float(v) for v in tok.strip()[1:].split(",")) for tok in selector.split(";") if tok.strip()]


def pick_token(point) -> str:
    return "@" + ",".join(f"{v:.3f}".rstrip("0").rstrip(".") for v in point)


AXES = {"X": 0, "Y": 1, "Z": 2}


def select_edges(shape, selector: str) -> list:
    edges = _items(shape, TopAbs_EDGE)
    if selector.strip().startswith("@"):                    # picked in the 3D view
        found = []
        for pt in _picks(selector):
            e = _nearest(edges, pt)
            if e is None:
                raise ModelError(f"No edge near {pt} any more; pick the edges again")
            if not any(e.IsSame(x) for x in found):
                found.append(e)
        return found
    result = None
    for part in selector.split("&"):
        part = part.strip()
        if part == "all":
            chosen = edges
        elif part[0] in "|#" and part[1:] in AXES:
            k = AXES[part[1:]]
            chosen = []
            for e in edges:
                c = BRepAdaptor_Curve(e)
                if c.GetType() == GeomAbs_Line:
                    d = c.Line().Direction().Coord(k + 1)
                    if (part[0] == "|" and abs(d) > 0.999) or (part[0] == "#" and abs(d) < 1e-3):
                        chosen.append(e)
                elif part[0] == "#" and c.GetType() == GeomAbs_Circle and \
                        abs(c.Circle().Axis().Direction().Coord(k + 1)) > 0.999:
                    chosen.append(e)                     # circles lying flat count as horizontal
        elif part[0] in "<>" and part[1:] in AXES:
            faces = select_faces(shape, part)
            ids = {id(None)}
            chosen = []
            for f in faces:
                for fe in _items(f, TopAbs_EDGE):
                    for e in edges:
                        if e.IsSame(fe) and not any(x.IsSame(e) for x in chosen):
                            chosen.append(e)
        elif part.upper() in ("%LINE", "%CIRCLE"):
            want = GeomAbs_Line if part.upper() == "%LINE" else GeomAbs_Circle
            chosen = [e for e in edges if BRepAdaptor_Curve(e).GetType() == want]
        else:
            raise ModelError(f"Unknown edge selector {part!r}")
        result = chosen if result is None else [e for e in result if any(e.IsSame(c) for c in chosen)]
    return result or []


def select_faces(shape, selector: str) -> list:
    """>Z: the planar face(s) furthest along +Z; <Z the furthest along -Z; @x,y,z picks."""
    if selector.strip().startswith("@"):
        faces = _items(shape, TopAbs_FACE)
        found = []
        for pt in _picks(selector):
            f = _nearest(faces, pt)
            if f is None:
                raise ModelError(f"No face near {pt} any more; pick the faces again")
            found.append(f)
        return found
    sign, k = (1 if selector[0] == ">" else -1), AXES[selector[1:]]
    best, faces = None, []
    for f in _items(shape, TopAbs_FACE):
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Plane:
            continue
        b = geometry.bounding_box(f)
        lo, hi = (b.xmin, b.ymin, b.zmin)[k], (b.xmax, b.ymax, b.zmax)[k]
        if hi - lo > 1e-6:
            continue                                     # not perpendicular to the axis
        pos = sign * hi
        if best is None or pos > best + 1e-6:
            best, faces = pos, [f]
        elif abs(pos - best) <= 1e-6:
            faces.append(f)
    return faces


# ---- features --------------------------------------------------------------------------------

@dataclass
class Feature:
    type: str
    name: str = ""
    sketch: str = ""             # extrude / revolve / sweep profile; loft: "a;b;c" bottom to top
    path: str = ""               # sweep: sketch holding one chain of lines and arcs
    distance: float = 10.0       # extrude: along the sketch normal (negative = other way)
    symmetric: bool = False      # extrude: half each side of the sketch plane
    operation: str = "new"       # new | join | cut | intersect
    axis: str = ""               # revolve: line id in the sketch
    angle: float = 360.0         # revolve
    radius: float = 1.0          # fillet
    edges: str = "|Z"            # fillet / chamfer selector
    faces: str = ">Z"            # shell: faces to open
    thickness: float = 2.0       # shell (negative = outward)
    plane: str = "YZ"            # mirror: through the origin
    count: int = 1               # pattern of this feature's tool body (extrude / revolve)
    spacing: float = 10.0        # linear pattern step, along `direction`
    direction: str = "X"         # X | Y | Z, or "circular" (about Z through the origin)
    suppressed: bool = False


@dataclass
class Part:
    name: str = "Part"
    features: list[Feature] = field(default_factory=list)
    sketches: dict[str, Sketch] = field(default_factory=dict)

    # ---- evaluation -------------------------------------------------------------------

    def build(self, upto: int | None = None) -> TopoDS_Shape:
        """Evaluate features in order (all, or the first `upto`) into one solid."""
        shape = None
        for i, f in enumerate(self.features[:upto]):
            if f.suppressed:
                continue
            try:
                shape = self._apply(shape, f)
            except ModelError:
                raise
            except Exception as e:  # OpenCascade raises plain RuntimeErrors / Standard_Failure
                raise ModelError(f"Feature {i + 1} ({f.name or f.type}) failed: {e}") from e
        if shape is None:
            raise ModelError("The part has no features yet")
        return shape

    def _solved(self, name: str) -> Sketch:
        if name not in self.sketches:
            raise ModelError(f"No sketch called {name!r}")
        sk = self.sketches[name]
        res = sk.solve()
        if not res.ok:
            raise ModelError(f"Sketch {name!r} has conflicting constraints: {', '.join(res.conflicting)}")
        return sk

    def _loft(self, f: Feature):
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.BRepTools import BRepTools
        names = [n.strip() for n in f.sketch.split(";") if n.strip()]
        if len(names) < 2:
            raise ModelError("Loft needs two or more sketches: sketch = \"bottom;top\"")
        loft = BRepOffsetAPI_ThruSections(True, False)
        for n in names:
            loft.AddWire(BRepTools.OuterWire_s(sketch_faces(self._solved(n))[0]))
        loft.Build()
        if not loft.IsDone():
            raise ModelError("Loft failed: the profiles may twist or cross")
        return loft.Shape()

    def _sweep(self, f: Feature):
        from OCP.BRepGProp import BRepGProp
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from OCP.GProp import GProp_GProps
        profile = sketch_faces(self._solved(f.sketch))[0]
        path_sk = self._solved(f.path)
        try:
            segs = path_sk.path_segments()
        except ValueError as e:
            raise ModelError(str(e)) from e
        wire = BRepBuilderAPI_MakeWire()
        for e in _edges(path_sk, segs):
            wire.Add(e)
        spine = wire.Wire()
        # start the sweep at the end of the path the profile sits on
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(profile, props)
        c = props.CentreOfMass()
        first = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(_first_edge(spine), True))
        last = BRep_Tool.Pnt_s(TopExp.LastVertex_s(_last_edge(spine), True))
        if c.Distance(last) < c.Distance(first):
            spine = TopoDS.Wire(spine.Reversed())
        pipe = BRepOffsetAPI_MakePipe(spine, profile)
        pipe.Build()
        if not pipe.IsDone():
            raise ModelError("Sweep failed: check the profile sits at one end of the path")
        return pipe.Shape()

    def _tool_body(self, f: Feature):
        if f.type == "loft":
            return self._loft(f)
        if f.type == "sweep":
            return self._sweep(f)
        sk = self.sketches[f.sketch]
        res = sk.solve()
        if not res.ok:
            raise ModelError(f"Sketch {f.sketch!r} has conflicting constraints: {', '.join(res.conflicting)}")
        bodies = []
        for face in sketch_faces(sk):
            if f.type == "extrude":
                n = PLANES[sk.plane][2]
                d = f.distance
                body = BRepPrimAPI_MakePrism(face, gp_Vec(*(c * d for c in n))).Shape()
                if f.symmetric:
                    t = gp_Trsf()
                    t.SetTranslation(gp_Vec(*(-c * d / 2 for c in n)))
                    body = BRepBuilderAPI_Transform(body, t, True).Shape()
            else:
                ln = sk.lines[f.axis]
                a, b = sk.points[ln.a], sk.points[ln.b]
                pa, pb = _to3d(sk, a.x, a.y), _to3d(sk, b.x, b.y)
                axis = gp_Ax1(pa, gp_Dir(gp_Vec(pa, pb)))
                body = BRepPrimAPI_MakeRevol(face, axis, math.radians(f.angle)).Shape()
            bodies.append(body)
        body = bodies[0]
        for b in bodies[1:]:
            body = BRepAlgoAPI_Fuse(body, b).Shape()
        if f.count > 1:
            copies = [body]
            for k in range(1, f.count):
                t = gp_Trsf()
                if f.direction == "circular":
                    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2 * math.pi * k / f.count)
                else:
                    v = [0.0, 0.0, 0.0]
                    v[AXES[f.direction]] = f.spacing * k
                    t.SetTranslation(gp_Vec(*v))
                copies.append(BRepBuilderAPI_Transform(body, t, True).Shape())
            body = copies[0]
            for c in copies[1:]:
                body = BRepAlgoAPI_Fuse(body, c).Shape()
        return body

    def _apply(self, shape, f: Feature):
        if f.type in ("extrude", "revolve", "loft", "sweep"):
            body = self._tool_body(f)
            if shape is None or f.operation == "new":
                return body if shape is None else BRepAlgoAPI_Fuse(shape, body).Shape()
            op = {"join": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}[f.operation]
            return op(shape, body).Shape()
        if shape is None:
            raise ModelError(f"{f.type} needs a body before it")
        if f.type in ("fillet", "chamfer"):
            edges = select_edges(shape, f.edges)
            if not edges:
                raise ModelError(f"{f.type}: selector {f.edges!r} matches no edges")
            mk = BRepFilletAPI_MakeFillet(shape) if f.type == "fillet" else BRepFilletAPI_MakeChamfer(shape)
            for e in edges:
                mk.Add(f.radius, e)
            return mk.Shape()
        if f.type == "shell":
            faces = List_TopoDS_Shape()
            for face in select_faces(shape, f.faces):
                faces.Append(face)
            mk = BRepOffsetAPI_MakeThickSolid()
            mk.MakeThickSolidByJoin(shape, faces, -f.thickness, 1e-3)
            return mk.Shape()
        if f.type == "mirror":
            t = gp_Trsf()
            normal = {"YZ": (1, 0, 0), "XZ": (0, 1, 0), "XY": (0, 0, 1)}[f.plane]
            t.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*normal)))
            return BRepAlgoAPI_Fuse(shape, BRepBuilderAPI_Transform(shape, t, True).Shape()).Shape()
        raise ModelError(f"Unknown feature type {f.type!r}")

    # ---- files ----------------------------------------------------------------------------

    def save(self, folder: str | Path, filename: str = "model.toml") -> None:
        folder = Path(folder)
        (folder / "sketches").mkdir(parents=True, exist_ok=True)
        defaults = asdict(Feature(type=""))
        out = ["[part]", f'name = "{self.name}"', ""]
        for f in self.features:
            out.append("[[features]]")
            for k, v in asdict(f).items():
                if k == "type" or v != defaults[k]:     # only what differs: short, readable diffs
                    out.append(f"{k} = " + ('"' + v + '"' if isinstance(v, str) else
                                             ("true" if v else "false") if isinstance(v, bool) else repr(v)))
            out.append("")
        (folder / filename).write_text("\n".join(out))
        for name, sk in self.sketches.items():
            sk.name = name
            sk.save(folder / "sketches" / f"{name}.toml")

    @classmethod
    def load(cls, path: str | Path) -> "Part":
        path = Path(path)
        d = tomllib.loads(path.read_text())
        part = cls(name=d.get("part", {}).get("name", "Part"),
                   features=[Feature(**f) for f in d.get("features", [])])
        for f in sorted((path.parent / "sketches").glob("*.toml")):
            part.sketches[f.stem] = Sketch.load(f)
        return part

    def export_step(self, path: str | Path) -> None:
        w = STEPControl_Writer()
        w.Transfer(self.build(), STEPControl_AsIs)
        w.Write(str(path))
