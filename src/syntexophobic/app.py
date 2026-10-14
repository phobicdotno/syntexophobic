"""Main window: 3D view, job settings, and the git-style history panel."""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QSlider, QSplitter, QStackedWidget, QToolBar, QVBoxLayout, QWidget,
)

from . import desktop, geometry
from .job import Operation, Tool
from .project import Project

CHOICES = {
    "type": ["rough", "adaptive", "finish", "profile", "parallel", "vcarve", "engrave", "drill", "bore"],
    "up": list(geometry.UP_AXES),
    "zero": ["stock", "model"],
    "post": ["carbide", "grbl"],
    "shape": ["flat", "ball", "vbit"],
    "raster": ["X", "Y"],
    "side": ["top", "bottom"],
    "material": None,   # filled from feeds.MATERIALS below
}
from .feeds import MATERIALS  # noqa: E402

CHOICES["material"] = list(MATERIALS)
LABELS = {
    "stepover": "Stepover (× tool Ø)", "leave": "Leave on walls", "feed": "Feed (mm/min)",
    "plunge": "Plunge (mm/min)", "bottom": "Bottom Z", "rpm": "Spindle (rpm)",
    "safe_z": "Safe height", "top_offset": "Material above part", "margin": "Margin around part",
    "Stock.width": "Stock X size (0 = margin)", "Stock.depth": "Stock Y size (0 = margin)",
    "Stock.height": "Stock Z size (0 = to part bottom)",
    "side": "Side (bottom = after flipping)", "rest_from": "Rest after tool", "source": "Source (sketch or .dxf)", "max_depth": "Max depth",
    "angle": "V angle (°)", "hole_min": "Smallest hole Ø", "hole_max": "Largest hole Ø",
    "material": "Stock material", "up": "Model axis facing up", "Stock.body": "Body to machine (blank = all)", "ramp_angle": "Ramp angle (°)", "tabs": "Tabs per outline", "zero": "Work zero", "post": "Post-processor",
}
AUTO = -99999.0   # spin box value shown as "auto" for an empty optional number
OPTIONAL = {"bottom": "model bottom", "hole_min": "auto", "hole_max": "auto", "rest_from": "off"}
# Operation fields that mean nothing for a given type are hidden from the form.
HIDDEN = {
    "rough": {"tabs", "tab_width", "tab_height", "hole_min", "hole_max", "raster", "source", "max_depth"},
    "adaptive": {"tabs", "tab_width", "tab_height", "hole_min", "hole_max", "rest_from", "raster", "source", "max_depth"},
    "parallel": {"stepdown", "ramp_angle", "tabs", "tab_width", "tab_height", "hole_min", "hole_max", "rest_from",
                 "source", "max_depth"},
    "vcarve": {"stepdown", "stepover", "leave", "ramp_angle", "tabs", "tab_width", "tab_height", "hole_min",
               "hole_max", "rest_from", "raster", "bottom"},
    "engrave": {"stepover", "leave", "ramp_angle", "tabs", "tab_width", "tab_height", "hole_min",
                "hole_max", "rest_from", "raster", "bottom"},
    "finish": {"stepover", "hole_min", "hole_max", "rest_from", "raster", "source", "max_depth"},
    "profile": {"stepover", "hole_min", "hole_max", "rest_from", "raster", "source", "max_depth"},
    "drill": {"stepover", "leave", "feed", "ramp_angle", "tabs", "tab_width", "tab_height", "rest_from", "raster", "source", "max_depth"},
    "bore": {"tabs", "tab_width", "tab_height", "rest_from", "raster", "source", "max_depth"},
}


ICONS = Path(__file__).with_name("assets") / "icons"


ICON_INK = "#d8dce4"                                    # phobiCCCP's icon ink
ICON_ACCENTS = ("#9fc8f2", "#f2a536", "#f0a030", "#e05050")  # its blue, ambers and red


