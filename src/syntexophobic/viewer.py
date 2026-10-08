"""3D view: model, stock box and toolpaths, drawn with VTK inside a Qt widget."""

from __future__ import annotations

import vtkmodules.vtkInteractionStyle  # noqa: F401  (registers interactor styles)
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkFiltersSources import vtkCubeSource
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer

from . import geometry

# Operation colours cycle through these; rapids are drawn thin and red.
OP_COLOURS = [(64, 160, 255), (255, 176, 59), (120, 214, 120), (214, 120, 214)]
RAPID = (230, 70, 70)


def _rgb(c):
    return tuple(v / 255 for v in c)


class Viewer(QVTKRenderWindowInteractor):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ren = vtkRenderer()
        self.ren.SetBackground(0.13, 0.14, 0.16)
        self.ren.SetBackground2(0.24, 0.26, 0.30)
        self.ren.GradientBackgroundOn()
        self.GetRenderWindow().AddRenderer(self.ren)
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self._model = self._stock = None
        self._paths: list[vtkActor] = []
        axes = vtkAxesActor()
        self._axes = vtkOrientationMarkerWidget()
        self._axes.SetOrientationMarker(axes)
        self._axes.SetInteractor(self.GetRenderWindow().GetInteractor())
        self._axes.SetViewport(0.0, 0.0, 0.14, 0.2)
        self._axes.EnabledOn()
        self._axes.InteractiveOff()
        self.Initialize()

    # ---- scene ----------------------------------------------------------------------

    def show_model(self, shape) -> None:
        if self._model:
            self.ren.RemoveActor(self._model)
        verts, tris = geometry.mesh(shape)
        pts = vtkPoints()
        for v in verts:
            pts.InsertNextPoint(v)
        cells = vtkCellArray()
        for t in tris:
            cells.InsertNextCell(3, t)
        poly = vtkPolyData()
        poly.SetPoints(pts)
        poly.SetPolys(cells)
        normals = vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.SetFeatureAngle(35)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        self._model = vtkActor()
        self._model.SetMapper(mapper)
        prop = self._model.GetProperty()
        prop.SetColor(0.78, 0.80, 0.84)
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(30)
        self.ren.AddActor(self._model)

    def show_stock(self, box: geometry.Box) -> None:
        if self._stock:
            self.ren.RemoveActor(self._stock)
        cube = vtkCubeSource()
        cube.SetBounds(box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(cube.GetOutputPort())
        self._stock = vtkActor()
        self._stock.SetMapper(mapper)
        p = self._stock.GetProperty()
        p.SetRepresentationToWireframe()
        p.SetColor(0.95, 0.75, 0.40)
        p.SetLineWidth(1.5)
        self.ren.AddActor(self._stock)

    def show_toolpaths(self, toolpaths, show_rapids: bool = True) -> None:
        for a in self._paths:
            self.ren.RemoveActor(a)
        self._paths = []
        for i, tp in enumerate(toolpaths):
            for kind in ("feed", "rapid") if show_rapids else ("feed",):
                actor = self._polyline_actor(tp, kind, OP_COLOURS[i % len(OP_COLOURS)])
                if actor:
                    self._paths.append(actor)
                    self.ren.AddActor(actor)
        self.GetRenderWindow().Render()

    def _polyline_actor(self, tp, kind: str, colour):
        pts, lines = vtkPoints(), vtkCellArray()
        last = None
        for k, x, y, z, f in tp.positions():
            if last is not None and k == kind:
                a = pts.InsertNextPoint(last)
                b = pts.InsertNextPoint((x, y, z))
                lines.InsertNextCell(2, (a, b))
            last = (x, y, z)
        if lines.GetNumberOfCells() == 0:
            return None
        poly = vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(lines)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)
        actor = vtkActor()
        actor.SetMapper(mapper)
        p = actor.GetProperty()
        p.SetColor(*_rgb(RAPID if kind == "rapid" else colour))
        p.SetLineWidth(1.0 if kind == "rapid" else 1.8)
        p.SetOpacity(0.45 if kind == "rapid" else 1.0)
        p.LightingOff()
        return actor

    # ---- camera ---------------------------------------------------------------------

    def view_iso(self) -> None:
        cam = self.ren.GetActiveCamera()
        cam.SetFocalPoint(0, 0, 0)
        cam.SetPosition(1, -1.4, 1.1)
        cam.SetViewUp(0, 0, 1)
        self.ren.ResetCamera()
        self.GetRenderWindow().Render()

    def view_top(self) -> None:
        cam = self.ren.GetActiveCamera()
        cam.SetFocalPoint(0, 0, 0)
        cam.SetPosition(0, 0, 1)
        cam.SetViewUp(0, 1, 0)
        self.ren.ResetCamera()
        self.GetRenderWindow().Render()

    def refresh(self) -> None:
        self.GetRenderWindow().Render()
