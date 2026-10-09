"""3D view: model, stock box and toolpaths, drawn with VTK inside a Qt widget."""

from __future__ import annotations

import vtkmodules.vtkInteractionStyle  # noqa: F401  (registers interactor styles)
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
from PySide6.QtCore import Signal
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


class FusionStyle(vtkInteractorStyleTrackballCamera):
    """Mouse like Fusion 360: middle-drag pans, Shift+middle orbits (turntable, Z stays
    up), wheel zooms towards the cursor, double middle-click fits. Left-drag also orbits
    while there is nothing to select."""

    def __init__(self, renderer):
        super().__init__()
        self.ren = renderer
        self.on_click = None          # callback(x, y, shift) for left clicks without dragging
        self._press = None
        self._orbit = False
        self._pan = False
        self._last_middle = 0.0
        for event, handler in (("MiddleButtonPressEvent", self._middle_down),
                               ("MiddleButtonReleaseEvent", self._release),
                               ("LeftButtonPressEvent", self._left_down),
                               ("LeftButtonReleaseEvent", self._release),
                               ("MouseMoveEvent", self._move),
                               ("MouseWheelForwardEvent", lambda o, e: self.zoom_at_cursor(0.85)),
                               ("MouseWheelBackwardEvent", lambda o, e: self.zoom_at_cursor(1 / 0.85))):
            self.AddObserver(event, handler)

    def _middle_down(self, obj, event):
        import time
        now = time.monotonic()
        if now - self._last_middle < 0.35:
            self.ren.ResetCamera()
            self.GetInteractor().Render()
        self._last_middle = now
        if self.GetInteractor().GetShiftKey():
            self._orbit = True
        else:
            self._pan = True
            self.StartPan()

    def _left_down(self, obj, event):
        self._orbit = True
        self._press = self.GetInteractor().GetEventPosition()

    def _release(self, obj, event):
        if self._pan:
            self.EndPan()
        iren = self.GetInteractor()
        press = getattr(self, "_press", None)
        if self._orbit and press is not None and self.on_click:
            x, y = iren.GetEventPosition()
            if abs(x - press[0]) + abs(y - press[1]) <= 4:       # a click, not an orbit drag
                self.on_click(x, y, bool(iren.GetShiftKey()))
        self._press = None
        self._orbit = self._pan = False

    def _move(self, obj, event):
        if self._orbit:
            iren = self.GetInteractor()
            x, y = iren.GetEventPosition()
            px, py = iren.GetLastEventPosition()
            self.orbit(x - px, y - py)
        else:
            self.OnMouseMove()      # lets the built-in pan run while panning

    def orbit(self, dx: float, dy: float) -> None:
        """Turntable: spin around the world Z axis through the focal point, then tilt."""
        cam = self.ren.GetActiveCamera()
        cam.SetViewUp(0, 0, 1)
        cam.Azimuth(-dx * 0.4)
        before = cam.GetPosition()
        cam.Elevation(-dy * 0.4)
        if abs(cam.GetDirectionOfProjection()[2]) > 0.998:   # within ~3.6° of vertical: stop
            cam.SetPosition(before)
        cam.SetViewUp(0, 0, 1)
        cam.OrthogonalizeViewUp()
        self.ren.ResetCameraClippingRange()
        self.GetInteractor().Render()

    def zoom_at_cursor(self, factor: float) -> None:
        """Move the camera towards (factor < 1) or away from the point under the cursor."""
        iren = self.GetInteractor()
        x, y = iren.GetEventPosition()
        cam = self.ren.GetActiveCamera()
        fp, pos = cam.GetFocalPoint(), cam.GetPosition()
        self.ren.SetWorldPoint(*fp, 1.0)
        self.ren.WorldToDisplay()
        depth = self.ren.GetDisplayPoint()[2]
        self.ren.SetDisplayPoint(x, y, depth)
        self.ren.DisplayToWorld()
        w = self.ren.GetWorldPoint()
        target = [w[i] / w[3] for i in range(3)]
        cam.SetFocalPoint(*[t + (f - t) * factor for t, f in zip(target, fp)])
        cam.SetPosition(*[t + (p - t) * factor for t, p in zip(target, pos)])
        self.ren.ResetCameraClippingRange()
        iren.Render()


