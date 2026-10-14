"""Faceted holes and bosses in a mesh come back as true cylinders."""

import math

import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
)
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

from syntexophobic import export, geometry, roundfaces


def _cyl(x, y, z, r, h, d=(0, 0, 1)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(*d)), r, h).Shape()


def _radii(shape):
    return sorted(round(BRepAdaptor_Surface(f).Cylinder().Radius(), 4) for f in roundfaces._faces(shape)
                  if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder)


def _via_stl(shape, tmp_path, name="p.stl"):
    export.export_shape(shape, tmp_path / name, deflection=0.05)
    return geometry.load_stl(str(tmp_path / name))


def test_through_hole_blind_hole_and_boss(tmp_path):
    s = BRepPrimAPI_MakeBox(60, 40, 10).Shape()
    s = BRepAlgoAPI_Cut(s, _cyl(15, 20, -1, 4, 12)).Shape()          # through
    s = BRepAlgoAPI_Cut(s, _cyl(30, 20, 5, 3, 6)).Shape()            # blind, 5 deep
    s = BRepAlgoAPI_Fuse(s, _cyl(45, 20, 10, 5, 6)).Shape()          # boss on top
    out = _via_stl(s, tmp_path)
    assert _radii(out) == [3.0, 4.0, 5.0]
    assert BRepCheck_Analyzer(out).IsValid()
    assert roundfaces._volume(out) == pytest.approx(60 * 40 * 10 - math.pi * (16 * 10 + 9 * 5 - 25 * 6), rel=1e-7)
    assert len(roundfaces._faces(out)) == 11                         # 6 box sides + 2 holes' walls + floor + boss wall + top


def test_hole_along_another_axis(tmp_path):
    s = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(30, 30, 30).Shape(), _cyl(-1, 15, 15, 6, 32, (1, 0, 0))).Shape()
    assert _radii(_via_stl(s, tmp_path)) == [6.0]


def test_hexagon_stays_a_hexagon(tmp_path):
    poly = BRepBuilderAPI_MakePolygon()
    for k in range(6):
        poly.Add(gp_Pnt(10 * math.cos(k * math.pi / 3), 10 * math.sin(k * math.pi / 3), 0))
    poly.Close()
    hexa = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(poly.Wire()).Face(), gp_Vec(0, 0, 5)).Shape()
    out = _via_stl(hexa, tmp_path)
    assert _radii(out) == [] and len(roundfaces._faces(out)) == 8


def test_coarse_stl_hole_with_few_facets(tmp_path):
    # a 16-sided hole, as a coarse export writes it: still a hole, made round at the facets' radius
    s = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(20, 20, 5).Shape(), _cyl(10, 10, -1, 3, 7)).Shape()
    export.export_shape(s, tmp_path / "c.stl", deflection=0.08)
    out = geometry.load_stl(str(tmp_path / "c.stl"))
    assert _radii(out) == [3.0]


def test_counterbore_and_a_hole_that_breaks_out_of_the_side(tmp_path):
    s = BRepPrimAPI_MakeBox(40, 20, 10).Shape()
    s = BRepAlgoAPI_Cut(s, _cyl(10, 10, -1, 3, 12)).Shape()           # counterbored: 3 through,
    s = BRepAlgoAPI_Cut(s, _cyl(10, 10, 6, 5, 5)).Shape()             # 5 wide and 4 deep on top
    s = BRepAlgoAPI_Cut(s, _cyl(40, 10, -1, 4, 12)).Shape()           # half a hole at the end
    out = _via_stl(s, tmp_path)
    assert _radii(out) == [3.0, 5.0]                                  # the half hole stays faceted
    assert BRepCheck_Analyzer(out).IsValid()
    assert roundfaces._volume(out) == pytest.approx(roundfaces._volume(s), rel=2e-3)


def test_many_holes_at_once(tmp_path):
    s = BRepPrimAPI_MakeBox(200, 100, 10).Shape()
    for i in range(10):
        for j in range(4):
            s = BRepAlgoAPI_Cut(s, _cyl(10 + i * 20, 15 + j * 22, -1, 4, 12)).Shape()
    export.export_shape(s, tmp_path / "m.stl", deflection=0.01)
    out = geometry.load_stl(str(tmp_path / "m.stl"))
    assert _radii(out) == [4.0] * 40 and len(roundfaces._faces(out)) == 46
