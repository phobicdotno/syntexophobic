"""3MF import, as Bambu Studio writes it: objects in their own model files, placed by
components and build items with transforms."""

import zipfile

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer

from syntexophobic import export, geometry
from syntexophobic.mesh3mf import Format3mfError, read_3mf

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"


def _count(shape, kind):
    exp, n = TopExp_Explorer(shape, kind), 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def _mesh_xml(v, t):
    return ("<mesh><vertices>" + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v)
            + "</vertices><triangles>" + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
            + "</triangles></mesh>")


@pytest.fixture(scope="module")
def bambu(tmp_path_factory):
    """Two 20 x 10 x 5 boxes, as Bambu Studio saves a plate: the root model only places
    objects that live in 3D/Objects/object_1.model; the second copy is turned 90°."""
    v, t = export.mesh(BRepPrimAPI_MakeBox(20, 10, 5).Shape())
    obj = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}"><resources>'
           f'<object id="1" type="model">{_mesh_xml(v, t)}</object></resources><build/></model>')
    root = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}" xmlns:p="{PROD}"><resources>'
            f'<object id="2" type="model" name="Bracket"><components>'
            f'<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            f'</components></object></resources><build>'
            f'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 128 128 0" printable="1"/>'
            f'<item objectid="2" transform="0 1 0 -1 0 0 0 0 1 50 50 0"/>'
            f'<item objectid="2" printable="0"/>'
            f'</build></model>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    path = tmp_path_factory.mktemp("bambu") / "plate.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", root)
        z.writestr("3D/Objects/object_1.model", obj)
        z.writestr("Metadata/project_settings.config", "{}")       # Bambu's slicer settings: ignored
    return path


def test_bambu_plate_reads_every_printable_object(bambu):
    objs = read_3mf(bambu)
    assert [name for name, _, _ in objs] == ["Bracket", "Bracket"]          # the unprintable one is left out
    xs = [x for x, _, _ in objs[0][1]]
    assert min(xs) == pytest.approx(128) and max(xs) == pytest.approx(148)
    turned = objs[1][1]                                                      # 90° about Z, then moved
    assert min(x for x, _, _ in turned) == pytest.approx(40) and max(y for _, y, _ in turned) == pytest.approx(70)


def test_bambu_plate_becomes_solids_with_flat_faces(bambu):
    shape = geometry.load_model(str(bambu))
    assert _count(shape, TopAbs_SOLID) == 2
    assert _count(shape, TopAbs_FACE) == 12                                  # 6 flat faces each, not 24 triangles
    b = geometry.bounding_box(shape)
    assert (round(b.xmin), round(b.xmax), round(b.zmax)) == (40, 148, 5)


def test_our_own_3mf_round_trips(tmp_path):
    export.export_shape(BRepPrimAPI_MakeBox(30, 20, 10).Shape(), tmp_path / "box.3mf")
    shape = geometry.load_model(str(tmp_path / "box.3mf"))
    assert _count(shape, TopAbs_FACE) == 6
    geometry.stl_to_step(str(tmp_path / "box.3mf"), str(tmp_path / "box.step"))
    assert (tmp_path / "box.step").stat().st_size > 1000


def test_units_and_bad_files(tmp_path):
    v, t = export.mesh(BRepPrimAPI_MakeBox(1, 1, 1).Shape())
    with zipfile.ZipFile(tmp_path / "inch.3mf", "w") as z:
        z.writestr("3D/3dmodel.model", f'<model unit="inch" xmlns="{CORE}"><resources><object id="1">'
                                       f'{_mesh_xml(v, t)}</object></resources><build><item objectid="1"/></build></model>')
    assert max(x for x, _, _ in read_3mf(tmp_path / "inch.3mf")[0][1]) == pytest.approx(25.4)
    (tmp_path / "junk.3mf").write_text("no")
    with pytest.raises(Format3mfError):
        read_3mf(tmp_path / "junk.3mf")


def test_object_of_several_parts_is_a_solid_each(tmp_path):
    from OCP.BRepCheck import BRepCheck_Analyzer
    v, t = export.mesh(BRepPrimAPI_MakeBox(10, 10, 10).Shape())
    with zipfile.ZipFile(tmp_path / "parts.3mf", "w") as z:
        z.writestr("3D/3dmodel.model",
                   f'<model xmlns="{CORE}"><resources><object id="1">{_mesh_xml(v, t)}</object>'
                   f'<object id="2" name="two"><components><component objectid="1"/>'
                   f'<component objectid="1" transform="1 0 0 0 1 0 0 0 1 30 0 0"/></components></object>'
                   f'</resources><build><item objectid="2"/></build></model>')
    shape = geometry.load_model(str(tmp_path / "parts.3mf"))
    assert _count(shape, TopAbs_SOLID) == 2 and BRepCheck_Analyzer(shape).IsValid()


def test_triangle_limit_counts_each_component_once(tmp_path, monkeypatch):
    from syntexophobic import mesh3mf
    v, t = export.mesh(BRepPrimAPI_MakeBox(10, 10, 10).Shape())
    comps = "".join(f'<component objectid="1" transform="1 0 0 0 1 0 0 0 1 {20 * i} 0 0"/>' for i in range(4))
    with zipfile.ZipFile(tmp_path / "four.3mf", "w") as z:
        z.writestr("3D/3dmodel.model",
                   f'<model xmlns="{CORE}"><resources><object id="1">{_mesh_xml(v, t)}</object>'
                   f'<object id="2"><components>{comps}</components></object>'
                   f'</resources><build><item objectid="2"/></build></model>')
    monkeypatch.setattr(mesh3mf, "MAX_TRIANGLES", 4 * len(t))
    read_3mf(tmp_path / "four.3mf")                   # exactly at the limit: no error
