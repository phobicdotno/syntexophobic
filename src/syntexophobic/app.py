"""Main window: 3D view, job settings, and the git-style history panel."""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QSlider, QSplitter, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
)

from . import geometry
from .job import Operation, Tool
from .project import Project

CHOICES = {
    "type": ["rough", "adaptive", "finish", "profile", "parallel", "drill", "bore"],
    "up": list(geometry.UP_AXES),
    "zero": ["stock", "model"],
    "post": ["carbide", "grbl"],
    "shape": ["flat", "ball"],
    "raster": ["X", "Y"],
    "material": None,   # filled from feeds.MATERIALS below
}
from .feeds import MATERIALS  # noqa: E402

CHOICES["material"] = list(MATERIALS)
LABELS = {
    "stepover": "Stepover (× tool Ø)", "leave": "Leave on walls", "feed": "Feed (mm/min)",
    "plunge": "Plunge (mm/min)", "bottom": "Bottom Z", "rpm": "Spindle (rpm)",
    "safe_z": "Safe height", "top_offset": "Material above part", "margin": "Margin around part",
    "width": "Stock X size (0 = margin)", "depth": "Stock Y size (0 = margin)", "height": "Stock Z size (0 = to part bottom)",
    "rest_from": "Rest after tool", "hole_min": "Smallest hole Ø", "hole_max": "Largest hole Ø",
    "material": "Stock material", "up": "Model axis facing up", "ramp_angle": "Ramp angle (°)", "tabs": "Tabs per outline", "zero": "Work zero", "post": "Post-processor",
}
AUTO = -99999.0   # spin box value shown as "auto" for an empty optional number
OPTIONAL = {"bottom": "model bottom", "hole_min": "auto", "hole_max": "auto", "rest_from": "off"}
# Operation fields that mean nothing for a given type are hidden from the form.
HIDDEN = {
    "rough": {"tabs", "tab_width", "tab_height", "hole_min", "hole_max", "raster"},
    "adaptive": {"tabs", "tab_width", "tab_height", "hole_min", "hole_max", "rest_from", "raster"},
    "parallel": {"stepdown", "ramp_angle", "tabs", "tab_width", "tab_height", "hole_min", "hole_max", "rest_from"},
    "finish": {"stepover", "hole_min", "hole_max", "rest_from", "raster"},
    "profile": {"stepover", "hole_min", "hole_max", "rest_from", "raster"},
    "drill": {"stepover", "leave", "feed", "ramp_angle", "tabs", "tab_width", "tab_height", "rest_from", "raster"},
    "bore": {"tabs", "tab_width", "tab_height", "rest_from", "raster"},
}


ICONS = Path(__file__).with_name("assets") / "icons"


def mono_icon(name: str, palette: QPalette) -> QIcon:
    """A line icon drawn in the palette's text colour (and a dimmed copy for disabled)."""
    from PySide6.QtSvg import QSvgRenderer

    svg = (ICONS / f"{name}.svg").read_text()
    icon = QIcon()
    for mode, role in ((QIcon.Normal, QPalette.Active), (QIcon.Disabled, QPalette.Disabled)):
        colour = palette.color(role, QPalette.WindowText).name()
        renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", colour).encode()))
        for size in (16, 22, 24, 32, 36, 48, 64):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pm, mode)
    return icon