def mono_icon(name: str, palette: QPalette) -> QIcon:
    """A phobiCCCP-style line icon: the shape in ink, clicked points and helper lines in
    blue, material in amber, removed parts in red. The disabled copy is all one grey."""
    from PySide6.QtSvg import QSvgRenderer

    svg = (ICONS / f"{name}.svg").read_text()
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Disabled):
        if mode == QIcon.Normal:
            text = svg.replace("currentColor", ICON_INK)
        else:
            text = svg.replace("currentColor", palette.color(QPalette.Disabled, QPalette.WindowText).name())
            for accent in ICON_ACCENTS:
                text = text.replace(accent, palette.color(QPalette.Disabled, QPalette.WindowText).name())
        renderer = QSvgRenderer(QByteArray(text.encode()))
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
            # labels for this kind of object first (Stock.width is not Feature.width)
            label = LABELS.get(f"{type(obj).__name__}.{f.name}") or LABELS.get(f.name) or \
                f.name.replace("_", " ").capitalize()
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


def _scrolling(body: QWidget) -> QScrollArea:
    """Body in a scroll area, so a tall dock never forces the window taller than the screen."""
    area = QScrollArea()
    area.setWidget(body)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


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
        from .update_ui import UpdateBar
        self.update_bar = UpdateBar(pool=self.pool)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.update_bar)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
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
        self._build_ribbon()
        self._build_assistant_dock()
        self.status = QLabel("Start a new design to model a part, or open a STEP / STL file to machine one.")
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
        self.update_bar.changed.connect(self.status.setText)
        self._start_control_server()
        self.set_workspace(self.c_workspace.currentText())   # one of the Design / Job docks, not both
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
        m_help = bar.addMenu("&Help")
        m_help.addAction("Check for &updates…", lambda: self.update_bar.check(quiet=False))
        self.a_auto_update = m_help.addAction("Check for updates at start")
        self.a_auto_update.setCheckable(True)
        self.a_auto_update.setChecked(QSettings().value("update/auto", True, type=bool))
        self.a_auto_update.toggled.connect(lambda on: QSettings().setValue("update/auto", on))
        m_help.addSeparator()
        m_help.addAction("&About Syntexophobic", lambda: __import__("syntexophobic.about", fromlist=["x"]).dialog(self).exec())
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
        self.a_new_design = act("New design…", self.new_design, "design", "Ctrl+Shift+N", m_file)
        act("New from STEP / STL…", self.new_project, "new", "Ctrl+N", m_file)
        act("Open project…", self.open_project, "open", "Ctrl+O", m_file)
        tb.addSeparator()
        self.a_generate = act("Generate toolpaths", self.generate, "generate", "Ctrl+G", m_paths, toolbar=False)
        self.a_simulate = act("Simulate", self.simulate, "simulate", "Ctrl+R", m_paths, toolbar=False)
        self.a_export = act("Export G-code…", self.export_gcode, "export", "Ctrl+E", m_file, toolbar=False)
        m_file.addSeparator()
        for text, slot in (("Import DXF as sketch…", self.import_dxf), ("Export sketch as DXF…", self.export_dxf),
                           ("Export model (STEP / STL)…", self.export_model),
                           ("Convert STL to STEP…", self.convert_stl)):
            m_file.addAction(text).triggered.connect(slot)
        m_file.addSeparator()
        quit_ = m_file.addAction("Quit")
        quit_.setShortcut(QKeySequence("Ctrl+Q"))
        quit_.triggered.connect(self.close)
        tb.addSeparator()
        act("Home (iso)", lambda: self.viewer.view_iso(), "iso", "1", m_view)
        act("Front view", lambda: self.viewer.view_front(), "front", "2", m_view)
        act("Right view", lambda: self.viewer.view_right(), "right", "3", m_view)
        act("Top view", lambda: self.viewer.view_top(), "top", "4", m_view)
        act("Fit", lambda: self.viewer.fit(), "fit", "F", m_view)
        m_views = m_view.addMenu("More views")
        for label, fn in (("Back", self.viewer.view_back), ("Left", self.viewer.view_left),
                          ("Bottom", self.viewer.view_bottom)):
            m_views.addAction(label, fn)
        m_style3d = m_view.addMenu("Visual style")
        grp = QActionGroup(self)
        for label, key in (("Shaded with edges", "edges"), ("Shaded", "shaded"), ("Wireframe", "wireframe")):
            a = m_style3d.addAction(label)
            a.setCheckable(True)
            a.setChecked(key == "edges")
            grp.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self.viewer.set_visual_style(k))
        tb.addSeparator()
        m_view.addSeparator()
        self.c_paths = act("Toolpaths", lambda _: self._redraw_paths(), "toolpaths", "T", m_view, True, toolbar=False)
        self.c_rapids = act("Rapids", lambda _: self._redraw_paths(), "rapids", "R", m_view, True, toolbar=False)
        self.c_model = act("Model", lambda on: self.viewer.set_model_visible(on), "model", "M", m_view, True, toolbar=False)

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
        self.play_slider = QSlider(Qt.Horizontal)
        self.play_slider.setMaximumWidth(260)
        self.play_slider.setToolTip("Simulation playback: drag to see the stock at each moment")
        self.play_slider.valueChanged.connect(self._play)
        self.play_action = tb.addWidget(self.play_slider)
        self.play_action.setVisible(False)
        self.sim_result = None
        self.section_axis = None
        a_assist = m_view.addAction("Assistant (Claude)")
        a_assist.setShortcut(QKeySequence("Ctrl+K"))
        a_assist.triggered.connect(lambda: (self.assistant_dock.show(), self.assistant_dock.raise_(),
                                            self.assistant.input.setFocus()))
        self.a_measure = m_view.addAction("Measure")
        self.a_measure.setCheckable(True)
        self.a_measure.setShortcut(QKeySequence("D"))
        self.a_measure.toggled.connect(self._measure_mode)
        self._measure_picks = []
        m_view.addSeparator()
        self.a_fullscreen = m_view.addAction("Full screen")
        self.a_fullscreen.setCheckable(True)
        # F11 explicitly: QKeySequence.FullScreen is platform-theme dependent on X11 and
        # often resolves to nothing, so the key did nothing.
        self.a_fullscreen.setShortcuts([QKeySequence("F11"), QKeySequence(QKeySequence.FullScreen)])
        self.a_fullscreen.toggled.connect(self.set_fullscreen)
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

    def _build_ribbon(self):
        """The Fusion-style tool ribbon under the app bar: a Design tab (Create / Modify /
        Insert / Inspect) and a Manufacture tab (Actions / Display), swapped by workspace."""
        from PySide6.QtWidgets import QFrame
        from .design_ui import _ribbon_button, _ribbon_group
        dp = self.design_panel

        def qa(icon, text, slot, tip=None):
            a = QAction(mono_icon(icon, self.palette()), text, self)
            a.setToolTip(tip or text)
            a.triggered.connect(lambda _=False, s=slot: s())
            return a

        def page(groups):
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(4, 0, 4, 0)
            row.setSpacing(0)
            for i, (title, buttons) in enumerate(groups):
                if i:
                    line = QFrame()
                    line.setFrameShape(QFrame.VLine)
                    line.setProperty("ribbonSep", True)
                    row.addWidget(line)
                row.addWidget(_ribbon_group(title, buttons))
            row.addStretch(1)
            return w

        # ---- Design tab ----
        big = {"extrude", "revolve"}
        create = [qa("sketch_new", "Create Sketch", dp.new_sketch, "New sketch on a plane"),
                  qa("sketch_face", "Sketch on Face", dp.sketch_on_face, "New sketch on the picked flat face"),
                  qa("extrude", "Extrude", lambda: dp.add("extrude")),
                  qa("revolve", "Revolve", lambda: dp.add("revolve")),
                  qa("loft", "Loft", lambda: dp.add("loft")),
                  qa("sweep", "Sweep", lambda: dp.add("sweep")),
                  qa("hole", "Hole", lambda: dp.add("hole"))]
        modify = [qa("fillet", "Fillet", lambda: dp.add("fillet")),
                  qa("chamfer", "Chamfer", lambda: dp.add("chamfer")),
                  qa("shell", "Shell", lambda: dp.add("shell")),
                  qa("mirror", "Mirror", lambda: dp.add("mirror"))]
        insert = [qa("relief", "Relief", dp.add_relief, "Relief from a picture"),
                  qa("import", "Insert STEP/STL", dp.add_import, "Import a STEP or STL body")]
        self.a_parameters = qa("parameters", "Parameters", dp.edit_parameters, "Named parameters")
        self.a_measure.setIcon(mono_icon("dimension", self.palette()))
        inspect = [self.a_parameters, self.a_measure]
        self.design_feature_actions = create + modify + insert + [self.a_parameters]
        design_page = page([("CREATE", [_ribbon_button(a, big=a.text() in {"Extrude", "Revolve"}) for a in create]),
                            ("MODIFY", [_ribbon_button(a) for a in modify]),
                            ("INSERT", [_ribbon_button(a) for a in insert]),
                            ("INSPECT", [_ribbon_button(a) for a in inspect])])

        # ---- Manufacture tab ----
        manu_page = page([("ACTIONS", [_ribbon_button(self.a_generate, big=True),
                                       _ribbon_button(self.a_simulate, big=True),
                                       _ribbon_button(self.a_export)]),
                          ("DISPLAY", [_ribbon_button(self.c_paths), _ribbon_button(self.c_rapids),
                                       _ribbon_button(self.c_model)])])

        self.ribbon = QStackedWidget()
        self.ribbon.addWidget(design_page)
        self.ribbon.addWidget(manu_page)
        self._design_page, self._manu_page = design_page, manu_page
        rb = QToolBar("Ribbon")
        rb.setObjectName("ribbon-toolbar")
        rb.setMovable(False)
        rb.setContextMenuPolicy(Qt.PreventContextMenu)
        rb.addWidget(self.ribbon)
        self.addToolBarBreak()
        self.addToolBar(rb)
        self.ribbon_toolbar = rb

    def set_fullscreen(self, on: bool):
        # showFullScreen/showNormal are the reliable way across window managers; plain
        # setWindowState is quietly ignored by some. Remember maximize so exiting restores it.
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:   # also when the window manager changes it
            self.a_fullscreen.blockSignals(True)       # reflect the state without re-triggering
            self.a_fullscreen.setChecked(self.isFullScreen())
            self.a_fullscreen.blockSignals(False)

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
        dock.setWidget(_scrolling(body))
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
                  self.design_panel):
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
        self.design_note = QLabel()
        self.design_note.setWordWrap(True)
        self.b_new_design = QPushButton("New design…")
        self.b_new_design.clicked.connect(self.new_design)
        self.b_to_design = QPushButton("Design on this part")
        self.b_to_design.setToolTip("Make the imported model the first feature of a design, "
                                    "so you can add sketches, cuts, holes and fillets to it")
        self.b_to_design.clicked.connect(self.to_design)
        self.design_start = QWidget()
        start = QVBoxLayout(self.design_start)
        start.setContentsMargins(0, 0, 0, 0)
        for w in (self.design_note, self.b_new_design, self.b_to_design):
            start.addWidget(w)
        start.addStretch(1)
        body = QWidget()
        col = QVBoxLayout(body)
        col.addWidget(self.design_start)
        col.addWidget(self.design_panel)
        dock.setWidget(_scrolling(body))
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.design_dock = dock
        self._show_design_start()

    def _show_design_start(self):
        """The Design dock without a designed part: how to start one."""
        has_part = self.part is not None
        self.design_panel.setVisible(has_part)
        self.design_start.setVisible(not has_part)
        for a in getattr(self, "design_feature_actions", []):
            a.setEnabled(has_part)
        if self.project is None:
            self.design_note.setText("Model a part: start a new design, then draw a sketch and extrude it.\n\n"
                                     "To machine a finished part, use New from STEP / STL instead.")
        else:
            self.design_note.setText(f"This project machines {self.project.job.model} as it is.\n\n"
                                     "Design on this part to add sketches, cuts, holes or fillets to it, "
                                     "or start a separate new design.")
        self.b_to_design.setVisible(self.project is not None and not has_part)

    def _play(self, i: int):
        """Show the stock and the tool at playback frame i."""
        from dataclasses import replace
        r = self.sim_result
        if r is None or not r.frames:
            return
        name, tip, dia, stock, (shape, k) = r.frames[i]
        self.viewer.show_simulation(replace(r, stock=stock))
        self.viewer.set_section(self.section_axis, self.section_slider.value() / 1000)
        last = i == len(r.frames) - 1
        self.viewer.show_tool(None if last else tip, dia, shape=shape, k=k)
        self.status.setText(f"Playback {i + 1}/{len(r.frames)}: {name}" + ("  (end)" if last else ""))

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

    def _build_assistant_dock(self):
        from .assistant_ui import AssistantPanel
        dock = QDockWidget("Assistant (Claude)", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        dock.setObjectName("assistant")
        self.assistant = AssistantPanel(self)
        dock.setWidget(self.assistant)
        dock.setMinimumWidth(340)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.tabifyDockWidget(self.history_dock, dock)
        self.history_dock.raise_()
        self.assistant_dock = dock

    def set_workspace(self, name: str):
        design = name == "Design"
        self.viewer.picking = design or self.a_measure.isChecked()
        self.design_dock.setVisible(design)
        self.job_dock.setVisible(not design)
        self.ribbon.setCurrentWidget(self._design_page if design else self._manu_page)
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
        try:
            project = Project.create_design(target, name.strip())
        except Exception as e:
            QMessageBox.critical(self, "Could not create the design", str(e))
            return
        self.load(project)
        self.c_workspace.setCurrentText("Design")

    def to_design(self):
        """The open STEP / STL project becomes a design whose first feature imports it."""
        if self.project is None or self.part is not None:
            return
        self.project.to_design()
        self.load(self.project)
        self.status.setText("The model is now the Import feature of a design: add sketches and features after it.")

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
            row = self.design_panel.current_row()
            self.part = Part.load(self.project.folder / self.project.job.model)
            self.design_panel.set_part(self.part)
            self.design_panel.set_current_row(row)
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
        self.ribbon_toolbar.hide()                   # the sketch page carries its own ribbon
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
        self.ribbon_toolbar.show()
        if save:
            self._design_changed()

    # ---- project ----------------------------------------------------------------------

    def new_project(self):
        step, _ = QFileDialog.getOpenFileName(self, "Model to machine", str(Path.home()), "Models (*.step *.stp *.stl *.STEP *.STP *.STL)")
        if not step:
            return
        default = str(Path(step).with_suffix("")) + "-cam"
        folder = QFileDialog.getExistingDirectory(self, "Folder for the new project (pick or create an empty one)", str(Path(default).parent))
        if not folder:
            return
        target = Path(folder)
        if any(target.iterdir()):
            target = target / (Path(step).stem + "-cam")
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            project = Project.create(target, step)
        except Exception as e:
            QMessageBox.critical(self, "Could not create the project", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.load(project)

    def convert_stl(self):
        from . import geometry
        src, _ = QFileDialog.getOpenFileName(self, "STL to convert", str(Path.home()), "STL (*.stl *.STL)")
        if not src:
            return
        dst, _ = QFileDialog.getSaveFileName(self, "Save STEP", str(Path(src).with_suffix(".step")), "STEP (*.step *.stp)")
        if not dst:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            geometry.stl_to_step(src, dst)
        except Exception as e:
            QMessageBox.critical(self, "Could not convert", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Saved {dst}", 8000)

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "Project folder (contains job.toml)", str(Path.home()))
        if folder:
            self.load_path(folder)

    def load_path(self, path: str):
        p = Path(path)
        try:
            if p.suffix.lower() in (".step", ".stp", ".stl"):
                self.load(Project.create(p.with_name(p.stem + "-cam"), p))
            else:
                self.load(Project(p))
        except Exception as e:
            QMessageBox.critical(self, "Could not open", str(e))

    # ---- remote control (MCP / Claude Code) ------------------------------------------------

    def _start_control_server(self):
        """Listen for commands from `syntexophobic mcp` (Claude Code) on a local socket."""
        from PySide6.QtNetwork import QLocalServer
        from . import automation
        self.session = automation.Session()
        self.session.window = self
        automation.STATE.mkdir(parents=True, exist_ok=True)
        QLocalServer.removeServer(automation.control_name())
        self.control = QLocalServer(self)
        self.control.setSocketOptions(QLocalServer.UserAccessOption)      # only this user
        if self.control.listen(automation.control_name()):
            self.control.newConnection.connect(self._control_connection)

    def _control_connection(self):
        conn = self.control.nextPendingConnection()
        conn.setProperty("buf", b"")

        def readable():
            buf = bytes(conn.property("buf") or b"") + bytes(conn.readAll())
            conn.setProperty("buf", buf)
            if not buf.endswith(b"\n"):
                return
            import json
            from . import automation, faults
            try:
                req = json.loads(buf.decode())
                self.status.setText(f"Claude: {req['cmd']}…")
                QApplication.processEvents()
                result = automation.run(self.session, req["cmd"], req.get("args") or {})
                self._after_automation(req["cmd"])
                reply = {"ok": True, "result": result}
            except Exception as e:  # report every failure back to Claude
                what = buf[:300].decode(errors="replace").strip()
                if isinstance(e, automation.CommandError):            # a refusal, not a crash
                    faults.log.warning("command %s refused: %s", what, e)
                else:
                    faults.log.exception("command %s failed", what)
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            conn.write((json.dumps(reply) + "\n").encode())
            conn.flush()
            conn.disconnectFromServer()
        conn.readyRead.connect(readable)

    def _after_automation(self, cmd: str):
        """Show what a remote command changed."""
        s = self.session
        if cmd == "library_add" and hasattr(self, "assistant"):
            self.assistant.library_changed()
        if cmd == "library_list" or (cmd == "library_add" and self.project is None):
            return
        if cmd in ("status", "get_sketch", "history", "diff", "screenshot", "view"):
            return
        if s.project is not self.project:
            self.load(s.project)
        else:
            self.project.reload()
            if self.part is not None:
                from .model import Part
                row = self.design_panel.current_row()
                self.part = Part.load(self.project.folder / self.project.job.model)
                self.design_panel.set_part(self.part)
                self.design_panel.set_current_row(max(row, 0))
            self._fill_job()
            self._refresh_model()
            self._refresh_history()
        if cmd == "generate" and s.toolpaths:
            self.c_workspace.setCurrentText("Manufacture")
            self._generated(s.toolpaths)
        elif cmd == "simulate" and s.last_simulation is not None:
            self.c_workspace.setCurrentText("Manufacture")
            self.toolpaths = s.toolpaths
            self._simulated(s.last_simulation)
        if hasattr(self, "undo"):
            self.undo.record()
        self.status.setText(f"Claude: {cmd} done.")

    def screenshot(self, path: str) -> str:
        """The window as a PNG, with the 3D view painted in (a plain grab shows it black)."""
        import tempfile
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QImage
        from vtkmodules.vtkIOImage import vtkPNGWriter
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        for _ in range(5):
            QApplication.processEvents()
        shot = self.grab().toImage()
        if self.viewer.isVisible():
            self.viewer.GetRenderWindow().Render()
            f = vtkWindowToImageFilter()
            f.SetInput(self.viewer.GetRenderWindow())
            f.ReadFrontBufferOff()
            f.Update()
            tmp = Path(tempfile.mkdtemp()) / "view.png"
            w = vtkPNGWriter()
            w.SetFileName(str(tmp))
            w.SetInputConnection(f.GetOutputPort())
            w.Write()
            p = QPainter(shot)
            r = QRect(self.viewer.mapTo(self, QPoint(0, 0)), self.viewer.size())
            p.drawImage(r, QImage(str(tmp)).scaled(r.size()))
            p.end()
        shot = shot.scaledToWidth(min(shot.width(), 1600), Qt.SmoothTransformation)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shot.save(path)
        return path

    def automation_view(self, preset: str, workspace: str) -> dict:
        if workspace:
            self.c_workspace.setCurrentText(workspace)
        (self.viewer.view_top if preset == "top" else self.viewer.view_iso)()
        return {"view": preset, "workspace": self.c_workspace.currentText()}

    def load(self, project: Project):
        from .project import Undo
        self.finish_sketch(save=False)
        self.project = project
        if hasattr(self, "session"):
            self.session.project = project
            self.session.toolpaths = []
        self.undo = Undo(project.folder)
        self.toolpaths = []
        if project.job.model.endswith(".toml"):
            from .model import Part
            self.part = Part.load(project.folder / project.job.model)
            self.design_panel.set_part(self.part)
        else:
            self.part = None
        self._show_design_start()
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
            tool = self.project.job.tools[row]
            there = library.add(tool)
            self.status.setText(f"The library already has {there.name}." if there
                                else f"Saved {tool.name} to {library.path()}.")

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
        if hasattr(self, "session"):
            self.session.toolpaths = toolpaths
        self.viewer.clear_simulation()
        self.play_action.setVisible(False)
        self.sim_result = None
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
        task = Task(lambda: sim.simulate(setup, tps, snapshots=40))
        task.signals.done.connect(self._simulated)
        task.signals.failed.connect(self._failed)
        self.pool.start(task)

    def _simulated(self, result):
        self.a_simulate.setEnabled(True)
        self.c_model.setChecked(False)
        self.c_paths.setChecked(False)
        self.viewer.show_simulation(result)
        self.viewer.set_section(self.section_axis, self.section_slider.value() / 1000)
        self.sim_result = result
        if result.frames:
            self.play_slider.blockSignals(True)
            self.play_slider.setRange(0, len(result.frames) - 1)
            self.play_slider.setValue(len(result.frames) - 1)
            self.play_slider.blockSignals(False)
            self.play_action.setVisible(True)
        if result.rapid_hits:
            name, (x, y, z), d = max(result.rapid_hits, key=lambda h: h[2])
            self.viewer.show_tool((x, y, z), 2.0)
            self.status.setText(f"Rapid into stock: {len(result.rapid_hits)} rapid moves hit material, the worst "
                                f"{d:.2f} mm deep in {name} at X{x:.1f} Y{y:.1f} Z{z:.1f} (marked). Do not run this file.")
        elif result.gouge_cells:
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
        if not path:
            return
        sides = self.project.sides()
        if sides == ["top"]:
            Path(path).write_text(self.project.gcode(self.toolpaths))
            self.status.setText(f"Saved {path}")
            return
        # two sides: one file each, run the top one, flip the part, run the bottom one
        base = Path(path)
        saved = []
        for side in sides:
            out = base.with_name(f"{base.stem}-{side}{base.suffix or '.nc'}")
            out.write_text(self.project.gcode(self.toolpaths, side=side))
            saved.append(out.name)
        self.status.setText("Saved " + " and ".join(saved) + ": run the top file, flip the part over its X axis, "
                            "then run the bottom file.")

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
        from . import history
        if not history.available():
            self.t_diff.setPlainText("Version history is off because git is not installed.\n"
                                     "Install it with: sudo apt install git")
        self.b_save.setEnabled(history.available())
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
    base, window, text, accent = QColor(27, 29, 33), QColor(37, 40, 45), QColor(226, 229, 234), QColor(64, 160, 255)
    for role, c in ((QPalette.Window, window), (QPalette.WindowText, text), (QPalette.Base, base),
                    (QPalette.AlternateBase, QColor(33, 36, 41)), (QPalette.Text, text), (QPalette.Button, window),
                    (QPalette.ButtonText, text), (QPalette.Highlight, accent),
                    (QPalette.HighlightedText, QColor(255, 255, 255)), (QPalette.ToolTipBase, base),
                    (QPalette.ToolTipText, text), (QPalette.PlaceholderText, QColor(130, 136, 145)),
                    (QPalette.Link, accent)):
        p.setColor(role, c)
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 115, 122))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 115, 122))
    app.setPalette(p)
    app.setStyleSheet(_STYLESHEET)