class Viewer(QVTKRenderWindowInteractor):
    picked = Signal(str, object, bool)       # "edge" | "face" | "", (x, y, z) on it, shift held

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ren = vtkRenderer()
        self.ren.SetBackground(0.13, 0.14, 0.16)
        self.ren.SetBackground2(0.24, 0.26, 0.30)
        self.ren.GradientBackgroundOn()
        self.GetRenderWindow().AddRenderer(self.ren)
        self.style = FusionStyle(self.ren)
        self.style.on_click = self._click
        self._edges = self._highlight = None
        self.shape = None
        self.picking = False                # only pick while the Design workspace wants it
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(self.style)
        self._model = self._stock = self._sim = None
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
        self.shape = shape
        self._show_edges(shape)

    def _show_edges(self, shape) -> None:
        """Model edges as dark lines (CAD look); also what edge picking hits."""
        if self._edges:
            self.ren.RemoveActor(self._edges)
        self.clear_highlight()
        self._edge_polylines = geometry.edge_polylines(shape)
        self._edges = self._lines_actor(self._edge_polylines, (0.10, 0.11, 0.13), 1.4)
        self.ren.AddActor(self._edges)

    def _lines_actor(self, polylines, colour, width):
        pts, lines = vtkPoints(), vtkCellArray()
        self._cell_edge = []
        for ei, pl in enumerate(polylines):
            ids = [pts.InsertNextPoint(p) for p in pl]
            for a, b in zip(ids, ids[1:]):
                lines.InsertNextCell(2, (a, b))
                self._cell_edge.append(ei)
        poly = vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(lines)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*colour)
        actor.GetProperty().SetLineWidth(width)
        actor.GetProperty().LightingOff()
        return actor

    # ---- picking ------------------------------------------------------------------------

    def _click(self, x: int, y: int, shift: bool) -> None:
        if not self.picking or self.shape is None:
            return
        kind, point = self.pick_at(x, y)
        self.picked.emit(kind, point, shift)

    def pick_at(self, x: int, y: int):
        """("edge" | "face" | "", world point) under display position (x, y). Edges win
        when the click is within a few pixels of one."""
        from vtkmodules.vtkRenderingCore import vtkCellPicker
        picker = vtkCellPicker()
        picker.PickFromListOn()
        if self._edges:
            picker.AddPickList(self._edges)
            picker.SetTolerance(0.006)
            if picker.Pick(x, y, 0, self.ren) and picker.GetActor() is self._edges:
                return "edge", tuple(picker.GetPickPosition())
        picker = vtkCellPicker()
        picker.PickFromListOn()
        if self._model:
            picker.AddPickList(self._model)
            picker.SetTolerance(0.0005)
            if picker.Pick(x, y, 0, self.ren) and picker.GetActor() is self._model:
                return "face", tuple(picker.GetPickPosition())
        return "", None

    def highlight(self, edges=(), faces=()) -> None:
        """Draw the given OCC edges and faces in the selection colour."""
        self.clear_highlight()
        polylines = [geometry.edge_polylines(e)[0] for e in edges]
        for f in faces:
            polylines += geometry.edge_polylines(f)          # outline the face
        if polylines:
            self._highlight = self._lines_actor(polylines, (1.0, 0.69, 0.23), 4.0)
            self.ren.AddActor(self._highlight)
        self.GetRenderWindow().Render()

    def clear_highlight(self) -> None:
        for name in ("_highlight", "_measure"):
            actor = getattr(self, name, None)
            if actor:
                self.ren.RemoveActor(actor)
                setattr(self, name, None)

    def show_measure_line(self, p, q) -> None:
        """A line between two measured points (drawn on top of the selection)."""
        self._measure = self._lines_actor([[p, q]], (0.35, 0.85, 1.0), 3.0)
        self.ren.AddActor(self._measure)
        self.GetRenderWindow().Render()

    def clear_model(self) -> None:
        for name in ("_model", "_stock", "_edges", "_highlight"):
            actor = getattr(self, name)
            if actor:
                self.ren.RemoveActor(actor)
                setattr(self, name, None)
        self.GetRenderWindow().Render()

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

    def show_simulation(self, result) -> None:
        """Machined stock as a shaded height field; gouges in red."""
        import numpy as np
        from vtkmodules.util.numpy_support import numpy_to_vtk
        from vtkmodules.vtkCommonDataModel import vtkImageData
        from vtkmodules.vtkFiltersGeometry import vtkImageDataGeometryFilter
        from vtkmodules.vtkFiltersGeneral import vtkWarpScalar

        self.clear_simulation()
        g = result.grid
        img = vtkImageData()
        img.SetDimensions(g.nx, g.ny, 1)
        img.SetOrigin(g.x0, g.y0, 0)
        img.SetSpacing(g.res, g.res, 1)
        h = numpy_to_vtk(np.ascontiguousarray(result.stock.ravel()), deep=True)
        h.SetName("height")
        img.GetPointData().SetScalars(h)
        rgb = np.empty((g.ny * g.nx, 3), np.uint8)
        rgb[:] = (205, 170, 120)                                  # cut stock: wood
        top = np.isclose(result.stock.ravel(), result.stock.max())
        rgb[top] = (170, 140, 100)                                # untouched stock top
        rgb[result.gouges().ravel()] = (235, 50, 50)              # gouge
        colours = numpy_to_vtk(rgb, deep=True)
        colours.SetName("colour")
        img.GetPointData().AddArray(colours)
        geom = vtkImageDataGeometryFilter()
        geom.SetInputData(img)
        warp = vtkWarpScalar()
        warp.SetInputConnection(geom.GetOutputPort())
        warp.SetNormal(0, 0, 1)
        warp.UseNormalOn()
        normals = vtkPolyDataNormals()
        normals.SetInputConnection(warp.GetOutputPort())
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray("colour")
        mapper.SetColorModeToDirectScalars()
        mapper.ScalarVisibilityOn()
        self._sim = vtkActor()
        self._sim.SetMapper(mapper)
        self._sim.GetProperty().SetSpecular(0.1)
        self.ren.AddActor(self._sim)
        self.GetRenderWindow().Render()

    def clear_simulation(self) -> None:
        if self._sim:
            self.ren.RemoveActor(self._sim)
            self._sim = None

    def set_model_visible(self, on: bool) -> None:
        for actor in (self._model, self._edges):
            if actor:
                actor.SetVisibility(on)
            self.GetRenderWindow().Render()

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

    # ---- 3Dconnexion ------------------------------------------------------------------

    SPACE_SPEED = 1.0          # overall sensitivity

    def spacemouse_motion(self, m) -> None:
        """Six-axis input: twist spins about Z, tilt tilts the view, slide pans, push zooms.

        Values are about ±350 at full deflection; scaling by the event period keeps the
        speed independent of how often the daemon reports.
        """
        cam = self.ren.GetActiveCamera()
        dt = max(m.period, 1) / 1000.0 * self.SPACE_SPEED
        dist = cam.GetDistance()
        # orbit (degrees/s at full deflection: about 120)
        self.style.orbit(-m.ry * dt * 0.95, m.rx * dt * 0.95)
        # pan along the screen axes, proportional to how far away the model is
        fp, pos = list(cam.GetFocalPoint()), list(cam.GetPosition())
        up = cam.GetViewUp()
        d = cam.GetDirectionOfProjection()
        right = (d[1] * up[2] - d[2] * up[1], d[2] * up[0] - d[0] * up[2], d[0] * up[1] - d[1] * up[0])
        k = dist * dt * 0.004
        move = [-(right[i] * m.x + up[i] * m.y) * k for i in range(3)]
        cam.SetFocalPoint(*[fp[i] + move[i] for i in range(3)])
        cam.SetPosition(*[pos[i] + move[i] for i in range(3)])
        # push the cap away (-z) to zoom in
        if m.z:
            cam.Dolly(1.0 + max(-0.5, min(0.5, -m.z * dt * 0.004)))
        self.ren.ResetCameraClippingRange()
        self.GetRenderWindow().Render()

    def spacemouse_button(self, number: int, pressed: bool) -> None:
        if not pressed:
            return
        if number == 0:
            self.ren.ResetCamera()
            self.GetRenderWindow().Render()
        elif number == 1:
            self.view_iso()

    # ---- section ----------------------------------------------------------------------

    def set_section(self, axis: str | None, fraction: float = 0.5) -> None:
        """Cut the model, edges and simulated stock with a plane across `axis` (X, Y, Z),
        at `fraction` (0..1) of the model's size; None removes the cut."""
        from vtkmodules.vtkCommonDataModel import vtkPlane
        actors = [a for a in (self._model, self._edges, self._sim, self._stock) if a]
        for a in actors:
            a.GetMapper().RemoveAllClippingPlanes()
        if axis and self._model:
            b = self._model.GetBounds()
            k = "XYZ".index(axis)
            pos = b[2 * k] + (b[2 * k + 1] - b[2 * k]) * fraction
            plane = vtkPlane()
            origin = [0.0, 0.0, 0.0]
            origin[k] = pos
            normal = [0.0, 0.0, 0.0]
            normal[k] = -1.0                     # keep the side below the plane
            plane.SetOrigin(*origin)
            plane.SetNormal(*normal)
            for a in actors:
                a.GetMapper().AddClippingPlane(plane)
        self.GetRenderWindow().Render()

    def refresh(self) -> None:
        self.GetRenderWindow().Render()
