"""Export to other programs: the 3D part in the common solid and mesh formats, and a sketch
in the common flat vector formats. The format is picked from the file's extension.

3D, exact solids: STEP, IGES, BREP (OpenCascade's own).
3D, meshes: STL, OBJ, PLY, glTF (.gltf / .glb) and 3MF, all in millimetres.
2D: DXF, SVG, PDF, EPS and HPGL (.plt / .hpgl), in millimetres; construction geometry is left out.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import zipfile
from pathlib import Path

SOLID_3D = {".step": "STEP", ".stp": "STEP", ".iges": "IGES", ".igs": "IGES", ".brep": "BREP"}
MESH_3D = {".stl": "STL", ".obj": "OBJ", ".ply": "PLY", ".glb": "glTF binary", ".gltf": "glTF", ".3mf": "3MF"}
FLAT_2D = {".dxf": "DXF", ".svg": "SVG", ".pdf": "PDF", ".eps": "EPS", ".plt": "HPGL", ".hpgl": "HPGL"}


class ExportError(RuntimeError):
    pass


def _filter(formats: dict) -> str:
    by_name: dict[str, list[str]] = {}
    for ext, name in formats.items():
        by_name.setdefault(name, []).append(f"*{ext}")
    return ";;".join(f"{n} ({' '.join(e)})" for n, e in by_name.items())


def model_filter() -> str:
    """For a save dialog: every 3D format."""
    return _filter(SOLID_3D | MESH_3D)


def sketch_filter() -> str:
    return _filter(FLAT_2D)


def _suffix(path, formats: dict) -> str:
    ext = Path(path).suffix.lower()
    if ext not in formats:
        raise ExportError(f"Can't export to {ext or 'a file without an extension'}; "
                          f"use one of {', '.join(sorted(formats))}")
    return ext


# ---- 3D ---------------------------------------------------------------------------------------

def export_shape(shape, path: str | Path, deflection: float = 0.02) -> None:
    """Write a solid to `path` in the format its extension names."""
    path = Path(path)
    ext = _suffix(path, SOLID_3D | MESH_3D)
    if ext in (".step", ".stp"):
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
        from .geometry import quiet
        quiet()
        w = STEPControl_Writer()
        w.Transfer(shape, STEPControl_AsIs)
        ok = w.Write(str(path)) == IFSelect_RetDone
    elif ext in (".iges", ".igs"):
        from OCP.IGESControl import IGESControl_Controller, IGESControl_Writer
        IGESControl_Controller.Init_s()
        w = IGESControl_Writer("MM", 1)                    # 1: solids as B-rep, not trimmed surfaces
        w.AddShape(shape)
        w.ComputeModel()
        ok = w.Write(str(path))
    elif ext == ".brep":
        from OCP.BRepTools import BRepTools
        ok = BRepTools.Write_s(shape, str(path))
    else:
        vertices, triangles = mesh(shape, deflection)
        if not triangles:
            raise ExportError("The model has no surface to export")
        {".stl": _stl, ".obj": _obj, ".ply": _ply, ".glb": _glb, ".gltf": _gltf, ".3mf": _3mf}[ext](
            path, vertices, triangles)
        ok = True
    if not ok:
        raise ExportError(f"Could not write {path}")


def mesh(shape, deflection: float = 0.02) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Triangles of the shape's faces (outward winding), with shared corners within a face."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.2, True)
    vertices, triangles = [], []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        base = len(vertices)
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            vertices.append((p.X(), p.Y(), p.Z()))
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            triangles.append((base + a - 1, base + c - 1, base + b - 1) if flip else
                             (base + a - 1, base + b - 1, base + c - 1))
    return vertices, triangles


def _normal(v, t):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (v[i] for i in t)
    ux, uy, uz, wx, wy, wz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    n = (uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx)
    length = math.sqrt(sum(c * c for c in n)) or 1.0
    return tuple(c / length for c in n)


def _stl(path, v, tris):
    with open(path, "wb") as f:
        f.write(b"Syntexophobic".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<12fH", *_normal(v, t), *v[t[0]], *v[t[1]], *v[t[2]], 0))


def _obj(path, v, tris):
    with open(path, "w") as f:
        f.write("# Syntexophobic, millimetres\no part\n")
        f.writelines(f"v {x:.6g} {y:.6g} {z:.6g}\n" for x, y, z in v)
        f.writelines(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in tris)


def _ply(path, v, tris):
    with open(path, "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment Syntexophobic, millimetres\n"
                 f"element vertex {len(v)}\nproperty float x\nproperty float y\nproperty float z\n"
                 f"element face {len(tris)}\nproperty list uchar int vertex_indices\nend_header\n").encode())
        f.write(b"".join(struct.pack("<3f", *p) for p in v))
        f.write(b"".join(struct.pack("<B3i", 3, *t) for t in tris))


def _gltf_parts(v, tris):
    """(json, binary buffer) for a glTF of the mesh. glTF is in metres with Y up, so the part
    is scaled from mm and turned from Z up."""
    pos = b"".join(struct.pack("<3f", x / 1000, z / 1000, -y / 1000) for x, y, z in v)
    idx = b"".join(struct.pack("<3I", *t) for t in tris)
    lo = [min(p[k] for p in v) for k in range(3)]
    hi = [max(p[k] for p in v) for k in range(3)]
    mn = [lo[0] / 1000, lo[2] / 1000, -hi[1] / 1000]
    mx = [hi[0] / 1000, hi[2] / 1000, -lo[1] / 1000]
    doc = {
        "asset": {"version": "2.0", "generator": "Syntexophobic"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "part"}],
        "meshes": [{"name": "part", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"name": "part", "pbrMetallicRoughness": {"baseColorFactor": [0.75, 0.77, 0.8, 1],
                                                                 "metallicFactor": 0.3, "roughnessFactor": 0.6}}],
        "buffers": [{"byteLength": len(pos) + len(idx)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                        {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx), "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(v), "type": "VEC3", "min": mn, "max": mx},
                      {"bufferView": 1, "componentType": 5125, "count": 3 * len(tris), "type": "SCALAR"}],
    }
    return doc, pos + idx


def _glb(path, v, tris):
    doc, buf = _gltf_parts(v, tris)
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    buf += b"\0" * (-len(buf) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(buf)))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(buf), b"BIN\0") + buf)


def _gltf(path, v, tris):
    doc, buf = _gltf_parts(v, tris)
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(buf).decode()
    Path(path).write_text(json.dumps(doc))


def _3mf(path, v, tris):
    verts = "".join(f'<vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>' for x, y, z in v)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Application">Syntexophobic</metadata>'
             f'<resources><object id="1" type="model" name="part"><mesh><vertices>{verts}</vertices>'
             f'<triangles>{faces}</triangles></mesh></object></resources>'
             '<build><item objectid="1"/></build></model>')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


# ---- 2D ---------------------------------------------------------------------------------------

def outlines(sk) -> list[tuple]:
    """A sketch's visible geometry in mm: ("line", p, q), ("arc", centre, r, a0, a1) running
    counter-clockwise from a0 to a1 (radians), ("circle", centre, r), ("poly", points, closed)."""
    P = sk.points
    xy = lambda pid: (P[pid].x, P[pid].y)                   # noqa: E731
    out = []
    for ln in sk.lines.values():
        if not ln.construction:
            out.append(("line", xy(ln.a), xy(ln.b)))
    for c in sk.circles.values():
        if not c.construction:
            out.append(("circle", xy(c.center), c.r))
    for a in sk.arcs.values():
        if not a.construction:
            (cx, cy), (sx, sy), (ex, ey) = xy(a.center), xy(a.start), xy(a.end)
            a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
            if a1 <= a0:
                a1 += 2 * math.pi
            out.append(("arc", (cx, cy), a.r, a0, a1))
    for sid, sp in sk.splines.items():
        if not sp.construction:
            out.append(("poly", sk.spline_samples(sid, 128), sp.closed))
    return out


def _polyline(item, tol: float = 0.01) -> tuple[list[tuple[float, float]], bool]:
    """Any outline as points (arcs and circles split finely enough to stay within `tol` mm)."""
    kind = item[0]
    if kind == "line":
        return [item[1], item[2]], False
    if kind == "poly":
        return list(item[1]), item[2]
    (cx, cy), r = item[1], item[2]
    a0, a1 = (0.0, 2 * math.pi) if kind == "circle" else (item[3], item[4])
    step = 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 8
    n = max(8, math.ceil((a1 - a0) / max(step, 1e-3)))
    pts = [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]
    return (pts[:-1], True) if kind == "circle" else (pts, False)


def bounds(items) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for it in items:
        pts, _ = _polyline(it, 0.5)
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    if not xs:
        raise ExportError("The sketch has nothing to export")
    return min(xs), min(ys), max(xs), max(ys)


def export_sketch(sk, path: str | Path, margin: float = 2.0) -> None:
    """Write a sketch to `path` in the flat format its extension names (millimetres, 1:1)."""
    path = Path(path)
    ext = _suffix(path, FLAT_2D)
    if ext == ".dxf":
        from .dxf import export_dxf
        export_dxf(sk, path)
        return
    items = outlines(sk)
    x0, y0, x1, y1 = bounds(items)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    {".svg": _svg, ".pdf": _pdf, ".eps": _eps, ".plt": _hpgl, ".hpgl": _hpgl}[ext](path, items, (x0, y0, x1, y1))


def _svg(path, items, box):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    X = lambda x: f"{x - x0:.4f}"                            # noqa: E731
    Y = lambda y: f"{y1 - y:.4f}"                            # SVG's Y runs down  # noqa: E731
    parts = []
    for it in items:
        kind = it[0]
        if kind == "line":
            (ax, ay), (bx, by) = it[1], it[2]
            parts.append(f'<line x1="{X(ax)}" y1="{Y(ay)}" x2="{X(bx)}" y2="{Y(by)}"/>')
        elif kind == "circle":
            (cx, cy), r = it[1], it[2]
            parts.append(f'<circle cx="{X(cx)}" cy="{Y(cy)}" r="{r:.4f}"/>')
        elif kind == "arc":
            (cx, cy), r, a0, a1 = it[1:]
            sx, sy, ex, ey = cx + r * math.cos(a0), cy + r * math.sin(a0), cx + r * math.cos(a1), cy + r * math.sin(a1)
            large = 1 if a1 - a0 > math.pi else 0
            # counter-clockwise with Y up is sweep-flag 0 once Y is flipped
            parts.append(f'<path d="M{X(sx)} {Y(sy)} A{r:.4f} {r:.4f} 0 {large} 0 {X(ex)} {Y(ey)}"/>')
        else:
            pts, closed = it[1], it[2]
            d = "M" + " L".join(f"{X(x)} {Y(y)}" for x, y in pts) + (" Z" if closed else "")
            parts.append(f'<path d="{d}"/>')
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.4f}mm" height="{h:.4f}mm" viewBox="0 0 {w:.4f} {h:.4f}">\n'
        '<g fill="none" stroke="#000" stroke-width="0.1">\n' + "\n".join(parts) + "\n</g>\n</svg>\n")


PT = 72 / 25.4                                               # PostScript / PDF points per mm


def _eps(path, items, box):
    x0, y0, x1, y1 = box
    ops = []
    for it in items:
        kind = it[0]
        if kind == "circle":
            (cx, cy), r = it[1], it[2]
            ops.append(f"newpath {cx - x0:.4f} {cy - y0:.4f} {r:.4f} 0 360 arc closepath stroke")
        elif kind == "arc":
            (cx, cy), r, a0, a1 = it[1:]
            ops.append(f"newpath {cx - x0:.4f} {cy - y0:.4f} {r:.4f} {math.degrees(a0):.5f} {math.degrees(a1):.5f} arc stroke")
        else:
            pts, closed = _polyline(it)
            (fx, fy), rest = pts[0], pts[1:]
            ops.append(f"newpath {fx - x0:.4f} {fy - y0:.4f} moveto "
                       + " ".join(f"{x - x0:.4f} {y - y0:.4f} lineto" for x, y in rest)
                       + (" closepath" if closed else "") + " stroke")
    w, h = (x1 - x0) * PT, (y1 - y0) * PT
    Path(path).write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%Creator: Syntexophobic\n"
        f"%%BoundingBox: 0 0 {math.ceil(w)} {math.ceil(h)}\n%%HiResBoundingBox: 0 0 {w:.3f} {h:.3f}\n%%EndComments\n"
        f"gsave {PT:.6f} {PT:.6f} scale 0.1 setlinewidth 1 setlinecap 1 setlinejoin\n"
        + "\n".join(ops) + "\ngrestore\nshowpage\n%%EOF\n")


def _pdf(path, items, box):
    """A one-page PDF the size of the drawing, written by hand (no Qt or other library)."""
    x0, y0, x1, y1 = box
    ops = [f"{PT:.6f} 0 0 {PT:.6f} 0 0 cm 0.1 w 1 J 1 j"]
    for it in items:
        pts, closed = _polyline(it)
        (fx, fy), rest = pts[0], pts[1:]
        ops.append(f"{fx - x0:.4f} {fy - y0:.4f} m " + " ".join(f"{x - x0:.4f} {y - y0:.4f} l" for x, y in rest)
                   + (" h S" if closed else " S"))
    stream = "\n".join(ops).encode()
    w, h = (x1 - x0) * PT, (y1 - y0) * PT
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.3f} {h:.3f}] /Contents 4 0 R /Resources << >> >>".encode(),
               f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    Path(path).write_bytes(bytes(out))


def _hpgl(path, items, box):
    """HPGL for plotters and cutters: 40 plotter units per mm, origin at the drawing's corner."""
    x0, y0 = box[0], box[1]
    U = lambda x, y: f"{round((x - x0) * 40)},{round((y - y0) * 40)}"     # noqa: E731
    cmds = ["IN", "SP1"]
    for it in items:
        pts, closed = _polyline(it)
        if closed:
            pts = pts + [pts[0]]
        cmds.append(f"PU{U(*pts[0])}")
        cmds.append("PD" + ",".join(U(x, y) for x, y in pts[1:]))
    cmds += ["PU", "SP0"]
    Path(path).write_text(";\n".join(cmds) + ";\n")
