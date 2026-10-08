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
from OCP.gp import gp_Ax1, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf

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


def upward_flat_levels(shape: TopoDS_Shape) -> list[float]:
    """Z heights of horizontal faces that point up: pocket floors, steps and the top."""
    levels = set()
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
                levels.add(round(plane.Location().Z(), 4))
        exp.Next()
    return sorted(levels)


@dataclass(frozen=True)
class Hole:
    x: float
    y: float
    diameter: float
    top: float
    bottom: float


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
            if abs(axis.Direction().Z()) > 0.999 and face.Orientation() == TopAbs_REVERSED:
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


def mesh(shape: TopoDS_Shape, deflection: float = 0.1):
    """Triangle mesh for display: (vertices [(x,y,z)], triangles [(i,j,k)])."""
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
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
        exp.Next()
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
