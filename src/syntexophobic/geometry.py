"""Model geometry: STEP loading, bounding boxes, Z-slices and meshes, on top of OpenCascade."""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Ax1, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

Point = tuple[float, float]
Loop = list[Point]


@dataclass(frozen=True)
class Box:
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float


def load_step(path: str) -> TopoDS_Shape:
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError(f"Could not read STEP file: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def solid_from_mesh(mesh, what: str = "mesh") -> TopoDS_Shape:
    """A closed, indexed Poly_Triangulation as a faceted solid. Faces built on one indexed
    mesh already share their edges, so a shell of them is closed without sewing (which
    takes minutes on a fine mesh)."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeShapeOnMesh, BRepBuilderAPI_MakeSolid
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopoDS import TopoDS_Shell
    faces = BRepBuilderAPI_MakeShapeOnMesh(mesh).Shape()
    shell, builder = TopoDS_Shell(), BRep_Builder()
    builder.MakeShell(shell)
    exp = TopExp_Explorer(faces, TopAbs_FACE)
    while exp.More():
        builder.Add(shell, exp.Current())
        exp.Next()
    solid = BRepBuilderAPI_MakeSolid(shell).Solid()
    if solid.IsNull():
        raise ValueError(f"{what}: could not make a solid")
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, props)
    if props.Mass() < 0:                    # triangles wound inwards: turn it inside out
        solid.Reverse()
    return solid


def load_stl(path: str, merge_flat: bool = True) -> TopoDS_Shape:
    """An STL file as a solid. Coincident corners are merged on reading; with merge_flat,
    triangles lying in one plane become one face (a box comes out with 6 faces, not 12
    triangles), which CAD programs and the CAM's flat detection like better."""
    from OCP.Message import Message_ProgressRange
    from OCP.RWStl import RWStl
    mesh = RWStl.ReadFile_s(str(path), Message_ProgressRange())
    if mesh is None or mesh.NbTriangles() == 0:
        raise ValueError(f"Could not read STL file: {path}")
    return _flat_faces(solid_from_mesh(mesh, str(path)), mesh.NbTriangles(), merge_flat)


def _flat_faces(solid, triangles: int, merge_flat: bool = True):
    """Triangles lying in one plane merged into one face (skipped on huge meshes: slow)."""
    if merge_flat and triangles <= 200_000:
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
        u = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
        u.Build()
        if not u.Shape().IsNull():
            solid = u.Shape()
    return solid


MESH_SUFFIXES = (".stl", ".3mf")


def load_3mf(path: str, merge_flat: bool = True) -> TopoDS_Shape:
    """A 3MF file (Bambu Studio, PrusaSlicer, Cura, Windows 3D Builder) as solids: one per
    object placed on the build plate, where the file puts it, in millimetres. Flat areas
    become single faces, as for STL. Several objects come back as one compound."""
    from OCP.BRep import BRep_Builder
    from OCP.Poly import Poly_Triangle, Poly_Triangulation
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Pnt
    from .mesh3mf import read_3mf
    objects = read_3mf(path)
    if not objects:
        raise ValueError(f"No printable objects in {path}")
    solids = []
    for name, vertices, triangles in objects:
        for part in _connected(triangles):          # an object of several parts: a solid each
            used = sorted({i for t in part for i in t})
            index = {old: new for new, old in enumerate(used, 1)}
            mesh = Poly_Triangulation(len(used), len(part), False)
            for old, new in index.items():
                mesh.SetNode(new, gp_Pnt(*vertices[old]))
            for i, (a, b, c) in enumerate(part, 1):
                mesh.SetTriangle(i, Poly_Triangle(index[a], index[b], index[c]))
            solids.append(_flat_faces(solid_from_mesh(mesh, f"{path}: {name}"), len(part), merge_flat))
    if len(solids) == 1:
        return solids[0]
    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for s in solids:
        builder.Add(compound, s)
    return compound


def _connected(triangles: list[tuple[int, int, int]]) -> list[list[tuple[int, int, int]]]:
    """Triangles grouped into pieces that share corners."""
    parent: dict[int, int] = {}

    def find(i):
        while parent.setdefault(i, i) != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in triangles:
        ra = find(a)
        for v in (b, c):
            rv = find(v)
            if rv != ra:
                parent[rv] = ra
    groups: dict[int, list] = {}
    for t in triangles:
        groups.setdefault(find(t[0]), []).append(t)
    return list(groups.values())


def load_mesh(path: str, merge_flat: bool = True) -> TopoDS_Shape:
    return load_3mf(path, merge_flat) if str(path).lower().endswith(".3mf") else load_stl(path, merge_flat)


def load_model(path: str) -> TopoDS_Shape:
    """STEP, STL or 3MF, by extension."""
    return load_mesh(path) if str(path).lower().endswith(MESH_SUFFIXES) else load_step(path)


def quiet() -> None:
    """Only failures from OpenCascade's messenger: the STEP writer prints its statistics."""
    from OCP.Message import Message, Message_Gravity
    printers = Message.DefaultMessenger_s().Printers()
    for i in range(1, printers.Size() + 1):
        printers.Value(i).SetTraceLevel(Message_Gravity.Message_Fail)


def stl_to_step(src: str, dst: str, merge_flat: bool = True) -> TopoDS_Shape:
    """An STL or 3MF mesh as a STEP solid."""
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    solid = load_mesh(src, merge_flat)
    quiet()
    w = STEPControl_Writer()
    w.Transfer(solid, STEPControl_AsIs)
    if w.Write(str(dst)) != IFSelect_RetDone:
        raise ValueError(f"Could not write STEP file: {dst}")
    return solid


def bounding_box(shape: TopoDS_Shape) -> Box:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return Box(lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z())


def _edge_points(edge) -> list[Point]:
    curve = BRepAdaptor_Curve(edge)
    u0, u1 = curve.FirstParameter(), curve.LastParameter()
    if curve.GetType() == GeomAbs_Line:
        n = 1
    else:
        length = GCPnts_AbscissaPoint.Length_s(curve)
        n = min(400, max(8, math.ceil(length / 0.2)))
    pts = []
    for i in range(n + 1):
        p = curve.Value(u0 + (u1 - u0) * i / n)
        pts.append((p.X(), p.Y()))
    return pts


def _chain(segments: list[list[Point]], tol: float = 0.01, open_chains: list | None = None) -> list[Loop]:
    """Join loose edge polylines end-to-end into closed loops.

    Endpoints within `tol` are merged into one node, then edges are walked like a graph,
    so outlines that touch at a corner (four edges meeting) still come out as loops.
    """
    nodes: list[Point] = []
    grid: dict[tuple[int, int], list[int]] = {}

    def node(p: Point) -> int:
        gx, gy = math.floor(p[0] / tol), math.floor(p[1] / tol)
        for cx in (gx - 1, gx, gx + 1):
            for cy in (gy - 1, gy, gy + 1):
                for i in grid.get((cx, cy), ()):
                    q = nodes[i]
                    if abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol:
                        return i
        nodes.append(p)
        grid.setdefault((gx, gy), []).append(len(nodes) - 1)
        return len(nodes) - 1

    edges = []                       # (start node, end node, points)
    at: dict[int, list[int]] = {}    # node -> indices of edges touching it
    for seg in segments:
        if len(seg) < 2:
            continue
        a, b = node(seg[0]), node(seg[-1])
        if a == b and len(seg) < 4:
            continue                 # degenerate sliver
        at.setdefault(a, []).append(len(edges))
        at.setdefault(b, []).append(len(edges))
        edges.append((a, b, seg))

    used = [False] * len(edges)
    loops: list[Loop] = []
    for first in range(len(edges)):
        if used[first]:
            continue
        used[first] = True
        start, cur, pts = edges[first][0], edges[first][1], list(edges[first][2])
        while cur != start:
            nxt = next((e for e in at[cur] if not used[e]), None)
            if nxt is None:
                break                # open chain: not a closed outline
            used[nxt] = True
            a, b, seg = edges[nxt]
            pts += seg[1:] if a == cur else seg[-2::-1]
            cur = b if a == cur else a
        if cur == start and len(pts) >= 4:
            loops.append(pts[:-1])
        elif open_chains is not None:
            open_chains.append(pts)
    return loops


def solids(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    found = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        found.append(exp.Current())
        exp.Next()
    return found or [shape]


def slice_at(shape: TopoDS_Shape, z: float) -> list[list[Loop]]:
    """Closed outlines cut by the horizontal plane at height z, one list per solid.

    Solids are kept apart because parts of an assembly may touch or overlap, and their
    outlines only make sense as inside/outside per solid.
    """
    result = []
    for solid in solids(shape):
        loops = slice_solid(solid, z)
        if loops:
            result.append(loops)
    return result


def slice_solid(solid: TopoDS_Shape, z: float) -> list[Loop]:
    """Exact outlines of one solid at height z (OpenCascade section of the B-rep)."""
    section = BRepAlgoAPI_Section(solid, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), False)
    section.Approximation(False)
    section.ComputePCurveOn1(False)
    section.ComputePCurveOn2(False)
    section.Build()
    segments = []
    exp = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
    while exp.More():
        segments.append(_edge_points(TopoDS.Edge(exp.Current())))
        exp.Next()
    return _chain(segments)


UP_AXES = ("+Z", "-Z", "+X", "-X", "+Y", "-Y")


def flip(shape: TopoDS_Shape, cy: float, cz: float) -> TopoDS_Shape:
    """Turn the part over: 180° about the X axis through (y = cy, z = cz), the way it is
    flipped on the machine table for its second side."""
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, cy, cz), gp_Dir(1, 0, 0)), math.pi)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def orient(shape: TopoDS_Shape, up: str) -> TopoDS_Shape:
    """Rotate the model so the given model axis points up (towards the spindle)."""
    if up == "+Z":
        return shape
    rotations = {          # axis of rotation, angle in degrees
        "-Z": ((1, 0, 0), 180), "+X": ((0, 1, 0), -90), "-X": ((0, 1, 0), 90),
        "+Y": ((1, 0, 0), 90), "-Y": ((1, 0, 0), -90),
    }
    (ax, ay, az), deg = rotations[up]
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(ax, ay, az)), math.radians(deg))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def upward_flat_levels(shape: TopoDS_Shape, min_area: float = 1.0) -> list[float]:
    """Z heights of horizontal faces that point up: pocket floors, steps and the top.

    Faces are grouped by height and a level counts only when its flat area adds up to
    min_area mm²: on a finely faceted solid (a relief) almost every small triangle is
    "flat", and each would otherwise become a cutting level of its own."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    area: dict[float, float] = {}
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Plane:
            plane = surf.Plane()
            dz = plane.Axis().Direction().Z()
            if face.Orientation() == TopAbs_REVERSED:
                dz = -dz
            if dz > 0.999:
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                z = round(plane.Location().Z(), 4)
                area[z] = area.get(z, 0.0) + props.Mass()
        exp.Next()
    # heights within 0.05 mm are one level (a blurred relief is "flat" at 3.000, 3.001...),
    # kept at the height with the most flat area; a level needs min_area mm², and at least
    # 0.2 % of the part's footprint
    b = bounding_box(shape)
    need = max(min_area, 0.002 * (b.xmax - b.xmin) * (b.ymax - b.ymin))
    levels, group = [], []
    for z in sorted(area):
        if group and z - group[-1] > 0.05:
            levels.append(group)
            group = []
        group.append(z)
    if group:
        levels.append(group)
    return [max(g, key=lambda z: area[z]) for g in levels if sum(area[z] for z in g) >= need]


@dataclass(frozen=True)
class Hole:
    x: float
    y: float
    diameter: float
    top: float
    bottom: float


def _normal_towards_axis(face, surf, axis) -> bool:
    """True when the face's outward (material-leaving) normal points at the axis: the
    material is around the cylinder, so it is a hole, not a boss."""
    u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
    v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    surf.D1(u, v, p, du, dv)
    n = du.Crossed(dv)
    if face.Orientation() == TopAbs_REVERSED:
        n.Reverse()
    loc = axis.Location()
    return n.X() * (loc.X() - p.X()) + n.Y() * (loc.Y() - p.Y()) > 0


def find_holes(shape: TopoDS_Shape) -> list[Hole]:
    """Vertical round holes: full cylinders with material outside them.

    A hole is often split into two half-cylinder faces, so faces are grouped by axis and
    radius and must cover a full turn together. Partial cylinders (fillets in pocket
    corners) and bosses (material inside) are left out.
    """
    groups: dict[tuple, list] = {}
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            axis = cyl.Axis()
            if abs(axis.Direction().Z()) > 0.999 and _normal_towards_axis(face, surf, axis):
                loc = axis.Location()
                key = (round(loc.X(), 3), round(loc.Y(), 3), round(cyl.Radius(), 3))
                sweep = surf.LastUParameter() - surf.FirstUParameter()
                box = bounding_box(face)
                groups.setdefault(key, []).append((sweep, box.zmin, box.zmax))
        exp.Next()
    holes = []
    for (x, y, r), parts in groups.items():
        if sum(p[0] for p in parts) >= 2 * math.pi - 1e-3:
            holes.append(Hole(x, y, 2 * r, max(p[2] for p in parts), min(p[1] for p in parts)))
    return sorted(holes, key=lambda h: (h.y, h.x))


def unique(shape: TopoDS_Shape, kind) -> list:
    """Edges or faces of a shape, each once (explorers visit shared edges once per face)."""
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
    from OCP.TopExp import TopExp
    m = ShapeMap()
    TopExp.MapShapes_s(shape, kind, m)
    cast = TopoDS.Edge if kind == TopAbs_EDGE else TopoDS.Face
    return [cast(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def edge_polylines(shape: TopoDS_Shape) -> list[list[tuple[float, float, float]]]:
    """Every edge as a 3D polyline, in unique() order (for drawing and picking)."""
    out = []
    for e in unique(shape, TopAbs_EDGE):
        c = BRepAdaptor_Curve(e)
        u0, u1 = c.FirstParameter(), c.LastParameter()
        n = 1 if c.GetType() == GeomAbs_Line else 48
        out.append([(lambda p: (p.X(), p.Y(), p.Z()))(c.Value(u0 + (u1 - u0) * k / n)) for k in range(n + 1)])
    return out


def edge_midpoint(edge) -> tuple[float, float, float]:
    c = BRepAdaptor_Curve(edge)
    p = c.Value((c.FirstParameter() + c.LastParameter()) / 2)
    return p.X(), p.Y(), p.Z()


def face_point(face) -> tuple[float, float, float]:
    """A point on the face (its surface at the middle of its parameter range)."""
    s = BRepAdaptor_Surface(face)
    p = s.Value((s.FirstUParameter() + s.LastUParameter()) / 2, (s.FirstVParameter() + s.LastVParameter()) / 2)
    return p.X(), p.Y(), p.Z()


def mesh(shape: TopoDS_Shape, deflection: float = 0.1, face_ids: list | None = None):
    """Triangle mesh for display: (vertices [(x,y,z)], triangles [(i,j,k)]).

    Pass a list as face_ids to receive, per triangle, the index of its face in
    unique(shape, TopAbs_FACE), for picking.
    """
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    faces = unique(shape, TopAbs_FACE)
    for fi, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            base = len(verts)
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                verts.append((p.X(), p.Y(), p.Z()))
            flip = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i)
                a, b, c = (t.Value(k) - 1 + base for k in (1, 2, 3))
                tris.append((a, c, b) if flip else (a, b, c))
                if face_ids is not None:
                    face_ids.append(fi)
    return verts, tris


# ---- fast slicing on a mesh -------------------------------------------------------------
# Exact B-spline sections take about a second per level on a real part. A fine mesh
# (0.01 mm chordal error, well below what a router holds) sliced with numpy takes
# milliseconds, which is how the toolpaths are computed. slice_at stays as the exact
# reference.


class MeshSlicer:
    """Slices a fine mesh of the model; the triangle maths runs on a compute backend."""

    def __init__(self, shape: TopoDS_Shape, deflection: float = 0.01, backend=None):
        import numpy as np

        from . import compute

        self.backend = backend or compute.get()
        self.solids = []          # (B-rep solid, (n, 3, 3) triangles on the backend device)
        self.fallbacks = 0        # levels where the mesh slice did not close and the exact one was used
        for solid in solids(shape):
            verts, tris = mesh(solid, deflection)
            if tris:
                v = np.asarray(verts, dtype=float)
                self.solids.append((solid, self.backend.asarray(v[np.asarray(tris)])))

    def slice(self, z: float) -> list[list[Loop]]:
        from . import compute

        result = []
        for solid, tri in self.solids:
            segs = compute.slice_segments(self.backend, tri, z)
            if not len(segs):
                continue
            segments = [[(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]
                        for a, b in segs if (a != b).any()]
            open_chains: list = []
            loops = _chain(segments, tol=1e-4, open_chains=open_chains)
            if open_chains:
                self.fallbacks += 1
                loops = slice_solid(solid, z)
            if loops:
                result.append(loops)
        return result


def describe(item) -> str:
    """Length / diameter of an edge, or area of a face, as text."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.GeomAbs import GeomAbs_Circle as _Circle, GeomAbs_Cylinder as _Cyl
    props = GProp_GProps()
    if item.ShapeType() == TopAbs_EDGE:
        BRepGProp.LinearProperties_s(item, props)
        c = BRepAdaptor_Curve(item)
        if c.GetType() == _Circle:
            return f"edge {props.Mass():.3f} mm long, circle Ø{2 * c.Circle().Radius():.3f}"
        return f"edge {props.Mass():.3f} mm long"
    BRepGProp.SurfaceProperties_s(item, props)
    s = BRepAdaptor_Surface(item)
    if s.GetType() == _Cyl:
        return f"face {props.Mass():.2f} mm², cylinder Ø{2 * s.Cylinder().Radius():.3f}"
    return f"face {props.Mass():.2f} mm²"


def distance(a, b):
    """Minimum distance between two shapes and the two closest points."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    d = BRepExtrema_DistShapeShape(a, b)
    if not d.IsDone() or d.NbSolution() == 0:
        raise ValueError("Could not measure between these")
    p, q = d.PointOnShape1(1), d.PointOnShape2(1)
    return d.Value(), (p.X(), p.Y(), p.Z()), (q.X(), q.Y(), q.Z())
