"""Design workspace: feature list, feature settings and the sketch toolbar."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QPushButton, QToolBar, QVBoxLayout, QWidget)

from .model import Feature, ModelError, Part
from .sketch import Sketch

FEATURE_CHOICES = {
    "operation": ["new", "join", "cut", "intersect"],
    "direction": ["X", "Y", "Z", "circular"],
    "plane": ["XY", "XZ", "YZ"],
}
# fields shown per feature type
FEATURE_FIELDS = {
    "extrude": ["name", "sketch", "distance", "symmetric", "operation", "count", "spacing", "direction", "suppressed"],
    "revolve": ["name", "sketch", "axis", "angle", "operation", "count", "direction", "suppressed"],
    "fillet": ["name", "radius", "edges", "suppressed"],
    "chamfer": ["name", "radius", "edges", "suppressed"],
    "shell": ["name", "faces", "thickness", "suppressed"],
    "mirror": ["name", "plane", "suppressed"],
}
FEATURE_LABELS = {"radius": "Radius / distance", "edges": "Edges (|Z, >Z, %CIRCLE…)",
                  "faces": "Open faces (>Z, <Z…)", "count": "Pattern count", "spacing": "Pattern spacing",
                  "direction": "Pattern direction", "axis": "Axis (line id)"}


class DesignPanel(QWidget):
    """Edits a Part. Emits `changed` after every edit; `edit_sketch(name)` to open one."""

    changed = Signal()
    edit_sketch = Signal(str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from .app import DataForm, CHOICES, HIDDEN, LABELS      # shared typed form
        self._DataForm = DataForm
        self.part: Part | None = None
        col = QVBoxLayout(self)

        box = QGroupBox("Features (built top to bottom)")
        bl = QVBoxLayout(box)
        self.l_features = QListWidget()
        self.l_features.currentRowChanged.connect(self._pick)
        self.l_features.itemDoubleClicked.connect(lambda _: self.open_sketch())
        bl.addWidget(self.l_features)
        grid = QGridLayout()
        for i, (text, slot) in enumerate((
                ("New sketch…", self.new_sketch), ("Extrude", lambda: self.add("extrude")),
                ("Revolve", lambda: self.add("revolve")), ("Fillet", lambda: self.add("fillet")),
                ("Chamfer", lambda: self.add("chamfer")), ("Shell", lambda: self.add("shell")),
                ("Mirror", lambda: self.add("mirror")), ("Edit sketch", self.open_sketch),
                ("Remove", self.remove), ("Up", lambda: self.move(-1)), ("Down", lambda: self.move(1)))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            grid.addWidget(b, i // 3, i % 3)
        bl.addLayout(grid)
        col.addWidget(box)

        self.form = DataForm()
        self.form.changed.connect(self._edited)
        fbox = QGroupBox("Feature")
        QVBoxLayout(fbox).addWidget(self.form)
        col.addWidget(fbox)
        col.addStretch()
        CHOICES.update(FEATURE_CHOICES)
        LABELS.update({k: v for k, v in FEATURE_LABELS.items() if k not in LABELS})

    # ---- part ------------------------------------------------------------------------

    def set_part(self, part: Part) -> None:
        self.part = part
        self._fill()

    def _fill(self, row: int = 0) -> None:
        self.l_features.blockSignals(True)
        self.l_features.clear()
        for i, f in enumerate(self.part.features):
            text = f"{i + 1}. {f.name or f.type.capitalize()}  ·  {f.type}"
            if f.type in ("extrude", "revolve"):
                text += f" {f.sketch}, {f.operation}"
            self.l_features.addItem(text + ("  (off)" if f.suppressed else ""))
        self.l_features.blockSignals(False)
        if self.part.features:
            self.l_features.setCurrentRow(max(0, min(row, len(self.part.features) - 1)))
        self._pick(self.l_features.currentRow())

    def _pick(self, row: int) -> None:
        feats = self.part.features if self.part else []
        f = feats[row] if 0 <= row < len(feats) else None
        self.form.set(f, FEATURE_FIELDS.get(f.type) if f else None)
        if f is not None and f.type in ("extrude", "revolve"):
            self._sketch_combo(f)

    def _sketch_combo(self, f: Feature) -> None:
        """Replace the free-text sketch field with a list of the part's sketches."""
        lay = self.form.layout_
        for i in range(lay.rowCount()):
            if lay.itemAt(i, lay.ItemRole.LabelRole).widget().text() == "Sketch":
                combo = QComboBox()
                combo.addItems(sorted(self.part.sketches))
                combo.setCurrentText(f.sketch)
                combo.currentTextChanged.connect(lambda t: (setattr(f, "sketch", t), self._edited()))
                lay.removeRow(i)
                lay.insertRow(i, "Sketch", combo)
                break

    def _edited(self) -> None:
        row = self.l_features.currentRow()
        if 0 <= row < len(self.part.features):
            f = self.part.features[row]
            item = self.l_features.item(row)
            text = f"{row + 1}. {f.name or f.type.capitalize()}  ·  {f.type}"
            if f.type in ("extrude", "revolve"):
                text += f" {f.sketch}, {f.operation}"
            item.setText(text + ("  (off)" if f.suppressed else ""))
        self.changed.emit()

    # ---- editing ----------------------------------------------------------------------

    def new_sketch(self) -> None:
        plane, ok = QInputDialog.getItem(self, "New sketch", "Plane", ["XY", "XZ", "YZ"], 0, False)
        if not ok:
            return
        offset, ok = QInputDialog.getDouble(self, "New sketch", "Offset along the plane normal (mm)", 0.0, -1e5, 1e5, 3)
        if not ok:
            return
        n = 1
        while f"sketch{n}" in self.part.sketches:
            n += 1
        name = f"sketch{n}"
        self.part.sketches[name] = Sketch(name=name, plane=plane, offset=offset)
        self.changed.emit()
        self.edit_sketch.emit(name)

    def add(self, kind: str) -> None:
        sketches = sorted(self.part.sketches)
        f = Feature(kind, name=f"{kind.capitalize()} {sum(1 for x in self.part.features if x.type == kind) + 1}")
        if kind in ("extrude", "revolve"):
            if not sketches:
                self.status.emit("Make a sketch first: New sketch…")
                return
            used = {x.sketch for x in self.part.features}
            f.sketch = next((s for s in sketches if s not in used), sketches[-1])
            f.operation = "new" if not self.part.features else "join"
            if kind == "revolve":
                sk = self.part.sketches[f.sketch]
                f.axis = next((l.id for l in sk.lines.values() if l.construction), next(iter(sk.lines), ""))
        self.part.features.append(f)
        self._fill(len(self.part.features) - 1)
        self.changed.emit()

    def remove(self) -> None:
        row = self.l_features.currentRow()
        if 0 <= row < len(self.part.features):
            del self.part.features[row]
            self._fill(row)
            self.changed.emit()

    def move(self, delta: int) -> None:
        feats, row = self.part.features, self.l_features.currentRow()
        if 0 <= row < len(feats) and 0 <= row + delta < len(feats):
            feats[row], feats[row + delta] = feats[row + delta], feats[row]
            self._fill(row + delta)
            self.changed.emit()

    def open_sketch(self) -> None:
        row = self.l_features.currentRow()
        name = None
        if 0 <= row < len(self.part.features):
            name = self.part.features[row].sketch or None
        if not name and self.part.sketches:
            name, ok = QInputDialog.getItem(self, "Edit sketch", "Sketch", sorted(self.part.sketches), 0, False)
            if not ok:
                return
        if name:
            self.edit_sketch.emit(name)