class DataForm(QWidget):
    """Edits a dataclass in place: one row per field, typed controls, emits `changed`."""

    changed = Signal()

    def __init__(self, obj=None, parent=None):
        super().__init__(parent)
        self.expressions: set[str] = set()      # fields edited as "number or expression"
        self.check_expression = None            # callback(text) -> error message or None
        self.layout_ = QFormLayout(self)
        self.layout_.setLabelAlignment(Qt.AlignRight)
        self.obj = None
        if obj is not None:
            self.set(obj)

    def set(self, obj, only: list[str] | None = None) -> None:
        """Show obj's fields: all of them, the ones in `only`, minus HIDDEN for operations."""
        while self.layout_.rowCount():
            self.layout_.removeRow(0)
        self.obj = obj
        self.only = only
        if obj is None:
            return
        hidden = HIDDEN.get(getattr(obj, "type", None), set()) if isinstance(obj, Operation) else set()
        for f in fields(obj):
            if f.name == "id" or f.name in hidden or (only is not None and f.name not in only):
                continue
            w = self._editor(f.name, getattr(obj, f.name), f.type)
            label = LABELS.get(f.name, f.name.replace("_", " ").capitalize())
            if f.name == "stepdown" and getattr(obj, "type", "") == "drill":
                label = "Peck depth"
            self.layout_.addRow(label, w)

    def _editor(self, name, value, ftype):
        def store(v):
            setattr(self.obj, name, v)
            self.changed.emit()
            if name == "type":            # different fields apply: rebuild the form
                QTimer.singleShot(0, lambda: self.set(self.obj, self.only))

        if name in self.expressions:
            w = QLineEdit(f"{value:g}" if isinstance(value, (int, float)) else str(value))
            w.setToolTip("A number, or an expression with parameters, e.g. width / 2")

            def commit():
                text = w.text().strip()
                try:
                    num = float(text)
                    store(int(round(num)) if "int" in str(ftype) else num)
                    w.setStyleSheet("")
                except ValueError:
                    err = self.check_expression(text) if self.check_expression else None
                    w.setStyleSheet("color: #e5534b" if err else "")
                    w.setToolTip(err or f"= {text}")
                    if not err:
                        store(text)
            w.editingFinished.connect(commit)
            return w
        if name in CHOICES:
            w = QComboBox()
            w.addItems(CHOICES[name])
            w.setCurrentText(str(value))
            w.currentTextChanged.connect(store)
        elif isinstance(value, bool):
            w = QCheckBox()
            w.setChecked(value)
            w.toggled.connect(store)
        elif name in OPTIONAL and "int" in str(ftype):
            w = QSpinBox()
            w.setRange(0, 1000)
            w.setSpecialValueText(OPTIONAL[name])      # 0 shows "off"
            w.setValue(value or 0)
            w.valueChanged.connect(lambda v: store(v or None))
        elif isinstance(value, int) and "float" not in str(ftype):
            w = QSpinBox()
            w.setRange(0, 100000)
            w.setValue(value)
            w.valueChanged.connect(store)
        elif isinstance(value, float) or "float" in str(ftype):
            w = QDoubleSpinBox()
            w.setDecimals(3)
            w.setRange(AUTO if name in OPTIONAL else -10000, 100000)
            w.setSingleStep(0.1 if name in ("stepover", "leave") else 0.5)
            if name in OPTIONAL:
                w.setSpecialValueText(OPTIONAL[name])
            w.setValue(AUTO if value is None else value)
            w.valueChanged.connect(lambda v: store(None if v == AUTO else v))
        else:
            w = QLineEdit(str(value))
            w.editingFinished.connect(lambda: store(w.text()))
        return w


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Runs fn() off the UI thread; result or traceback comes back through signals."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn())
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from .viewer import Viewer   # VTK import after QApplication exists

        self.project: Project | None = None
        self.toolpaths = []
        self.pool = QThreadPool.globalInstance()
        self.setWindowTitle("Syntexophobic")
        self.resize(1500, 920)

        self.viewer = Viewer(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.viewer)
        self.setCentralWidget(self.stack)
        self.part = None                     # the designed Part when the project has model.toml
        self.sketch_page = None
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(350)
        self._rebuild_timer.timeout.connect(self._rebuild_part)
        self._build_toolbar()
        self._build_job_dock()
        self._build_history_dock()
        self._build_design_dock()
        self.status = QLabel("Open a STEP file to start a project.")
        self.statusBar().addWidget(self.status, 1)
        from .spacemouse import SpaceMouse
        self.space_label = QLabel()
        self.statusBar().addPermanentWidget(self.space_label)
        self.spacemouse = SpaceMouse(parent=self)
        self.spacemouse.motion.connect(self.viewer.spacemouse_motion)
        self.spacemouse.button.connect(self.viewer.spacemouse_button)
        self.spacemouse.connected.connect(self._space_status)
        self._space_status(self.spacemouse.is_connected)
        self.design_panel.status.connect(self.status.setText)
        self._enable(False)

    # ---- layout -----------------------------------------------------------------------

    def _build_toolbar(self):
        """Toolbar plus menu bar. View > Toolbar switches icons / large icons / text only."""
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setObjectName("main-toolbar")
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(tb)
        self.toolbar = tb
        bar = self.menuBar()
        m_file, m_edit = bar.addMenu("&File"), bar.addMenu("&Edit")
        m_paths, m_view = bar.addMenu("&Toolpaths"), bar.addMenu("&View")
        self.a_undo = m_edit.addAction("Undo")
        self.a_undo.setShortcut(QKeySequence.Undo)
        self.a_undo.triggered.connect(lambda: self.undo_redo(False))
        self.a_redo = m_edit.addAction("Redo")
        self.a_redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self.a_redo.triggered.connect(lambda: self.undo_redo(True))

        def act(text, slot, icon, key=None, menu=None, checkable=False, toolbar=True):
            a = QAction(text, self)
            a.setIcon(mono_icon(icon, self.palette()))
            a.setIconText(text.rstrip("…"))
            a.setToolTip(text.rstrip("…") + (f"  ({QKeySequence(key).toString()})" if key else ""))
            if key:
                a.setShortcut(QKeySequence(key))
            if checkable:
                a.setCheckable(True)
                a.setChecked(True)
                a.toggled.connect(slot)
            else:
                a.triggered.connect(slot)
            if toolbar:
                tb.addAction(a)
            if menu is not None:
                menu.addAction(a)
            return a

        self.c_workspace = QComboBox()
        self.c_workspace.addItems(["Design", "Manufacture"])
        self.c_workspace.setToolTip("Workspace")
        self.c_workspace.currentTextChanged.connect(self.set_workspace)
        tb.addWidget(self.c_workspace)
        tb.addSeparator()
        new_design = m_file.addAction("New design…")
        new_design.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_design.triggered.connect(self.new_design)
        act("New from STEP…", self.new_project, "new", "Ctrl+N", m_file)
        act("Open project…", self.open_project, "open", "Ctrl+O", m_file)
        tb.addSeparator()
        self.a_generate = act("Generate toolpaths", self.generate, "generate", "Ctrl+G", m_paths)
        self.a_simulate = act("Simulate", self.simulate, "simulate", "Ctrl+R", m_paths)
        self.a_export = act("Export G-code…", self.export_gcode, "export", "Ctrl+E", m_file)
        m_file.addSeparator()
        for text, slot in (("Import DXF as sketch…", self.import_dxf), ("Export sketch as DXF…", self.export_dxf),
                           ("Export model (STEP / STL)…", self.export_model)):
            m_file.addAction(text).triggered.connect(slot)
        m_file.addSeparator()
        quit_ = m_file.addAction("Quit")
        quit_.setShortcut(QKeySequence("Ctrl+Q"))
        quit_.triggered.connect(self.close)
        tb.addSeparator()
        act("Iso view", lambda: self.viewer.view_iso(), "iso", "1", m_view)
        act("Top view", lambda: self.viewer.view_top(), "top", "2", m_view)
        tb.addSeparator()
        m_view.addSeparator()
        self.c_paths = act("Toolpaths", lambda _: self._redraw_paths(), "toolpaths", "T", m_view, True)
        self.c_rapids = act("Rapids", lambda _: self._redraw_paths(), "rapids", "R", m_view, True)
        self.c_model = act("Model", lambda on: self.viewer.set_model_visible(on), "model", "M", m_view, True)

        m_view.addSeparator()
        m_section = m_view.addMenu("Section")
        group = QActionGroup(self)
        for label in ("Off", "X", "Y", "Z"):
            a = m_section.addAction(label)
            a.setCheckable(True)
            a.setChecked(label == "Off")
            group.addAction(a)
            a.triggered.connect(lambda _=False, ax=label: self.set_section(None if ax == "Off" else ax))
        self.section_slider = QSlider(Qt.Horizontal)
        self.section_slider.setRange(0, 1000)
        self.section_slider.setValue(500)
        self.section_slider.setMaximumWidth(220)
        self.section_slider.setToolTip("Section position")
        self.section_slider.valueChanged.connect(lambda v: self.set_section(self.section_axis))
        self.section_slider_action = tb.addWidget(self.section_slider)
        self.section_slider_action.setVisible(False)
        self.section_axis = None
        self.a_measure = m_view.addAction("Measure")
        self.a_measure.setCheckable(True)
        self.a_measure.setShortcut(QKeySequence("D"))
        self.a_measure.toggled.connect(self._measure_mode)
        self._measure_picks = []
        m_view.addSeparator()
        m_style = m_view.addMenu("Toolbar")
        group = QActionGroup(self)
        self.toolbar_styles = {}
        for key, label in (("icons", "Icons"), ("large", "Large icons"), ("text", "Text only")):
            a = m_style.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_toolbar_style(k))
            group.addAction(a)
            self.toolbar_styles[key] = a
        self.set_toolbar_style(QSettings().value("toolbar_style", "icons"))

    def set_toolbar_style(self, key: str):
        tb = self.toolbar
        if key == "text":
            tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        else:
            tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
            size = 36 if key == "large" else 22
            tb.setIconSize(QSize(size, size))
        if key in self.toolbar_styles:
            self.toolbar_styles[key].setChecked(True)
            QSettings().setValue("toolbar_style", key)

    def _build_job_dock(self):
        dock = QDockWidget("Job", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        body = QWidget()
        col = QVBoxLayout(body)

        setup = QGroupBox("Stock and machine")
        sl = QVBoxLayout(setup)
        self.f_stock, self.f_machine = DataForm(), DataForm()
        sl.addWidget(self.f_stock)
        sl.addWidget(self.f_machine)
        col.addWidget(setup)

        tools = QGroupBox("Tools")
        tl = QVBoxLayout(tools)
        self.l_tools = QListWidget()
        self.l_tools.setMaximumHeight(80)
        self.l_tools.currentRowChanged.connect(self._pick_tool)
        self.f_tool = DataForm()
        row = QHBoxLayout()
        b_add = QPushButton("Add tool")
        b_add.clicked.connect(self.add_tool)
        b_lib = QPushButton("From library…")
        b_lib.clicked.connect(self.tool_from_library)
        b_save = QPushButton("Save to library")
        b_save.clicked.connect(self.tool_to_library)
        for b in (b_add, b_lib, b_save):
            row.addWidget(b)
        row.addStretch()
        tl.addWidget(self.l_tools)
        tl.addLayout(row)
        tl.addWidget(self.f_tool)
        col.addWidget(tools)

        ops = QGroupBox("Operations (run top to bottom)")
        ol = QVBoxLayout(ops)
        self.l_ops = QListWidget()
        self.l_ops.setMaximumHeight(110)
        self.l_ops.currentRowChanged.connect(self._pick_op)
        row = QHBoxLayout()
        for text, slot in (("Add", self.add_op), ("Remove", self.remove_op),
                           ("Up", lambda: self.move_op(-1)), ("Down", lambda: self.move_op(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.f_op = DataForm()
        b_feeds = QPushButton("Suggest feeds && speeds")
        b_feeds.setToolTip("Fill feed, plunge, stepdown and stepover for this operation from the "
                           "stock material, the tool and the spindle speed. A cautious starting point.")
        b_feeds.clicked.connect(self.suggest_feeds)
        ol.addWidget(self.l_ops)
        ol.addLayout(row)
        ol.addWidget(b_feeds)
        ol.addWidget(self.f_op)
        col.addWidget(ops)
        col.addStretch()

        for f in (self.f_stock, self.f_machine, self.f_tool, self.f_op):
            f.changed.connect(self._job_edited)
        dock.setWidget(body)
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.job_dock = dock

    def _build_history_dock(self):
        dock = QDockWidget("History", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        body = QWidget()
        col = QVBoxLayout(body)

        row = QHBoxLayout()
        row.addWidget(QLabel("Variant"))
        self.c_branch = QComboBox()
        self.c_branch.activated.connect(self._switch_branch)
        row.addWidget(self.c_branch, 1)
        b_new = QPushButton("New variant…")
        b_new.clicked.connect(self.new_branch)
        row.addWidget(b_new)
        col.addLayout(row)

        row = QHBoxLayout()
        self.e_message = QLineEdit()
        self.e_message.setPlaceholderText("What changed? e.g. 3 mm endmill for the slots")
        self.e_message.returnPressed.connect(self.save_version)
        self.b_save = QPushButton("Save version")
        self.b_save.clicked.connect(self.save_version)
        row.addWidget(self.e_message, 1)
        row.addWidget(self.b_save)
        col.addLayout(row)

        split = QSplitter(Qt.Vertical)
        self.l_versions = QListWidget()
        self.l_versions.currentRowChanged.connect(self._show_diff)
        split.addWidget(self.l_versions)
        self.t_diff = QPlainTextEdit()
        self.t_diff.setReadOnly(True)
        self.t_diff.setFont(QFont("monospace", 9))
        self.t_diff.setPlaceholderText("Pick a version to see what is different in the job now.")
        split.addWidget(self.t_diff)
        col.addWidget(split, 1)

        b_restore = QPushButton("Restore this version")
        b_restore.clicked.connect(self.restore_version)
        col.addWidget(b_restore)
        dock.setWidget(body)
        dock.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.history_dock = dock

    def _space_status(self, on: bool):
        self.space_label.setText("3Dconnexion: ready" if on else "")
        self.space_label.setToolTip("Connected to spacenavd. Twist to spin, tilt to tilt, slide to pan, "
                                    "push to zoom. Left button fits, right button iso view." if on else "")

    def _redraw_paths(self):
        paths = self.toolpaths if self.c_paths.isChecked() else []
        self.viewer.show_toolpaths(paths, self.c_rapids.isChecked())

    def _enable(self, on: bool):
        for w in (self.a_generate, self.a_simulate, self.a_export, self.job_dock, self.history_dock,
                  self.design_dock):
            w.setEnabled(on)

    # ---- design workspace ------------------------------------------------------------

    def _build_design_dock(self):
        from .design_ui import DesignPanel
        dock = QDockWidget("Design", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.design_panel = DesignPanel()
        self.design_panel.changed.connect(self._design_changed)
        self.design_panel.edit_sketch.connect(self.open_sketch)
        self.design_panel.selection.connect(self._show_selection)
        self.viewer.picked.connect(self._picked)
        self.design_note = QLabel("This project machines an imported STEP file.\n"
                                  "Use File > New design… to model a part here.")
        self.design_note.setWordWrap(True)
        body = QWidget()
        col = QVBoxLayout(body)
        col.addWidget(self.design_note)
        col.addWidget(self.design_panel)
        dock.setWidget(body)
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.design_dock = dock

    def set_section(self, axis):
        self.section_axis = axis
        self.section_slider_action.setVisible(axis is not None)
        self.viewer.set_section(axis, self.section_slider.value() / 1000)
        if axis:
            self.status.setText(f"Section across {axis}: drag the slider in the toolbar.")

    def _picked(self, kind, point, shift):
        if self.a_measure.isChecked():
            self._measure_pick(kind, point)
        else:
            self.design_panel.picked(kind, point, shift)

    def _measure_mode(self, on: bool):
        self._measure_picks = []
        self.viewer.picking = on or self.c_workspace.currentText() == "Design"
        self.viewer.clear_highlight()
        self.viewer.refresh()
        self.status.setText("Measure: click an edge or face, then another to get the distance. D ends."
                            if on else "")

    def _measure_pick(self, kind, point):
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from .model import _nearest
        if not kind or self.viewer.shape is None:
            return
        items = geometry.unique(self.viewer.shape, TopAbs_EDGE if kind == "edge" else TopAbs_FACE)
        item = _nearest(items, point)
        if item is None:
            return
        self._measure_picks = (self._measure_picks + [item])[-2:]
        edges = [i for i in self._measure_picks if i.ShapeType() == TopAbs_EDGE]
        faces = [i for i in self._measure_picks if i.ShapeType() == TopAbs_FACE]
        self.viewer.highlight(edges=edges, faces=faces)
        if len(self._measure_picks) == 1:
            self.status.setText("Measure: " + geometry.describe(item) + ". Click another to get the distance.")
            return
        a, b = self._measure_picks
        d, p, q = geometry.distance(a, b)
        self.viewer.show_measure_line(p, q)
        self.status.setText(f"Distance {d:.3f} mm   (ΔX {q[0] - p[0]:+.3f}, ΔY {q[1] - p[1]:+.3f}, "
                            f"ΔZ {q[2] - p[2]:+.3f})   ·   {geometry.describe(a)}  /  {geometry.describe(b)}")
        self._measure_picks = []

    def _show_selection(self, item):
        """Highlight what the current feature uses (picked edges / faces) or a picked face."""
        from .model import ModelError, select_edges, select_faces
        from OCP.TopAbs import TopAbs_FACE
        self.viewer.clear_highlight()
        if self.part is None or item is None:
            self.viewer.refresh()
            return
        try:
            if isinstance(item, tuple):
                from .model import _nearest
                face = _nearest(geometry.unique(self.part.build(), TopAbs_FACE), item[1])
                self.viewer.highlight(faces=[face] if face is not None else [])
                return
            row = self.part.features.index(item)
            if item.type in ("fillet", "chamfer") and item.edges.startswith("@"):
                self.viewer.highlight(edges=select_edges(self.part.build(row), item.edges))
            elif item.type == "shell" and item.faces.startswith("@"):
                self.viewer.highlight(faces=select_faces(self.part.build(row), item.faces))
            else:
                self.viewer.refresh()
        except (ModelError, ValueError) as e:
            self.status.setText(str(e))

    def set_workspace(self, name: str):
        design = name == "Design"
        self.viewer.picking = design or self.a_measure.isChecked()
        self.design_dock.setVisible(design)
        self.job_dock.setVisible(not design)
        for a in (self.a_generate, self.a_simulate, self.a_export, self.c_paths, self.c_rapids):
            a.setVisible(not design)
        if design:
            self.viewer.show_toolpaths([], False)
            self.viewer.clear_simulation()
            self.viewer.set_model_visible(True)
        else:
            if self.stack.currentWidget() is not self.viewer:
                self.finish_sketch()
            self._redraw_paths()

    def new_design(self):
        from .model import Part
        folder = QFileDialog.getExistingDirectory(self, "Folder for the new design (pick or create an empty one)",
                                                  str(Path.home()))
        if not folder:
            return
        name, ok = QInputDialog.getText(self, "New design", "Part name:", text="part")
        if not ok or not name.strip():
            return
        target = Path(folder)
        if any(target.iterdir()):
            target = target / name.strip()
        self.load(Project.create_design(target, name.strip()))
        self.c_workspace.setCurrentText("Design")

    def undo_redo(self, redo: bool):
        if self.project is None:
            return
        if self.sketch_page is not None:
            self.finish_sketch()                 # keep what was drawn as its own step first
        done = self.undo.redo() if redo else self.undo.undo()
        if not done:
            self.status.setText("Nothing to redo." if redo else "Nothing to undo.")
            return
        self.project.reload()
        if self.part is not None:
            from .model import Part
            row = self.design_panel.l_features.currentRow()
            self.part = Part.load(self.project.folder / self.project.job.model)
            self.design_panel.set_part(self.part)
            self.design_panel.l_features.setCurrentRow(row)
        self._fill_job()
        self._refresh_model()
        self._refresh_history_status()
        self.toolpaths = []
        self.status.setText("Redone." if redo else "Undone.")

    def import_dxf(self):
        from . import dxf
        if self.part is None:
            QMessageBox.information(self, "Import DXF", "Open or start a design first (File > New design…).")
            return
        path, _ = QFileDialog.getOpenFileName(self, "DXF drawing", str(Path.home()), "DXF (*.dxf *.DXF)")
        if not path:
            return
        name = Path(path).stem.replace(" ", "_")
        while name in self.part.sketches:
            name += "_"
        try:
            sk = dxf.import_dxf(path, name=name)
        except Exception as e:
            QMessageBox.critical(self, "Import DXF", f"Could not read the drawing: {e}")
            return
        self.part.sketches[name] = sk
        self._design_changed()
        self.c_workspace.setCurrentText("Design")
        self.open_sketch(name)
        self.status.setText(f"Imported {Path(path).name}: {len(sk.lines)} lines, {len(sk.arcs)} arcs, "
                            f"{len(sk.circles)} circles, {len(sk.splines)} splines, {len(sk.profiles())} closed profiles.")

    def export_dxf(self):
        from . import dxf
        if self.part is None or not self.part.sketches:
            QMessageBox.information(self, "Export DXF", "There is no sketch to export.")
            return
        if self.sketch_page is not None:
            sk = self.sketch_view.sketch
        else:
            name, ok = QInputDialog.getItem(self, "Export DXF", "Sketch", sorted(self.part.sketches), 0, False)
            if not ok:
                return
            sk = self.part.sketches[name]
        path, _ = QFileDialog.getSaveFileName(self, "Save DXF", str(self.project.folder / f"{sk.name}.dxf"), "DXF (*.dxf)")
        if path:
            dxf.export_dxf(sk, path)
            self.status.setText(f"Saved {path}")

    def export_model(self):
        if self.project is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export model", str(self.project.folder / f"{self.project.job.name}.step"),
                                              "STEP (*.step *.stp);;STL (*.stl)")
        if not path:
            return
        from .model import ModelError
        try:
            if self.part is not None:
                (self.part.export_stl if path.lower().endswith(".stl") else self.part.export_step)(path)
            else:
                from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
                w = STEPControl_Writer()
                w.Transfer(self.project.shape, STEPControl_AsIs)
                w.Write(path)
        except ModelError as e:
            QMessageBox.critical(self, "Export model", str(e))
            return
        self.status.setText(f"Saved {path}")

    def _design_changed(self):
        if self.part is None:
            return
        self.part.save(self.project.folder)
        self.undo.record()
        self._refresh_history_status()
        self._rebuild_timer.start()

    def _rebuild_part(self):
        from .model import ModelError
        try:
            shape = self.part.build()
        except ModelError as e:
            self.status.setText(f"Model: {e}")
            return
        self.project.reload()
        self.viewer.show_model(shape)
        self.viewer.show_stock(self.project.setup().stock)
        self.viewer.set_section(self.section_axis, self.section_slider.value() / 1000)
        self.viewer.refresh()
        self.toolpaths = []
        b = geometry.bounding_box(shape)
        self.status.setText(f"Part {b.xmax - b.xmin:.1f} × {b.ymax - b.ymin:.1f} × {b.zmax - b.zmin:.1f} mm")

    def open_sketch(self, name: str):
        from .design_ui import sketch_toolbar
        from .sketcher import SketchView
        self.finish_sketch(save=False)
        sk = self.part.sketches[name]
        try:
            sk.param_values = self.part.parameter_values()
        except Exception:
            sk.param_values = {}
        view = SketchView(sk)
        view.changed.connect(self._design_changed)
        view.status.connect(lambda t: self.status.setText(f"{name}: {t}"))
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(sketch_toolbar(view, self.finish_sketch))
        col.addWidget(view)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.sketch_page, self.sketch_view = page, view
        view.solve_and_draw()
        QTimer.singleShot(0, view.fit)

    def finish_sketch(self, save: bool = True):
        if self.sketch_page is None:
            return
        self.stack.setCurrentWidget(self.viewer)
        self.stack.removeWidget(self.sketch_page)
        self.sketch_page.deleteLater()
        self.sketch_page = None
        if save:
            self._design_changed()

    # ---- project ----------------------------------------------------------------------

    def new_project(self):
        step, _ = QFileDialog.getOpenFileName(self, "STEP model", str(Path.home()), "STEP (*.step *.stp *.STEP *.STP)")
        if not step:
            return
        default = str(Path(step).with_suffix("")) + "-cam"
        folder = QFileDialog.getExistingDirectory(self, "Folder for the new project (pick or create an empty one)", str(Path(default).parent))
        if not folder:
            return
        target = Path(folder)
        if any(target.iterdir()):
            target = target / (Path(step).stem + "-cam")
        self.load(Project.create(target, step))

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Project folder (contains job.toml)", str(Path.home()))
        if folder:
            self.load_path(folder)

    def load_path(self, path: str):
        p = Path(path)
        try:
            if p.suffix.lower() in (".step", ".stp"):
                self.load(Project.create(p.with_name(p.stem + "-cam"), p))
            else:
                self.load(Project(p))
        except Exception as e:
            QMessageBox.critical(self, "Could not open", str(e))

    def load(self, project: Project):
        from .project import Undo
        self.finish_sketch(save=False)
        self.project = project
        self.undo = Undo(project.folder)
        self.toolpaths = []
        if project.job.model.endswith(".toml"):
            from .model import Part
            self.part = Part.load(project.folder / project.job.model)
            self.design_panel.set_part(self.part)
        else:
            self.part = None
        self.design_panel.setVisible(self.part is not None)
        self.design_note.setVisible(self.part is None)
        self.setWindowTitle(f"Syntexophobic — {project.job.name}")
        self._fill_job()
        self._refresh_model()
        self._refresh_history()
        self._enable(True)
        self.c_workspace.setCurrentText("Design" if self.part is not None else "Manufacture")
        self.set_workspace(self.c_workspace.currentText())
        self.viewer.view_iso()

    def _refresh_model(self):
        from .model import ModelError
        p = self.project
        try:
            shape = p.shape
        except ModelError as e:
            self.viewer.clear_model()
            self.status.setText(f"{e}. Start with New sketch…, draw a closed shape, then Extrude.")
            return
        self.viewer.show_model(shape)
        self.viewer.set_section(self.section_axis, self.section_slider.value() / 1000)
        s = p.setup()
        self.viewer.show_stock(s.stock)
        self.viewer.show_toolpaths([], False)
        b = s.part
        self.status.setText(f"Part {b.xmax - b.xmin:.1f} × {b.ymax - b.ymin:.1f} × {b.zmax - b.zmin:.1f} mm. "
                            "Press Generate to compute toolpaths.")

    # ---- job editing --------------------------------------------------------------------

    def _fill_job(self):
        job = self.project.job
        self.f_stock.set(job.stock)
        self.f_machine.set(job.machine)
        self.l_tools.clear()
        for t in job.tools:
            self.l_tools.addItem(f"T{t.id}  {t.name}  Ø{t.diameter:g}")
        self.l_tools.setCurrentRow(0)
        self.l_ops.blockSignals(True)
        self.l_ops.clear()
        for op in job.operations:
            self.l_ops.addItem(self._op_label(op))
        self.l_ops.blockSignals(False)
        self.l_ops.setCurrentRow(0 if job.operations else -1)
        self._pick_op(self.l_ops.currentRow())

    @staticmethod
    def _op_label(op: Operation) -> str:
        return f"{op.name}  ·  {op.type}, T{op.tool}" + ("" if op.enabled else "  (off)")

    def _pick_tool(self, row):
        tools = self.project.job.tools if self.project else []
        self.f_tool.set(tools[row] if 0 <= row < len(tools) else None)

    def _pick_op(self, row):
        ops = self.project.job.operations if self.project else []
        self.f_op.set(ops[row] if 0 <= row < len(ops) else None)

    def _job_edited(self):
        p = self.project
        old_up = p._shape_up
        p.save_job()
        self.undo.record()
        for i, t in enumerate(p.job.tools):
            if self.l_tools.item(i):
                self.l_tools.item(i).setText(f"T{t.id}  {t.name}  Ø{t.diameter:g}")
        for i, op in enumerate(p.job.operations):
            if self.l_ops.item(i):
                self.l_ops.item(i).setText(self._op_label(op))
        if p.job.stock.up != old_up:
            self._refresh_model()
            self.viewer.view_iso()
        else:
            self.viewer.show_stock(p.setup().stock)
            self.viewer.refresh()
        self._mark_stale()
        if p.setup().warning:
            self.status.setText(p.setup().warning + " Check the stock size.")
        self._refresh_history_status()

    def _mark_stale(self):
        if self.toolpaths:
            self.status.setText("Job changed. Press Generate to update the toolpaths.")

    def suggest_feeds(self):
        from . import feeds
        row = self.l_ops.currentRow()
        if row < 0:
            return
        op = self.project.job.operations[row]
        values = feeds.apply(self.project.job, op)
        self.f_op.set(op)
        self._job_edited()
        material = feeds.MATERIALS[self.project.job.stock.material].label
        self.status.setText(f"{op.name}: {material} → " + ", ".join(f"{k} {v:g}" for k, v in values.items()))

    def tool_from_library(self):
        from . import library
        tools = library.load()
        labels = [f"{t.name}  (Ø{t.diameter:g}, {t.shape})" for t in tools]
        label, ok = QInputDialog.getItem(self, "Tool library", f"Add to this job (library: {library.path()})",
                                         labels, 0, False)
        if ok:
            new = library.into_job(self.project.job, tools[labels.index(label)])
            self._fill_job()
            self.l_tools.setCurrentRow(len(self.project.job.tools) - 1)
            self._job_edited()
            self.status.setText(f"Added T{new.id} {new.name}.")

    def tool_to_library(self):
        from . import library
        row = self.l_tools.currentRow()
        if 0 <= row < len(self.project.job.tools):
            library.add(self.project.job.tools[row])
            self.status.setText(f"Saved {self.project.job.tools[row].name} to {library.path()}.")

    def add_tool(self):
        tools = self.project.job.tools
        tools.append(Tool(id=max(t.id for t in tools) + 1, name="3 mm flat endmill", diameter=3.0))
        self._fill_job()
        self.l_tools.setCurrentRow(len(tools) - 1)
        self._job_edited()

    def add_op(self):
        ops = self.project.job.operations
        ops.append(Operation(name=f"Operation {len(ops) + 1}"))
        self._fill_job()
        self.l_ops.setCurrentRow(len(ops) - 1)
        self._job_edited()

    def remove_op(self):
        row = self.l_ops.currentRow()
        if row >= 0:
            del self.project.job.operations[row]
            self._fill_job()
            self._job_edited()

    def move_op(self, delta):
        ops, row = self.project.job.operations, self.l_ops.currentRow()
        if 0 <= row < len(ops) and 0 <= row + delta < len(ops):
            ops[row], ops[row + delta] = ops[row + delta], ops[row]
            self._fill_job()
            self.l_ops.setCurrentRow(row + delta)
            self._job_edited()

    # ---- toolpaths -------------------------------------------------------------------

    def generate(self):
        self.a_generate.setEnabled(False)
        self.status.setText("Computing toolpaths… (slicing the model level by level)")
        task = Task(self.project.toolpaths)
        task.signals.done.connect(self._generated)
        task.signals.failed.connect(self._failed)
        self.pool.start(task)

    def _generated(self, toolpaths):
        self.a_generate.setEnabled(True)
        self.toolpaths = toolpaths
        self.viewer.clear_simulation()
        self.c_model.setChecked(True)
        self.c_paths.setChecked(True)
        self._redraw_paths()
        parts = [f"{tp.op.name}: {len(tp.levels)} levels, {tp.minutes():.0f} min" for tp in toolpaths]
        total = sum(tp.minutes() for tp in toolpaths)
        self.status.setText("  ·  ".join(parts) + f"  ·  total ≈ {total:.0f} min cutting")

    def _failed(self, tb):
        self.a_generate.setEnabled(True)
        self.a_simulate.setEnabled(True)
        self.status.setText("Toolpath generation failed.")
        QMessageBox.critical(self, "Toolpath generation failed", tb[-2000:])

    def simulate(self):
        if not self.toolpaths:
            QMessageBox.information(self, "No toolpaths", "Generate toolpaths first.")
            return
        from . import simulate as sim
        self.a_simulate.setEnabled(False)
        self.status.setText("Simulating material removal…")
        setup, tps = self.project.setup(), self.toolpaths
        task = Task(lambda: sim.simulate(setup, tps))
        task.signals.done.connect(self._simulated)
        task.signals.failed.connect(self._failed)
        self.pool.start(task)

    def _simulated(self, result):
        self.a_simulate.setEnabled(True)
        self.c_model.setChecked(False)
        self.c_paths.setChecked(False)
        self.viewer.show_simulation(result)
        self.viewer.set_section(self.section_axis, self.section_slider.value() / 1000)
        if result.gouge_cells:
            self.status.setText(f"Gouge: the tool cuts up to {result.gouge_depth:.2f} mm into the part "
                                f"({result.gouge_cells} cells, shown red). Do not run this file.")
        else:
            self.status.setText(f"Simulated: no gouges. {result.removed_cm3:.1f} cm³ removed. "
                                "Tick Show model or Show toolpaths to compare.")

    def export_gcode(self):
        if not self.toolpaths:
            QMessageBox.information(self, "No toolpaths", "Generate toolpaths first.")
            return
        default = str(self.project.folder / f"{self.project.job.name}.nc")
        path, _ = QFileDialog.getSaveFileName(self, "Save G-code", default, "G-code (*.nc *.gcode *.tap)")
        if path:
            Path(path).write_text(self.project.gcode(self.toolpaths))
            self.status.setText(f"Saved {path}")

    # ---- history ---------------------------------------------------------------------

    def _refresh_history(self):
        h = self.project.history
        current, names = h.branches()
        self.c_branch.clear()
        self.c_branch.addItems(names)
        self.c_branch.setCurrentText(current)
        self.l_versions.blockSignals(True)
        self.l_versions.clear()
        self.versions = h.log()
        for v in self.versions:
            refs = f"  [{v.refs.replace('HEAD -> ', '')}]" if v.refs else ""
            item = QListWidgetItem(f"{v.short}  {v.date}  {v.message}{refs}")
            item.setToolTip(v.hash)
            self.l_versions.addItem(item)
        self.l_versions.blockSignals(False)
        self.t_diff.clear()
        self._refresh_history_status()

    def _refresh_history_status(self):
        dirty = self.project.history.has_changes()
        self.b_save.setText("Save version •" if dirty else "Save version")

    def save_version(self):
        msg = self.e_message.text().strip()
        if not msg:
            self.e_message.setFocus()
            self.status.setText("Write a short note about what changed, then save.")
            return
        if self.project.history.save(msg) is None:
            self.status.setText("Nothing changed since the last version.")
            return
        self.e_message.clear()
        self._refresh_history()
        self.status.setText(f"Saved version: {msg}")

    def _show_diff(self, row):
        if 0 <= row < len(self.versions):
            d = self.project.history.diff(self.versions[row].hash)
            self.t_diff.setPlainText(d or "The job is the same as in this version.")

    def restore_version(self):
        row = self.l_versions.currentRow()
        if row < 0:
            return
        v = self.versions[row]
        self.project.history.restore(v.hash)
        self.project.reload()
        self._fill_job()
        self._refresh_model()
        self._refresh_history_status()
        self.status.setText(f"Restored {v.short} “{v.message}”. Save a version to keep it.")

    def new_branch(self):
        name, ok = QInputDialog.getText(self, "New variant", "Name (for example 6mm-roughing):")
        name = name.strip().replace(" ", "-")
        if ok and name:
            try:
                self.project.history.new_branch(name)
            except RuntimeError as e:
                QMessageBox.warning(self, "Could not create variant", str(e))
            self._refresh_history()

    def _switch_branch(self):
        name = self.c_branch.currentText()
        try:
            self.project.history.switch(name)
        except RuntimeError as e:
            QMessageBox.warning(self, "Could not switch", str(e))
            self._refresh_history()
            return
        self.project.reload()
        self._fill_job()
        self._refresh_model()
        self._refresh_history()


def _dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    base, window, text, accent = QColor(30, 32, 36), QColor(40, 43, 48), QColor(226, 229, 234), QColor(64, 160, 255)
    for role, c in ((QPalette.Window, window), (QPalette.WindowText, text), (QPalette.Base, base),
                    (QPalette.AlternateBase, window), (QPalette.Text, text), (QPalette.Button, window),
                    (QPalette.ButtonText, text), (QPalette.Highlight, accent),
                    (QPalette.HighlightedText, QColor(255, 255, 255)), (QPalette.ToolTipBase, base),
                    (QPalette.ToolTipText, text), (QPalette.PlaceholderText, QColor(130, 136, 145))):
        p.setColor(role, c)
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 115, 122))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 115, 122))
    app.setPalette(p)


def run(path: str | None = None) -> int:
    # VTK renders through X11; under Wayland, Qt must use XWayland for the GL view to embed.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(sys.argv)
    app.setApplicationName("Syntexophobic")
    app.setOrganizationName("phobic")
    app.setDesktopFileName("syntexophobic")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "syntexophobic.svg")))
    _dark_palette(app)
    win = MainWindow()
    win.show()
    if path:
        win.load_path(path)
    return app.exec()
