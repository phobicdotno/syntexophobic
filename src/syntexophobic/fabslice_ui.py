"""The "Slice for fabrication" window: pick how the model is built back up from sheet
material, see the assembled slices in the 3D view and the cut sheets beside the settings,
and export the sheets for the laser or the CNC."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsScene,
                               QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

from . import fabexport, fabslice
from .fabslice import FabSettings
from .fabexport import SheetSettings

HELP = {
    "stacked": "Horizontal layers one sheet thick, glued on top of each other. Dowel holes line "
               "them up; each layer has where its neighbour goes engraved on it.",
    "rod": "Slices threaded on a rod or pipe, spacer rings (cut from the same sheet) between them.",
    "base": "Upright slices with tabs that sit in slots in a base plate.",
    "back": "Upright slices with tabs in slots of a back plate, to hang on a wall.",
    "waffle": "Two sets of upright slices at right angles, slotted half way so they slide "
              "together. No glue.",
}


def _spin(lo, hi, value, step=0.5, decimals=2, suffix=" mm"):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class FabSliceDialog(QDialog):
    """Not modal: the 3D view behind it shows the assembled slices while it is open, and
    the model again when it closes."""

    def __init__(self, shape, viewer=None, stem: str = "slices", folder: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Slice for fabrication")
        self.shape, self.viewer, self.stem, self.folder = shape, viewer, stem, folder
        self.result: fabslice.FabResult | None = None
        self.sheets: list[fabexport.Sheet] = []
        self.error = ""
        self.resize(1100, 680)

        form_box = QWidget()
        col = QVBoxLayout(form_box)
        col.setContentsMargins(0, 0, 0, 0)

        build = QGroupBox("Build")
        f = QFormLayout(build)
        self.style = QComboBox()
        for key in fabslice.STYLES:
            self.style.addItem(fabslice.STYLE_NAMES[key], key)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: #aeb4bd;")
        self.up = QComboBox()
        self.up.addItems(["+Z", "-Z", "+X", "-X", "+Y", "-Y"])
        self.axis = QComboBox()
        self.axis.addItems(["X", "Y", "Z"])
        self.thickness = _spin(0.5, 50, 3.0, 0.5)
        self.spacing = _spin(0, 500, 0, 1)
        self.spacing.setSpecialValueText("automatic")
        self.count = QSpinBox()
        self.count.setRange(0, 500)
        self.count.setSpecialValueText("from spacing")
        self.clearance = _spin(0, 2, 0.2, 0.05)
        f.addRow("Style", self.style)
        f.addRow(self.help)
        f.addRow("Model up", self.up)
        self.axis_label = QLabel("Slice across")
        f.addRow(self.axis_label, self.axis)
        f.addRow("Sheet thickness", self.thickness)
        self.spacing_label, self.count_label = QLabel("Slice spacing"), QLabel("Number of slices")
        f.addRow(self.spacing_label, self.spacing)
        f.addRow(self.count_label, self.count)
        f.addRow("Slot / hole play", self.clearance)
        col.addWidget(build)

        joins = QGroupBox("Holding it together")
        g = QFormLayout(joins)
        self.rod = _spin(1, 100, 8.0, 0.5)
        self.dowels = QSpinBox()
        self.dowels.setRange(0, 6)
        self.dowels.setValue(2)
        self.dowel_d = _spin(1, 30, 6.0, 0.5)
        self.wall = _spin(1, 30, 4.0, 0.5)
        self.margin = _spin(0, 100, 10.0, 1)
        self.guide = QCheckBox("Engrave where the next layer goes")
        self.guide.setChecked(True)
        self.rod_label, self.dowels_label, self.dowel_d_label = QLabel("Rod diameter"), QLabel("Dowel holes"), QLabel("Dowel diameter")
        self.margin_label = QLabel("Plate margin")
        g.addRow(self.rod_label, self.rod)
        g.addRow(self.dowels_label, self.dowels)
        g.addRow(self.dowel_d_label, self.dowel_d)
        g.addRow("Material around holes", self.wall)
        g.addRow(self.margin_label, self.margin)
        g.addRow(self.guide)
        col.addWidget(joins)

        machine = QGroupBox("Sheets and machine")
        m = QFormLayout(machine)
        self.machine = QComboBox()
        self.machine.addItem("Laser", "laser")
        self.machine.addItem("CNC router", "cnc")
        self.kerf = _spin(0, 1, 0.15, 0.01, 3)
        self.tool = _spin(0.5, 25, 3.175, 0.1, 3)
        self.sheet_w = _spin(50, 3000, 600, 10, 0)
        self.sheet_h = _spin(50, 3000, 400, 10, 0)
        self.gap = _spin(0, 50, 3, 0.5)
        self.labels = QCheckBox("Engrave part names")
        self.labels.setChecked(True)
        self.kerf_label, self.tool_label = QLabel("Laser kerf"), QLabel("Bit diameter")
        m.addRow("Machine", self.machine)
        m.addRow(self.kerf_label, self.kerf)
        m.addRow(self.tool_label, self.tool)
        size = QHBoxLayout()
        size.addWidget(self.sheet_w)
        size.addWidget(QLabel("×"))
        size.addWidget(self.sheet_h)
        m.addRow("Sheet size", size)
        m.addRow("Gap between parts", self.gap)
        m.addRow(self.labels)
        col.addWidget(machine)
        col.addStretch(1)

        # right: the sheets
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setBackgroundBrush(QColor("#1b1d21"))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        right = QWidget()
        rc = QVBoxLayout(right)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.addWidget(self.view, 1)
        rc.addWidget(self.summary)

        split = QSplitter()
        split.addWidget(form_box)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 720])
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.b_export = buttons.addButton("Export sheets…", QDialogButtonBox.AcceptRole)
        self.b_export.clicked.connect(lambda: self.export())
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.recompute)
        self._load()
        for w in self._inputs():
            sig = getattr(w, "valueChanged", None) or getattr(w, "currentIndexChanged", None) or w.toggled
            sig.connect(self._changed)
        self._changed()
        self.recompute()

    # ---- settings ------------------------------------------------------------------------

    def _inputs(self):
        return [self.style, self.up, self.axis, self.thickness, self.spacing, self.count, self.clearance,
                self.rod, self.dowels, self.dowel_d, self.wall, self.margin, self.guide, self.machine,
                self.kerf, self.tool, self.sheet_w, self.sheet_h, self.gap, self.labels]

    def settings(self) -> tuple[FabSettings, SheetSettings]:
        style = self.style.currentData()
        axis = self.axis.currentText()
        fs = FabSettings(style=style, up=self.up.currentText(), axis=axis,
                         thickness=self.thickness.value(), spacing=self.spacing.value(),
                         count=self.count.value(), clearance=self.clearance.value(),
                         rod_diameter=self.rod.value(), dowels=self.dowels.value(),
                         dowel_diameter=self.dowel_d.value(), wall=self.wall.value(),
                         plate_margin=self.margin.value(), guide_outline=self.guide.isChecked())
        ss = SheetSettings(width=self.sheet_w.value(), height=self.sheet_h.value(), gap=self.gap.value(),
                           machine=self.machine.currentData(), kerf=self.kerf.value(),
                           tool_diameter=self.tool.value(), labels=self.labels.isChecked())
        return fs, ss

    def _load(self):
        st = QSettings()
        for name, w in self._named():
            v = st.value(f"fabslice/{name}")
            if v is None:
                continue
            try:
                if isinstance(w, QComboBox):
                    i = w.findData(v) if w.findData(v) >= 0 else w.findText(str(v))
                    if i >= 0:
                        w.setCurrentIndex(i)
                elif isinstance(w, QCheckBox):
                    w.setChecked(str(v).lower() in ("true", "1"))
                elif isinstance(w, QSpinBox):
                    w.setValue(int(v))
                else:
                    w.setValue(float(v))
            except (TypeError, ValueError):
                continue

    def _save(self):
        st = QSettings()
        for name, w in self._named():
            if isinstance(w, QComboBox):
                st.setValue(f"fabslice/{name}", w.currentData() if w.currentData() is not None else w.currentText())
            elif isinstance(w, QCheckBox):
                st.setValue(f"fabslice/{name}", w.isChecked())
            else:
                st.setValue(f"fabslice/{name}", w.value())

    def _named(self):
        names = ["style", "up", "axis", "thickness", "spacing", "count", "clearance", "rod", "dowels",
                 "dowel_d", "wall", "margin", "guide", "machine", "kerf", "tool", "sheet_w", "sheet_h",
                 "gap", "labels"]
        return [(n, getattr(self, n)) for n in names]

    def _changed(self, *_):
        style = self.style.currentData()
        self.help.setText(HELP[style])
        # show only what the chosen style uses
        upright = style in ("rod", "base", "back")
        for w in (self.axis_label, self.axis):
            w.setVisible(upright)
        for i in range(self.axis.count()):         # base / back slice across X or Y only
            self.axis.model().item(i).setEnabled(style == "rod" or self.axis.itemText(i) != "Z")
        if style in ("base", "back") and self.axis.currentText() == "Z":
            self.axis.setCurrentText("X")
        for w in (self.spacing_label, self.spacing, self.count_label, self.count):
            w.setVisible(style != "stacked")
        for w in (self.rod_label, self.rod):
            w.setVisible(style == "rod")
        for w in (self.dowels_label, self.dowels, self.dowel_d_label, self.dowel_d, self.guide):
            w.setVisible(style == "stacked")
        for w in (self.margin_label, self.margin):
            w.setVisible(style in ("base", "back"))
        laser = self.machine.currentData() == "laser"
        for w in (self.kerf_label, self.kerf):
            w.setVisible(laser)
        for w in (self.tool_label, self.tool):
            w.setVisible(not laser)
        self._timer.start()

    # ---- compute and show ----------------------------------------------------------------

    def recompute(self):
        fs, ss = self.settings()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.error = ""
            self.result = fabslice.make(self.shape, fs)
            self.sheets = fabexport.layout(self.result, ss)
        except (fabslice.FabError, ValueError) as e:
            self.error, self.sheets = str(e), []
        except Exception as e:                       # geometry trouble must not kill the window
            self.error, self.sheets = f"Could not slice this model: {e}", []
        finally:
            QApplication.restoreOverrideCursor()
        self._draw_sheets(ss)
        self._show_3d()
        self._summarize()
        self.b_export.setEnabled(bool(self.sheets))

    def _summarize(self):
        if self.error:
            self.summary.setText(f'<span style="color:#e05050">{self.error}</span>')
            return
        r = self.result
        kinds = {}
        for p in r.parts:
            kinds[p.kind] = kinds.get(p.kind, 0) + p.copies
        names = {"slice": "slices", "plate": "plate", "spacer": "spacer rings"}
        what = ", ".join(f"{n} {names[k]}" if k != "plate" else "1 plate" for k, n in kinds.items())
        text = f"<b>{r.count()} parts</b> ({what}) on <b>{len(self.sheets)} sheet{'s' * (len(self.sheets) != 1)}</b>."
        if r.warnings:
            text += "<br>" + "<br>".join(f'<span style="color:#f2a536">{w}</span>' for w in r.warnings[:6])
            if len(r.warnings) > 6:
                text += f"<br>… and {len(r.warnings) - 6} more"
        self.summary.setText(text)

    def _draw_sheets(self, ss: SheetSettings):
        self.scene.clear()
        cut_pen = QPen(QColor("#f2a536"), 0)
        eng_pen = QPen(QColor("#9fc8f2"), 0)
        y = 0.0
        for i, sh in enumerate(self.sheets):
            self.scene.addRect(0, y, ss.width, ss.height, QPen(QColor("#5a6069"), 0), QBrush(QColor("#2a2d33")))
            t = self.scene.addSimpleText(f"Sheet {i + 1}")
            t.setBrush(QColor("#aeb4bd"))
            t.setPos(0, y - 18)
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for ring in sh.cuts:
                path.moveTo(QPointF(ring[0][0], y + ss.height - ring[0][1]))
                for px, py in ring[1:]:
                    path.lineTo(QPointF(px, y + ss.height - py))
                path.closeSubpath()
            self.scene.addPath(path, cut_pen, QBrush(QColor(58, 79, 102, 200)))
            eng = QPainterPath()
            for line in sh.engrave:
                eng.moveTo(QPointF(line[0][0], y + ss.height - line[0][1]))
                for px, py in line[1:]:
                    eng.lineTo(QPointF(px, y + ss.height - py))
            self.scene.addPath(eng, eng_pen)
            y += ss.height + 40
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-10, -30, 10, 10), Qt.KeepAspectRatio)

    def _show_3d(self):
        if self.viewer is None:
            return
        if self.result and not self.error:
            self.viewer.show_model(fabslice.assembly(self.result))
        else:
            self.viewer.show_model(self.shape)
        if hasattr(self.viewer, "refresh"):
            self.viewer.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.scene.itemsBoundingRect().isEmpty():
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-10, -30, 10, 10), Qt.KeepAspectRatio)

    # ---- export --------------------------------------------------------------------------

    def export(self, folder: str | None = None) -> list[Path]:
        if not self.sheets:
            return []
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Folder for the sheet files", self.folder or str(Path.home()))
            if not folder:
                return []
        _, ss = self.settings()
        try:
            files = fabexport.write(self.sheets, ss, folder, self.stem)
        except OSError as e:
            QMessageBox.warning(self, "Export", f"Could not write the files: {e}")
            return []
        self._save()
        if self.isVisible():
            QMessageBox.information(self, "Export", f"Wrote {len(files)} files to {folder}:\n"
                                    + "\n".join(p.name for p in files[:12]) + ("\n…" if len(files) > 12 else ""))
        return files

    def done(self, r):
        self._save()
        if self.viewer is not None:
            self.viewer.show_model(self.shape)
            if hasattr(self.viewer, "refresh"):
                self.viewer.refresh()
        super().done(r)

