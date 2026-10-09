import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

# One widgets application for every test: text and SpaceMouse tests would otherwise make a bare
# QGuiApplication first, and any later window or dialog in the same worker aborts.
_app = QApplication.instance() or QApplication([])

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt


@pytest.fixture(scope="session")
def pocket_part(tmp_path_factory):
    """60 x 40 x 15 block with a 30 x 20 x 5 pocket and a Ø8 boss standing 3 mm up from its floor."""
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60, 40, 15).Shape()
    pocket = BRepPrimAPI_MakeBox(gp_Pnt(15, 10, 10), 30, 20, 6).Shape()
    boss = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(30, 20, 10), gp_Dir(0, 0, 1)), 4, 3).Shape()
    part = BRepAlgoAPI_Fuse(BRepAlgoAPI_Cut(block, pocket).Shape(), boss).Shape()
    path = tmp_path_factory.mktemp("model") / "pocket.step"
    w = STEPControl_Writer()
    w.Transfer(part, STEPControl_AsIs)
    w.Write(str(path))
    return path

