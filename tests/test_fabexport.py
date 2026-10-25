"""Sheets from sliced parts: finishing for laser / CNC, packing, DXF and SVG files."""
import ezdxf
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

from syntexophobic import fabexport as fe
from syntexophobic import fabslice as fs


def box():
    return BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 60, 40).Shape()


def test_every_part_lands_on_a_sheet_inside_its_edges():
    res = fs.make(box(), fs.FabSettings(style="stacked", thickness=4))
    ss = fe.SheetSettings(width=300, height=200, labels=False, kerf=0)
    sheets = fe.layout(res, ss)
    assert sum(len(s.parts) for s in sheets) == res.count() == 10
    assert len(sheets) >= 2                                  # four 100 x 60 layers fit a sheet at most
    for s in sheets:
        for ring in s.cuts:
            assert all(5 - 1e-6 <= x <= 295 + 1e-6 and 5 - 1e-6 <= y <= 195 + 1e-6 for x, y in ring)


def test_spacer_copies_are_all_placed():
    res = fs.make(box(), fs.FabSettings(style="rod", axis="X", thickness=3, spacing=12))
    sheets = fe.layout(res, fe.SheetSettings(width=600, height=400))
    assert sum(len(s.parts) for s in sheets) == res.count()
    assert sum(1 for s in sheets for n in s.parts if n == "Spacer") == 24


def test_laser_kerf_makes_parts_bigger_by_half_the_kerf_each_side():
    part = fs.make(box(), fs.FabSettings(style="stacked", thickness=4, dowels=0)).parts[0]
    f = fe.finish(part, fe.SheetSettings(machine="laser", kerf=0.2, labels=False))
    xs = [x for x, _ in f.rings[0]]
    assert max(xs) - min(xs) == pytest.approx(100.2, abs=0.01)


def test_cnc_dogbones_in_the_waffle_slots():
    res = fs.make(box(), fs.FabSettings(style="waffle", thickness=6, count=2))
    part = res.parts[0]
    f = fe.finish(part, fe.SheetSettings(machine="cnc", tool_diameter=3.175, labels=False))
    assert f.rings[0] != part.rings[0] and len(f.rings[0]) > len(part.rings[0])


def test_labels_are_engraved():
    res = fs.make(box(), fs.FabSettings(style="stacked", thickness=4, dowels=0, guide_outline=False))
    f = fe.finish(res.parts[0], fe.SheetSettings(labels=True))
    assert f.engrave


def test_plate_slot_numbers_are_engraved():
    res = fs.make(BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 50), 50).Shape(),
                  fs.FabSettings(style="base", axis="X", count=4))
    plate = next(p for p in res.parts if p.kind == "plate")
    f = fe.finish(plate, fe.SheetSettings(labels=False))
    assert len(f.engrave) >= 4


def test_part_bigger_than_the_sheet_says_so():
    res = fs.make(box(), fs.FabSettings(style="stacked", thickness=4))
    with pytest.raises(ValueError, match="bigger sheet"):
        fe.layout(res, fe.SheetSettings(width=80, height=50))


def test_files_read_back(tmp_path):
    res = fs.make(box(), fs.FabSettings(style="waffle", thickness=3, count=3))
    ss = fe.SheetSettings(width=400, height=300)
    sheets = fe.layout(res, ss)
    files = fe.write(sheets, ss, tmp_path, "boxy")
    assert [p.name for p in files][:2] == ["boxy-sheet1.dxf", "boxy-sheet1.svg"]
    doc = ezdxf.readfile(files[0])
    msp = doc.modelspace()
    cuts = msp.query('LWPOLYLINE[layer=="CUT"]')
    assert len(cuts) == len(sheets[0].cuts)
    assert len(msp.query('LWPOLYLINE[layer=="ENGRAVE"]')) > 0
    svg = files[1].read_text()
    assert 'width="400mm"' in svg and svg.count("<path") == len(sheets[0].cuts) + len(
        [e for e in sheets[0].engrave if len(e) >= 2])
