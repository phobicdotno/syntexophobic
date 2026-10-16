"""3D view: model, stock box and toolpaths, drawn with VTK inside a Qt widget."""

from __future__ import annotations

import math

import numpy as np

import vtkmodules.vtkInteractionStyle  # noqa: F401  (registers interactor styles)
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  (registers the OpenGL backend)
from PySide6.QtCore import QTimer, Signal
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

# phobiCCCP's 3D palette (isopreview.cpp / simulation.cpp), so both apps look alike.
VIEW_BG = (0x15, 0x17, 0x1C)       # flat, no gradient
BOARD_FILL = (0x21, 0x24, 0x2B)    # stock block
BOARD_EDGE = (0x4A, 0x52, 0x60)
AXIS_X, AXIS_Y, AXIS_Z = (0x8A, 0x4A, 0x4A), (0x4A, 0x7A, 0x4A), (0x4A, 0x5A, 0x8A)
RAPID = (0x8A, 0x94, 0xA6)         # grey, faint
TOOL = (0x7F, 0xD4, 0xFF)          # the cutter: translucent cyan
INK = (0xD8, 0xDC, 0xE4)           # the part, as the icons' ink
AMBER = (0xF2, 0xA5, 0x36)         # selection
BLUE = (0x9F, 0xC8, 0xF2)          # helper geometry (measure line)
SIM_UNCUT = (0xD9, 0xC5, 0x9E)     # light stock
SIM_DEEP = (0x5C, 0x41, 0x24)      # full-depth floor
SIM_THROUGH = (0x2B, 0x3D, 0x5A)   # slate: cut through to the spoilboard
GOUGE = (0xE0, 0x50, 0x50)
DEPTH_BUCKETS = 8                   # toolpaths go yellow at the top to red at the bottom


def _rgb(c):
    return tuple(v / 255 for v in c)