def sketch_toolbar(view, on_finish) -> QWidget:
    """Two rows for a SketchView: drawing tools and actions, then constraints."""
    from .sketcher import RULES
    tools, cons = QToolBar("Sketch"), QToolBar("Constraints")
    for tb in (tools, cons):
        tb.setMovable(False)
    for key, text in (("select", "Select"), ("line", "Line"), ("rectangle", "Rectangle"),
                      ("circle", "Circle"), ("arc", "Arc"), ("point", "Point")):
        a = tools.addAction(text)
        a.setCheckable(True)
        a.setChecked(key == "select")
        a.setData(key)
        a.triggered.connect(lambda _=False, k=key: (view.set_tool(k),
                            [x.setChecked(x.data() == k) for x in tools.actions() if x.isCheckable()]))
    tools.addSeparator()
    a = tools.addAction("Dimension")
    a.setToolTip("Length, distance, diameter or angle of the selection")
    a.triggered.connect(lambda: view.dimension())
    a = tools.addAction("Construction")
    a.setToolTip("Toggle construction (reference) geometry for the selected lines and circles")

    def toggle_construction():
        s = view.sketch
        for e in view.selected:
            ent = s.lines.get(e) or s.circles.get(e) or s.arcs.get(e)
            if ent:
                ent.construction = not ent.construction
        view.commit()
    a.triggered.connect(toggle_construction)
    a = tools.addAction("Fit")
    a.triggered.connect(view.fit)
    tools.addSeparator()
    a = tools.addAction("Finish sketch")
    a.triggered.connect(on_finish)
    for name in RULES:
        a = cons.addAction(name.replace("_", " ").capitalize())
        a.setToolTip("Needs: " + " or ".join(" + ".join(r) for r in RULES[name]))
        a.triggered.connect(lambda _=False, n=name: view.apply_constraint(n))
    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(0)
    col.addWidget(tools)
    col.addWidget(cons)
    return box