# Fusion-dark chrome: flat toolbars, ribbon groups with captions, a browser tree and the
# green Finish button. Colours track the palette above (near-black canvas, grey panels, blue accent).
_STYLESHEET = """
QMainWindow, QDockWidget { background: #25282d; }
QToolBar#main-toolbar { background: #1f2227; border: none; border-bottom: 1px solid #14161a; padding: 2px 4px; spacing: 2px; }
QToolBar { background: transparent; border: none; }
QToolBar::separator { background: #3a3e45; width: 1px; margin: 4px 6px; }
QToolButton { border: 1px solid transparent; border-radius: 5px; padding: 3px; color: #e2e5ea; }
QToolButton:hover { background: #343941; border-color: #3f444d; }
QToolButton:pressed { background: #2b2f36; }
QToolButton:checked { background: #16324d; border-color: #2d6ca8; }
QToolButton::menu-indicator { image: none; }

/* Sketch ribbon: grouped tool columns with a caption under each */
QWidget[ribbonBar="true"] { background: #2a2d33; border-bottom: 1px solid #14161a; }
QToolButton[ribbon="1"], QToolButton[ribbon="big"] { font-size: 10px; }
QToolButton[ribbon="big"] { min-width: 46px; }
QLabel[ribbonCaption="true"] { color: #7f868f; font-size: 8px; font-weight: 600; letter-spacing: 1px; }
QFrame[ribbonSep="true"] { color: #3a3e45; max-width: 1px; margin: 3px 2px; }
QToolButton[finish="true"] { color: #d7f5df; }
QToolButton[finish="true"]:hover { background: #1f4d30; border-color: #2e8c52; }

/* Browser tree (Fusion's left panel) */
QTreeWidget, QTreeView { background: #1f2227; border: none; outline: 0; }
QTreeView::item { padding: 3px 2px; border: none; }
QTreeView::item:hover { background: #2c313a; }
QTreeView::item:selected { background: #16324d; color: #ffffff; }
QTreeView::branch { background: transparent; }

QDockWidget::title { background: #1f2227; padding: 5px 8px; color: #aeb4bd; font-weight: 600; }
QGroupBox { border: 1px solid #33373e; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #aeb4bd; }
QPushButton { background: #31353c; border: 1px solid #3c414a; border-radius: 5px; padding: 5px 10px; }
QPushButton:hover { background: #3a3f47; }
QPushButton:pressed { background: #2a2e34; }
QListWidget, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QTableWidget {
    background: #1b1d21; border: 1px solid #33373e; border-radius: 5px; selection-background-color: #16324d; }
QComboBox:hover, QLineEdit:focus { border-color: #2d6ca8; }
QStatusBar { background: #1f2227; color: #aeb4bd; }
QMenuBar { background: #1f2227; }
QMenuBar::item:selected { background: #2c313a; }
QTabBar::tab { background: #26292f; padding: 5px 12px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #16324d; color: #ffffff; }
QSplitter::handle { background: #14161a; }
QScrollBar:vertical { background: transparent; width: 11px; }
QScrollBar::handle:vertical { background: #3c414a; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4a505a; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; }
QScrollBar::handle:horizontal { background: #3c414a; border-radius: 5px; min-width: 24px; }
"""


