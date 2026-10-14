"""Exports: the part in 3D solid and mesh formats, a sketch in flat vector formats."""

import json
import struct
import xml.etree.ElementTree as ET
import zipfile

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from syntexophobic import export, geometry
from syntexophobic.sketch import Sketch


@pytest.fixture(scope="module")
def box():
    return BRepPrimAPI_MakeBox(40, 30, 10).Shape()


@pytest.mark.parametrize("ext", sorted(export.SOLID_3D | export.MESH_3D))
def test_every_3d_format_writes(box, tmp_path, ext):
    out = tmp_path / f"part{ext}"
    export.export_shape(box, out)
    assert out.stat().st_size > 100


def test_solids_read_back(box, tmp_path):
    export.export_shape(box, tmp_path / "p.step")
    b = geometry.bounding_box(geometry.load_step(str(tmp_path / "p.step")))
    assert (round(b.xmax - b.xmin), round(b.ymax - b.ymin), round(b.zmax - b.zmin)) == (40, 30, 10)
    from OCP.IGESControl import IGESControl_Reader
    export.export_shape(box, tmp_path / "p.igs")
    r = IGESControl_Reader()
    r.ReadFile(str(tmp_path / "p.igs"))
    r.TransferRoots()
    b = geometry.bounding_box(r.OneShape())
    assert round(b.xmax - b.xmin) == 40


def test_mesh_is_closed_and_wound_outwards(box, tmp_path):
    v, t = export.mesh(box)
    volume = sum(a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
                 for a, b, c in ((v[i], v[j], v[k]) for i, j, k in t)) / 6
    assert volume == pytest.approx(40 * 30 * 10)                            # negative if wound inwards
    export.export_shape(box, tmp_path / "p.stl")
    b = geometry.bounding_box(geometry.load_stl(str(tmp_path / "p.stl")))
    assert (round(b.xmax), round(b.ymax), round(b.zmax)) == (40, 30, 10)


def test_mesh_formats(box, tmp_path):
    v, t = export.mesh(box)
    export.export_shape(box, tmp_path / "p.obj")
    lines = (tmp_path / "p.obj").read_text().splitlines()
    assert sum(l.startswith("v ") for l in lines) == len(v) and sum(l.startswith("f ") for l in lines) == len(t)

    export.export_shape(box, tmp_path / "p.ply")
    head = (tmp_path / "p.ply").read_bytes().split(b"end_header\n")[0].decode()
    assert f"element vertex {len(v)}" in head and f"element face {len(t)}" in head

    export.export_shape(box, tmp_path / "p.glb")
    data = (tmp_path / "p.glb").read_bytes()
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert magic == b"glTF" and version == 2 and length == len(data)
    n = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + n])
    assert doc["accessors"][0]["count"] == len(v)
    assert doc["accessors"][0]["max"][1] == pytest.approx(0.010)            # Z up in mm -> Y up in m

    export.export_shape(box, tmp_path / "p.gltf")
    assert json.loads((tmp_path / "p.gltf").read_text())["buffers"][0]["uri"].startswith("data:")

    export.export_shape(box, tmp_path / "p.3mf")
    with zipfile.ZipFile(tmp_path / "p.3mf") as z:
        model = ET.fromstring(z.read("3D/3dmodel.model"))
        assert "[Content_Types].xml" in z.namelist()
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    assert model.get("unit") == "millimeter" and len(model.findall(".//m:triangle", ns)) == len(t)


def test_unknown_format(box, tmp_path):
    with pytest.raises(export.ExportError, match=".xyz"):
        export.export_shape(box, tmp_path / "p.xyz")


@pytest.fixture
def drawing():
    sk = Sketch("plate")
    sk.rectangle(0, 0, 50, 20)
    sk.circle(sk.point(10, 10), 4)
    c, s, e = sk.point(35, 10), sk.point(40, 10), sk.point(30, 10)
    sk.arc(c, s, e)                                          # the top half, counter-clockwise
    sk.line(sk.point(0, 30), sk.point(50, 30), construction=True)   # left out
    return sk


@pytest.mark.parametrize("ext", sorted(export.FLAT_2D))
def test_every_flat_format_writes(drawing, tmp_path, ext):
    out = tmp_path / f"plate{ext}"
    export.export_sketch(drawing, out)
    assert out.stat().st_size > 50


def test_svg(drawing, tmp_path):
    export.export_sketch(drawing, tmp_path / "p.svg")
    root = ET.parse(tmp_path / "p.svg").getroot()
    assert root.get("width") == "54.0000mm" and root.get("height") == "24.0000mm"   # 2 mm margin
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert len(root.findall(".//s:line", ns)) == 4 and len(root.findall(".//s:circle", ns)) == 1
    arc = root.find(".//s:path", ns).get("d")
    assert arc == "M42.0000 12.0000 A5.0000 5.0000 0 0 0 32.0000 12.0000"


def test_pdf_is_well_formed(drawing, tmp_path):
    export.export_sketch(drawing, tmp_path / "p.pdf")
    data = (tmp_path / "p.pdf").read_bytes()
    assert data.startswith(b"%PDF-1.4")
    start = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert data[start:start + 4] == b"xref"
    for k, off in enumerate(data[start:].split(b"\n")[3:7], 1):           # every object where xref says
        o = int(off.split()[0])
        assert data[o:].startswith(f"{k} 0 obj".encode())


def test_eps_and_hpgl(drawing, tmp_path):
    export.export_sketch(drawing, tmp_path / "p.eps")
    eps = (tmp_path / "p.eps").read_text()
    assert "%%BoundingBox: 0 0 154 69" in eps and " arc " in eps
    export.export_sketch(drawing, tmp_path / "p.plt")
    hpgl = (tmp_path / "p.plt").read_text()
    assert hpgl.startswith("IN;") and "PU80,80;" in hpgl                    # 40 units per mm, from the margin