def depth_colour(f: float):
    """phobiCCCP's toolpath colour for a cut a fraction f (0 top .. 1 bottom) down,
    stepped into DEPTH_BUCKETS bands: HSV hue 0.14 (yellow) to 0 (red)."""
    import colorsys
    b = min(DEPTH_BUCKETS - 1, max(0, int(f * DEPTH_BUCKETS)))
    return colorsys.hsv_to_rgb(0.14 * (1 - b / (DEPTH_BUCKETS - 1)), 0.9, 1.0)


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
        self._render_pending = False
        self.ren = vtkRenderer()
        self.ren.SetBackground(*_rgb(VIEW_BG))
        self.GetRenderWindow().AddRenderer(self.ren)
        self.style = FusionStyle(self.ren)
        self.style.on_click = self._click
        self._edges = self._highlight = None
        self.shape = None
        self.picking = False                # only pick while the Design workspace wants it
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(self.style)
        self._model = self._stock = self._sim = self._tool = None
        self._stock_box = self._stock_fill = None
        self._paths: list[vtkActor] = []
        self._style = "edges"          # shaded | edges | wireframe
        self._model_visible = True
        axes = vtkAxesActor()
        for shaft, tip, label, c in ((axes.GetXAxisShaftProperty(), axes.GetXAxisTipProperty(),
                                      axes.GetXAxisCaptionActor2D(), AXIS_X),
                                     (axes.GetYAxisShaftProperty(), axes.GetYAxisTipProperty(),
                                      axes.GetYAxisCaptionActor2D(), AXIS_Y),
                                     (axes.GetZAxisShaftProperty(), axes.GetZAxisTipProperty(),
                                      axes.GetZAxisCaptionActor2D(), AXIS_Z)):
            shaft.SetColor(*_rgb(c))
            tip.SetColor(*_rgb(c))
            label.GetCaptionTextProperty().SetColor(*_rgb(INK))
            label.GetCaptionTextProperty().ShadowOff()
        self._axes = vtkOrientationMarkerWidget()
        self._axes.SetOrientationMarker(axes)
        self._axes.SetInteractor(self.GetRenderWindow().GetInteractor())
        self._axes.SetViewport(0.0, 0.0, 0.14, 0.2)
        self._axes.EnabledOn()
        self._axes.InteractiveOff()
        self._add_view_cube()
        self.Initialize()

    def _add_view_cube(self) -> None:
        """Fusion's ViewCube, top-right: click a face, edge or corner to snap the camera.
        VTK's camera-orientation widget drives the main camera directly."""
        self._viewcube = None
        try:
            from vtkmodules.vtkInteractionWidgets import vtkCameraOrientationWidget
            cube = vtkCameraOrientationWidget()
            cube.SetParentRenderer(self.ren)
            rep = cube.GetRepresentation()
            rep.AnchorToUpperRight()
            rep.SetXAxisColor(*_rgb(AXIS_X))
            rep.SetYAxisColor(*_rgb(AXIS_Y))
            rep.SetZAxisColor(*_rgb(AXIS_Z))
            rep.GetContainerProperty().SetColor(*_rgb(BOARD_FILL))
            for label in (rep.GetXPlusLabelProperty(), rep.GetYPlusLabelProperty(), rep.GetZPlusLabelProperty(),
                          rep.GetXMinusLabelProperty(), rep.GetYMinusLabelProperty(),
                          rep.GetZMinusLabelProperty()):
                label.SetColor(*_rgb(INK))
            cube.On()
            self._viewcube = cube
        except Exception:                 # older VTK without the widget: the axis triad stays
            pass

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
        prop.SetColor(*_rgb(INK))
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(30)
        self.ren.AddActor(self._model)
        self.shape = shape
        self._show_edges(shape)
        # refit when the part grows out of what the camera was fitted to (a design started
        # from an empty sketch would otherwise stay zoomed on the origin); shrinking or
        # editing inside keeps the view the user set
        b = poly.GetBounds()
        old = getattr(self, "_fitted", None)
        pad = 0.1 * max(b[1] - b[0], b[3] - b[2], b[5] - b[4], 1e-6)
        if old is None or any(b[i] < old[i] - pad for i in (0, 2, 4)) or any(b[i] > old[i] + pad for i in (1, 3, 5)):
            self.ren.ResetCamera(*b)
            self._fitted = b
        self._update_model_display()                 # keep the chosen visual style across rebuilds

    def _show_edges(self, shape) -> None:
        """Model edges as dark lines (CAD look); also what edge picking hits."""
        if self._edges:
            self.ren.RemoveActor(self._edges)
        self.clear_highlight()
        from OCP.TopAbs import TopAbs_EDGE
        if len(geometry.unique(shape, TopAbs_EDGE)) > 4000:
            # a finely faceted solid (a relief, an imported mesh): drawing every triangle edge
            # turns it into a wireframe, so draw only the creases; edge picking is off for it
            self._edge_polylines = []
            self._edges = self._crease_actor()
        else:
            self._edge_polylines = geometry.edge_polylines(shape)
            self._edges = self._lines_actor(self._edge_polylines, (0.10, 0.11, 0.13), 1.4)
        self.ren.AddActor(self._edges)

    def _crease_actor(self):
        from vtkmodules.vtkFiltersCore import vtkFeatureEdges
        fe = vtkFeatureEdges()
        fe.SetInputConnection(self._model.GetMapper().GetInputConnection(0, 0))
        fe.BoundaryEdgesOff()
        fe.ManifoldEdgesOff()
        fe.NonManifoldEdgesOff()
        fe.FeatureEdgesOn()
        fe.SetFeatureAngle(40)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(fe.GetOutputPort())
        mapper.ScalarVisibilityOff()
        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.10, 0.11, 0.13)
        actor.GetProperty().SetLineWidth(1.4)
        actor.GetProperty().LightingOff()
        actor.PickableOff()
        return actor

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
        if self._edges and self._edge_polylines:
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
            self._highlight = self._lines_actor(polylines, _rgb(AMBER), 4.0)
            self.ren.AddActor(self._highlight)
        self.render_soon()

    def clear_highlight(self) -> None:
        for name in ("_highlight", "_measure"):
            actor = getattr(self, name, None)
            if actor:
                self.ren.RemoveActor(actor)
                setattr(self, name, None)

    def show_measure_line(self, p, q) -> None:
        """A line between two measured points (drawn on top of the selection)."""
        self._measure = self._lines_actor([[p, q]], _rgb(BLUE), 3.0)
        self.ren.AddActor(self._measure)
        self.render_soon()

    def clear_model(self) -> None:
        for name in ("_model", "_stock", "_stock_fill", "_edges", "_highlight"):
            actor = getattr(self, name)
            if actor:
                self.ren.RemoveActor(actor)
                setattr(self, name, None)
        self._stock_box = None
        self.render_soon()

    def show_stock(self, box: geometry.Box) -> None:
        for actor in (self._stock, self._stock_fill):
            if actor:
                self.ren.RemoveActor(actor)
        cube = vtkCubeSource()
        cube.SetBounds(box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax)
        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(cube.GetOutputPort())
        # phobiCCCP's board: a dark translucent block with grey edges. Both actors share
        # the mapper, so a section cut clips them together.
        fill, edges = vtkActor(), vtkActor()
        for actor in (fill, edges):
            actor.SetMapper(mapper)
            actor.PickableOff()
        fill.GetProperty().SetColor(*_rgb(BOARD_FILL))
        fill.GetProperty().SetOpacity(0.3)
        fill.GetProperty().LightingOff()
        p = edges.GetProperty()
        p.SetRepresentationToWireframe()
        p.SetColor(*_rgb(BOARD_EDGE))
        p.SetLineWidth(1.5)
        p.LightingOff()
        self._stock_box = box
        self._stock, self._stock_fill = edges, fill
        self.ren.AddActor(fill)
        self.ren.AddActor(edges)

    def show_toolpaths(self, toolpaths, show_rapids: bool = True) -> None:
        for a in self._paths:
            self.ren.RemoveActor(a)
        self._paths = []
        # Like phobiCCCP, cuts are coloured by how deep they go (yellow at the top, red at
        # the bottom) over the depth range of all the feed moves; rapids are faint grey.
        zs = [z for tp in toolpaths for k, x, y, z, f in tp.positions() if k == "feed"]
        span = (max(zs), min(zs)) if zs else (0.0, 0.0)
        for tp in toolpaths:
            for kind in ("feed", "rapid") if show_rapids else ("feed",):
                actor = self._polyline_actor(tp, kind, span)
                if actor:
                    self._paths.append(actor)
                    self.ren.AddActor(actor)
        self.render_soon()

    def _polyline_actor(self, tp, kind: str, span=(0.0, 0.0)):
        from vtkmodules.vtkCommonCore import vtkUnsignedCharArray
        pts, lines = vtkPoints(), vtkCellArray()
        colours = vtkUnsignedCharArray()
        colours.SetNumberOfComponents(3)
        colours.SetName("depth")
        top, depth = span[0], max(span[0] - span[1], 1e-9)
        last = None
        flip = getattr(tp, "flip", None) if getattr(tp, "side", "top") == "bottom" else None
        for k, x, y, z, f in tp.positions():
            zm = z                                     # machine Z, for the depth colour
            if flip:                                   # bottom side: back into the part's frame
                y, z = 2 * flip[0] - y, 2 * flip[1] - z
            if last is not None and k == kind:
                a = pts.InsertNextPoint(last)
                b = pts.InsertNextPoint((x, y, z))
                lines.InsertNextCell(2, (a, b))
                c = RAPID if kind == "rapid" else [round(255 * v) for v in depth_colour((top - zm) / depth)]
                colours.InsertNextTuple3(*c)
            last = (x, y, z)
        if lines.GetNumberOfCells() == 0:
            return None
        poly = vtkPolyData()
        poly.SetPoints(pts)
        poly.SetLines(lines)
        poly.GetCellData().SetScalars(colours)
        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)
        mapper.SetColorModeToDirectScalars()
        mapper.SetScalarModeToUseCellData()
        actor = vtkActor()
        actor.SetMapper(mapper)
        p = actor.GetProperty()
        p.SetLineWidth(1.0 if kind == "rapid" else 1.8)
        p.SetOpacity(0.55 if kind == "rapid" else 1.0)
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
        # phobiCCCP's stock colours: light wood on top, darkening with depth to the
        # full-depth floor, slate where it is cut through; gouges in red.
        z = result.stock.ravel()
        box = self._stock_box
        ztop, zbot = (box.zmax, box.zmin) if box else (z.max(), z.min())
        thick = max(ztop - zbot, 1e-9)
        cut = z < ztop - 1e-4
        f = np.where(cut, 0.12 + 0.88 * np.clip((ztop - z) / thick, 0, 1), 0.0)[:, None]
        rgb = (np.array(SIM_UNCUT) + (np.array(SIM_DEEP) - np.array(SIM_UNCUT)) * f).round().astype(np.uint8)
        rgb[z <= zbot + 1e-3] = SIM_THROUGH
        rgb[result.gouges().ravel()] = GOUGE
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
        self._sim.GetProperty().SetAmbient(0.35)        # phobiCCCP's 0.35 + 0.65 * light shading
        self._sim.GetProperty().SetDiffuse(0.65)
        self.ren.AddActor(self._sim)
        self.render_soon()

    def clear_simulation(self) -> None:
        if self._sim:
            self.ren.RemoveActor(self._sim)
            self._sim = None
        self.show_tool(None)

    def show_tool(self, tip, diameter: float = 6.0, length: float = 25.0, shape: str = "flat",
                  k: float = 1.0) -> None:
        """A cutter standing with its tip at `tip` (x, y, z), drawn in its own shape (flat,
        ball, or a V-bit with k = width per depth); None hides it."""
        from vtkmodules.vtkFiltersModeling import vtkRotationalExtrusionFilter
        if self._tool:
            self.ren.RemoveActor(self._tool)
            self._tool = None
        if tip is not None:
            r = diameter / 2
            if shape == "ball":
                profile = [(r * math.sin(a), r - r * math.cos(a)) for a in np.linspace(0, math.pi / 2, 12)]
            elif shape == "vbit":
                profile = [(0.0, 0.0), (r, r / max(k, 1e-3))]
            else:
                profile = [(0.0, 0.0), (r, 0.0)]
            profile += [(r, max(length, profile[-1][1] + 1)), (0.0, max(length, profile[-1][1] + 1))]
            pts, line = vtkPoints(), vtkCellArray()
            for x, z in profile:
                pts.InsertNextPoint(x, 0.0, z)
            line.InsertNextCell(len(profile), list(range(len(profile))))
            poly = vtkPolyData()
            poly.SetPoints(pts)
            poly.SetLines(line)
            spin = vtkRotationalExtrusionFilter()          # the profile turned about Z
            spin.SetInputData(poly)
            spin.SetResolution(36)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(spin.GetOutputPort())
            self._tool = vtkActor()
            self._tool.SetMapper(mapper)
            self._tool.SetPosition(tip[0], tip[1], tip[2])
            self._tool.GetProperty().SetColor(*_rgb(TOOL))
            self._tool.GetProperty().SetOpacity(0.47)
            self.ren.AddActor(self._tool)
        self.render_soon()

    def set_model_visible(self, on: bool) -> None:
        self._model_visible = on
        self._update_model_display()

    def set_visual_style(self, style: str) -> None:
        """shaded (surfaces only), edges (surfaces + CAD edges), or wireframe."""
        self._style = style
        self._update_model_display()

    def _update_model_display(self) -> None:
        if self._model:
            self._model.SetVisibility(self._model_visible)
            prop = self._model.GetProperty()
            if self._style == "wireframe":
                prop.SetRepresentationToWireframe()
            else:
                prop.SetRepresentationToSurface()
        if self._edges:
            self._edges.SetVisibility(self._model_visible and self._style == "edges")
        self.render_soon()

    # ---- camera ---------------------------------------------------------------------

    def _set_view(self, position, up) -> None:
        cam = self.ren.GetActiveCamera()
        cam.SetFocalPoint(0, 0, 0)
        cam.SetPosition(*position)
        cam.SetViewUp(*up)
        self.ren.ResetCamera()
        self._fitted = self.ren.ComputeVisiblePropBounds()
        self.render_soon()

    def view_iso(self) -> None:
        self._set_view((1, -1.4, 1.1), (0, 0, 1))

    def view_top(self) -> None:
        self._set_view((0, 0, 1), (0, 1, 0))

    def view_bottom(self) -> None:
        self._set_view((0, 0, -1), (0, 1, 0))

    def view_front(self) -> None:
        self._set_view((0, -1, 0), (0, 0, 1))

    def view_back(self) -> None:
        self._set_view((0, 1, 0), (0, 0, 1))

    def view_right(self) -> None:
        self._set_view((1, 0, 0), (0, 0, 1))

    def view_left(self) -> None:
        self._set_view((-1, 0, 0), (0, 0, 1))

    def fit(self) -> None:
        """Fit everything in view (the toolbar Fit and the nav home)."""
        self.ren.ResetCamera()
        self._fitted = self.ren.ComputeVisiblePropBounds()
        self.render_soon()

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
        self.render_soon()

    def spacemouse_button(self, number: int, pressed: bool) -> None:
        if not pressed:
            return
        if number == 0:
            self.ren.ResetCamera()
            self.render_soon()
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
        self.render_soon()

    def refresh(self) -> None:
        self.render_soon()

    def render_soon(self) -> None:
        """Draw once the current change is done: loading a project or an edit changes the
        model, stock, section and toolpaths one after another, and each drew a frame of its own."""
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self, self._render_now)      # dropped if the view is gone

    def _render_now(self) -> None:
        self._render_pending = False
        self.GetRenderWindow().Render()