def run(path: str | None = None) -> int:
    # VTK renders through X11; under Wayland, Qt must use XWayland for the GL view to embed.
    # macOS has only its own platform (cocoa), which VTK draws into directly.
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    elif sys.platform == "win32":
        # its own taskbar button and icon, not Python's (the launcher starts python.exe)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("phobic.Syntexophobic")
    # the window's WM_CLASS is (RESOURCE_NAME, application name): "syntexophobic", "Syntexophobic",
    # matching the menu entry, where it would otherwise be "__main__.py"
    os.environ.setdefault("RESOURCE_NAME", desktop.APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(desktop.WM_CLASS)
    app.setOrganizationName("phobic")
    app.setDesktopFileName(desktop.APP_ID)
    app.setWindowIcon(QIcon(str(desktop.SVG)))
    desktop.integrate()                            # before the window, so the shell matches it at once
    _dark_palette(app)
    from . import faults
    faults.setup_logging()
    faults.log.info("Syntexophobic %s started", faults.environment().get("Version"))
    win = MainWindow()
    win.show()
    if path:
        win.load_path(path)
    if win.a_auto_update.isChecked():       # quietly: nothing shows unless there is a new version
        QTimer.singleShot(3000, win.update_bar.check)
        recheck = QTimer(win)
        recheck.timeout.connect(win.update_bar.check)
        recheck.start(3600 * 1000)   # hourly, like phobiCCCP
    return app.exec()
