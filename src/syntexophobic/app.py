"""Main window: 3D view, job settings, and the git-style history panel."""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QSplitter, QToolBar, QVBoxLayout, QWidget,
)

from . import geometry
from .job import Operation, Tool
from .project import Project

CHOICES = {
    "type": ["rough", "finish"],
    "up": list(geometry.UP_AXES),
    "zero": ["stock", "model"],
    "post": ["grbl"],
}
LABELS = {
    "stepover": "Stepover (× tool Ø)", "leave": "Leave on walls", "feed": "Feed (mm/min)",
    "plunge": "Plunge (mm/min)", "bottom": "Bottom Z", "rpm": "Spindle (rpm)",
    "safe_z": "Safe height", "top_offset": "Material above part", "margin": "Margin around part",
    "up": "Model axis facing up", "zero": "Work zero", "post": "Post-processor",
}
AUTO = -99999.0   # spin box value shown as "model bottom" for an empty bottom


class DataForm(QWidget):
    """Edits a dataclass in place: one row per field, typed controls, emits `changed`."""

    changed = Signal()

    def __init__(self, obj=None, parent=None):
        super().__init__(parent)
        self.layout_ = QFormLayout(self)
        self.layout_.setLabelAlignment(Qt.AlignRight)
        self.obj = None
        if obj is not None:
            self.set(obj)

    def set(self, obj) -> None:
        while self.layout_.rowCount():
            self.layout_.removeRow(0)
        self.obj = obj
        if obj is None:
            return
        for f in fields(obj):
            if f.name == "id":
                continue
            w = self._editor(f.name, getattr(obj, f.name), f.type)
            self.layout_.addRow(LABELS.get(f.name, f.name.replace("_", " ").capitalize()), w)

    def _editor(self, name, value, ftype):
        def store(v):
            setattr(self.obj, name, v)
            self.changed.emit()

        if name in CHOICES:
            w = QComboBox()
            w.addItems(CHOICES[name])
            w.setCurrentText(str(value))
            w.currentTextChanged.connect(store)
        elif isinstance(value, bool):
            w = QCheckBox()
            w.setChecked(value)
            w.toggled.connect(store)
        elif isinstance(value, int) and "float" not in str(ftype):
            w = QSpinBox()
            w.setRange(0, 100000)
            w.setValue(value)
            w.valueChanged.connect(store)
        elif isinstance(value, float) or "float" in str(ftype):
            w = QDoubleSpinBox()
            w.setDecimals(3)
            w.setRange(AUTO if name == "bottom" else -10000, 100000)
            w.setSingleStep(0.1 if name in ("stepover", "leave") else 0.5)
            if name == "bottom":
                w.setSpecialValueText("model bottom")
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
        self.setCentralWidget(self.viewer)
        self._build_toolbar()
        self._build_job_dock()
        self._build_history_dock()
        self.status = QLabel("Open a STEP file to start a project.")
        self.statusBar().addWidget(self.status, 1)
        self._enable(False)

    # ---- layout -----------------------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, key=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(QKeySequence(key))
            tb.addAction(a)
            return a

        act("New from STEP…", self.new_project, "Ctrl+N")
        act("Open project…", self.open_project, "Ctrl+O")
        tb.addSeparator()
        self.a_generate = act("Generate toolpaths", self.generate, "Ctrl+G")
        self.a_export = act("Export G-code…", self.export_gcode, "Ctrl+E")
        tb.addSeparator()
        act("Iso view", lambda: self.viewer.view_iso(), "1")
        act("Top view", lambda: self.viewer.view_top(), "2")
        self.c_rapids = QCheckBox("Show rapids")
        self.c_rapids.setChecked(True)
        self.c_rapids.toggled.connect(lambda: self.viewer.show_toolpaths(self.toolpaths, self.c_rapids.isChecked()))
        tb.addWidget(self.c_rapids)

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
        row.addWidget(b_add)
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
        ol.addWidget(self.l_ops)
        ol.addLayout(row)
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

    def _enable(self, on: bool):
        for w in (self.a_generate, self.a_export, self.job_dock, self.history_dock):
            w.setEnabled(on)

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
        self.project = project
        self.toolpaths = []
        self.setWindowTitle(f"Syntexophobic — {project.job.name}")
        self._fill_job()
        self._refresh_model()
        self._refresh_history()
        self._enable(True)
        self.viewer.view_iso()

    def _refresh_model(self):
        p = self.project
        self.viewer.show_model(p.shape)
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
        self._refresh_history_status()

    def _mark_stale(self):
        if self.toolpaths:
            self.status.setText("Job changed. Press Generate to update the toolpaths.")

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
        self.viewer.show_toolpaths(toolpaths, self.c_rapids.isChecked())
        parts = [f"{tp.op.name}: {len(tp.levels)} levels, {tp.minutes():.0f} min" for tp in toolpaths]
        total = sum(tp.minutes() for tp in toolpaths)
        self.status.setText("  ·  ".join(parts) + f"  ·  total ≈ {total:.0f} min cutting")

    def _failed(self, tb):
        self.a_generate.setEnabled(True)
        self.status.setText("Toolpath generation failed.")
        QMessageBox.critical(self, "Toolpath generation failed", tb[-2000:])

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
    _dark_palette(app)
    win = MainWindow()
    win.show()
    if path:
        win.load_path(path)
    return app.exec()
