"""Reading 3MF, the 3D printing format (Bambu Studio, PrusaSlicer, Cura, 3D Builder).

A 3MF is a zip. Its _rels/.rels names the root model (usually 3D/3dmodel.model), an XML
file of <object>s, each a <mesh> (vertices, triangles) or a list of <component>s that
place other objects, possibly from other model files in the zip (the Production
extension's p:path, which Bambu Studio uses for every object: 3D/Objects/object_1.model).
<build> lists the <item>s on the plate, each with its own transform.
"""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD_PATH = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
UNITS = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
MAX_TRIANGLES = 5_000_000

Matrix = tuple[float, ...]          # 3MF's 3x4: m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32
IDENTITY: Matrix = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)


class Format3mfError(ValueError):
    pass


def _matrix(text: str | None) -> Matrix:
    if not text:
        return IDENTITY
    m = tuple(float(v) for v in text.split())
    if len(m) != 12:
        raise Format3mfError(f"bad transform {text!r}")
    return m


def _mul(a: Matrix, b: Matrix) -> Matrix:
    """a then b (3MF row vectors: p' = p · a · b)."""
    def at(m, r, c):                 # 4x3 with rows 0-2 linear, row 3 translation
        return m[r * 3 + c]
    out = []
    for r in range(4):
        for c in range(3):
            v = sum(at(a, r, k) * at(b, k, c) for k in range(3))
            if r == 3:
                v += at(b, 3, c)
            out.append(v)
    return tuple(out)


def _apply(m: Matrix, x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def read_3mf(path) -> list[tuple[str, list[tuple[float, float, float]], list[tuple[int, int, int]]]]:
    """Every build item as (name, vertices in mm, triangles), placed as on the plate."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise Format3mfError(f"{path} is not a 3MF file") from e
    with z:
        root = "3D/3dmodel.model"
        if "_rels/.rels" in z.namelist():
            for rel in ET.fromstring(z.read("_rels/.rels")).iter(f"{RELS}Relationship"):
                if rel.get("Type") == MODEL_REL:
                    root = rel.get("Target", root).lstrip("/")
        models: dict[str, ET.Element] = {}

        def model(name: str) -> ET.Element:
            name = name.lstrip("/")
            if name not in models:
                try:
                    models[name] = ET.fromstring(z.read(name))
                except KeyError:
                    raise Format3mfError(f"{path} has no {name}") from None
            return models[name]

        top = model(root)
        scale = UNITS.get(top.get("unit", "millimeter"), 1.0)
        out, count = [], [0]

        def objects(name: str) -> dict[str, ET.Element]:
            res = model(name).find(f"{CORE}resources")
            return {o.get("id"): o for o in (res.findall(f"{CORE}object") if res is not None else [])}

        def walk(file: str, oid: str, m: Matrix, verts, tris, depth=0):
            if depth > 16:
                raise Format3mfError("components nested too deep")
            obj = objects(file).get(oid)
            if obj is None:
                raise Format3mfError(f"object {oid} missing from {file}")
            mesh = obj.find(f"{CORE}mesh")
            if mesh is not None:
                base = len(verts)
                for v in mesh.find(f"{CORE}vertices").iter(f"{CORE}vertex"):
                    x, y, zz = _apply(m, float(v.get("x")), float(v.get("y")), float(v.get("z")))
                    verts.append((x * scale, y * scale, zz * scale))
                for t in mesh.find(f"{CORE}triangles").iter(f"{CORE}triangle"):
                    tris.append((base + int(t.get("v1")), base + int(t.get("v2")), base + int(t.get("v3"))))
                count[0] += len(tris)
                if count[0] > MAX_TRIANGLES:
                    raise Format3mfError(f"{path} has more than {MAX_TRIANGLES} triangles")
            comps = obj.find(f"{CORE}components")
            for c in (comps.findall(f"{CORE}component") if comps is not None else []):
                sub = c.get(PROD_PATH) or file
                walk(posixpath.normpath(sub.lstrip("/")), c.get("objectid"),
                     _mul(_matrix(c.get("transform")), m), verts, tris, depth + 1)

        build = top.find(f"{CORE}build")
        for item in (build.findall(f"{CORE}item") if build is not None else []):
            if item.get("printable") == "0":
                continue
            verts, tris = [], []
            file = (item.get(PROD_PATH) or root).lstrip("/")
            walk(file, item.get("objectid"), _matrix(item.get("transform")), verts, tris)
            obj = objects(file).get(item.get("objectid"))
            if tris:
                out.append((obj.get("name") or f"object {item.get('objectid')}", verts, tris))
    return out
