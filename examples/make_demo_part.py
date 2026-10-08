"""Writes examples/demo-plate.step: a plate with a rounded pocket, two bosses and corner holes."""

from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt


def vertical_edges_fillet(shape, radius):
    fillet = BRepFilletAPI_MakeFillet(shape)
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge(exp.Current())
        c = BRepAdaptor_Curve(edge)
        if c.GetType() == GeomAbs_Line and abs(c.Line().Direction().Z()) > 0.999:
            fillet.Add(radius, edge)
        exp.Next()
    return fillet.Shape()


def cylinder(x, y, z, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, h).Shape()


plate = vertical_edges_fillet(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 70, 20).Shape(), 8)
pocket = vertical_edges_fillet(BRepPrimAPI_MakeBox(gp_Pnt(20, 15, 12), 60, 40, 10).Shape(), 6)
part = BRepAlgoAPI_Cut(plate, pocket).Shape()
for x in (38, 62):
    part = BRepAlgoAPI_Fuse(part, cylinder(x, 35, 12, 6, 5)).Shape()
    part = BRepAlgoAPI_Cut(part, cylinder(x, 35, 0, 2.5, 30)).Shape()
for x, y in ((8, 8), (92, 8), (8, 62), (92, 62)):
    part = BRepAlgoAPI_Cut(part, cylinder(x, y, 0, 2.6, 30)).Shape()

out = Path(__file__).with_name("demo-plate.step")
w = STEPControl_Writer()
w.Transfer(part, STEPControl_AsIs)
w.Write(str(out))
print(f"wrote {out}")
