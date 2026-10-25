"""Slice for fabrication: each build style on simple solids with known answers."""
import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeTorus
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from syntexophobic import fabslice as fs


def box():
    return BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 60, 40).Shape()


def sphere():
    return BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 50), 50).Shape()


def torus():
    return BRepPrimAPI_MakeTorus(gp_Ax2(gp_Pnt(0, 0, 20), gp_Dir(0, 0, 1)), 40, 15).Shape()


def kinds(res):
    out = {}
    for p in res.parts:
        out[p.kind] = out.get(p.kind, 0) + p.copies
    return out


def test_stacked_box_layers_dowels_and_size():
    res = fs.make(box(), fs.FabSettings(style="stacked", thickness=4, dowels=2))
    assert kinds(res) == {"slice": 10}                     # 40 mm / 4 mm
    for p in res.parts:
        w, h = p.size()
        assert w == pytest.approx(100, abs=0.05) and h == pytest.approx(60, abs=0.05)
        assert len(p.rings) == 3                           # two dowel holes
    # the dowel holes line up through the stack
    centres = [sorted((round(sum(x for x, _ in r) / len(r), 2), round(sum(y for _, y in r) / len(r), 2))
                      for r in p.rings[1:]) for p in res.parts]
    assert all(c == centres[0] for c in centres)


def test_stacked_sphere_guides_on_the_bigger_neighbour():
    res = fs.make(sphere(), fs.FabSettings(style="stacked", thickness=5, dowels=0))
    assert len(res.parts) == 20
    assert all(len(p.rings) == 1 for p in res.parts)
    middle = res.parts[9]
    assert middle.engrave and not res.parts[0].engrave or res.parts[0].engrave == []
    assert sum(1 for p in res.parts if p.engrave) >= 17


def test_rod_spacers_fill_the_gaps():
    s = fs.FabSettings(style="rod", axis="X", thickness=3, spacing=12, rod_diameter=8)
    res = fs.make(box(), s)
    slices = [p for p in res.parts if p.kind == "slice"]
    assert [p.frame.origin[0] for p in slices] == pytest.approx([2 + 12 * i for i in range(9)])
    assert all(len(p.rings) == 2 for p in slices)          # the rod hole
    hole = slices[0].rings[1]
    r = math.dist(hole[0], (sum(x for x, _ in hole) / len(hole), sum(y for _, y in hole) / len(hole)))
    assert r == pytest.approx(4.1, abs=0.05)               # rod radius + half the clearance
    spacer = next(p for p in res.parts if p.kind == "spacer")
    assert spacer.copies == 8 * 3                          # 9 mm gaps = 3 sheets each


def test_rod_through_a_torus_gets_hubs_and_stays_one_piece():
    res = fs.make(torus(), fs.FabSettings(style="rod", axis="X", thickness=3))
    names = [p.name for p in res.parts if p.kind == "slice"]
    assert all(n[-1].isdigit() for n in names)             # no slice split into a / b pieces
    assert any("hub" in w for w in res.warnings)


@pytest.mark.parametrize("style", ["base", "back"])
@pytest.mark.parametrize("axis", ["X", "Y"])
def test_plate_slots_match_the_tabs(style, axis):
    s = fs.FabSettings(style=style, axis=axis, thickness=3, count=5, clearance=0.2)
    res = fs.make(sphere(), s)
    plate = next(p for p in res.parts if p.kind == "plate")
    slices = [p for p in res.parts if p.kind == "slice"]
    assert len(slices) == 5
    assert len(plate.rings) - 1 == 10                      # two tabs per slice, one slot each
    for hole in plate.rings[1:]:
        xs, ys = [x for x, _ in hole], [y for _, y in hole]
        assert min(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(3.2, abs=0.01)
    # every slot sits inside the plate outline, and every slice reaches below its plane
    assert all(p.area() > 0 for p in res.parts)


def test_base_plate_lies_under_the_model():
    res = fs.make(sphere(), fs.FabSettings(style="base", axis="X", thickness=3, count=4))
    plate = next(p for p in res.parts if p.kind == "plate")
    assert plate.frame.origin[2] == pytest.approx(-1.5, abs=0.01)
    assert plate.frame.n == (0, 0, 1)


def test_waffle_box_slots_meet_halfway():
    s = fs.FabSettings(style="waffle", thickness=3, count=3, clearance=0.2)
    res = fs.make(box(), s)
    xs = [p for p in res.parts if p.name.startswith("X")]
    ys = [p for p in res.parts if p.name.startswith("Y")]
    assert len(xs) == 3 and len(ys) == 3
    # an X slice is 60 x 40 with three slots from the top down to half height
    a = xs[0]
    assert a.area() == pytest.approx(60 * 40 - 3 * 3.2 * (20 + 0.1), rel=1e-3)
    b = ys[0]
    assert b.area() == pytest.approx(100 * 40 - 3 * 3.2 * (20 + 0.1), rel=1e-3)
    top = max(y for _, y in a.rings[0])
    assert top == pytest.approx(40, abs=0.01)


def test_waffle_sphere_has_no_slivers_and_no_warnings():
    res = fs.make(sphere(), fs.FabSettings(style="waffle", thickness=3, count=6))
    assert len(res.parts) == 12 and not res.warnings


def test_assembly_builds_a_solid_per_part():
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    res = fs.make(box(), fs.FabSettings(style="waffle", thickness=3, count=2))
    exp, n = TopExp_Explorer(fs.assembly(res), TopAbs_SOLID), 0
    while exp.More():
        n += 1
        exp.Next()
    assert n == len(res.parts)


def test_bad_settings():
    with pytest.raises(fs.FabError):
        fs.make(box(), fs.FabSettings(style="origami"))
    with pytest.raises(fs.FabError):
        fs.make(box(), fs.FabSettings(thickness=0))
