"""Design workspace: feature list, feature settings and the sketch toolbar."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QPushButton, QToolBar, QVBoxLayout, QWidget)

from pathlib import Path

from .model import Feature, ModelError, Part
from .sketch import Sketch

FEATURE_CHOICES = {
    "hole": ["simple", "counterbore", "countersink"],
    "operation": ["new", "join", "cut", "intersect"],
    "direction": ["X", "Y", "Z", "circular"],
    "plane": ["XY", "XZ", "YZ"],
}
# fields shown per feature type
FEATURE_FIELDS = {
    "extrude": ["name", "body", "sketch", "distance", "symmetric", "operation", "count", "spacing", "direction", "suppressed"],
    "revolve": ["name", "body", "sketch", "axis", "angle", "operation", "count", "direction", "suppressed"],
    "loft": ["name", "body", "sketch", "operation", "suppressed"],
    "sweep": ["name", "body", "sketch", "path", "operation", "suppressed"],
    "fillet": ["name", "body", "radius", "edges", "suppressed"],
    "chamfer": ["name", "body", "radius", "edges", "suppressed"],
    "shell": ["name", "body", "faces", "thickness", "suppressed"],
    "mirror": ["name", "body", "plane", "suppressed"],
    "relief": ["name", "body", "operation", "image", "width", "depth", "base", "invert", "suppressed"],
    "hole": ["name", "body", "sketch", "hole", "diameter", "depth", "head_diameter", "head_depth", "suppressed"],
}
FEATURE_LABELS = {"image": "Image (in the project)", "base": "Base thickness", "invert": "Dark is high",
                  "Feature.body": "Body (new: name; else target)", "Feature.width": "Width",
                  "Feature.depth": "Depth (hole: 0 = through)", "hole": "Hole type", "depth": "Depth (0 = through)", "head_diameter": "Head Ø",
                  "head_depth": "Counterbore depth", "path": "Path sketch", "radius": "Radius / distance", "edges": "Edges (|Z, >Z, %CIRCLE…)",
                  "faces": "Open faces (>Z, <Z…)", "count": "Pattern count", "spacing": "Pattern spacing",
                  "direction": "Pattern direction", "axis": "Axis (line id)"}


class DesignPanel(QWidget):
    """Edits a Part. Emits `changed` after every edit; `edit_sketch(name)` to open one."""

    changed = Signal()
    edit_sketch = Signal(str)
    status = Signal(str)
    selection = Signal(object)      # current feature (or None): the window highlights its picks

    def __init__(self, parent=None):
        super().__init__(parent)
        from .app import DataForm, CHOICES, HIDDEN, LABELS      # shared typed form
        self._DataForm = DataForm
        self.part: Part | None = None
        self.picked_face = None          # (x, y, z) of the last face clicked with no feature using it
        col = QVBoxLayout(self)

        box = QGroupBox("Features (built top to bottom)")
        bl = QVBoxLayout(box)
        self.l_features = QListWidget()
        self.l_features.currentRowChanged.connect(self._pick)
        self.l_features.itemDoubleClicked.connect(lambda _: self.open_sketch())
        bl.addWidget(self.l_features)
        grid = QGridLayout()
        for i, (text, slot) in enumerate((
                ("Parameters…", self.edit_parameters),
                ("New sketch…", self.new_sketch), ("Sketch on face", self.sketch_on_face),
                ("Extrude", lambda: self.add("extrude")),
                ("Revolve", lambda: self.add("revolve")), ("Loft", lambda: self.add("loft")),
                ("Sweep", lambda: self.add("sweep")), ("Fillet", lambda: self.add("fillet")),
                ("Chamfer", lambda: self.add("chamfer")), ("Shell", lambda: self.add("shell")),
                ("Mirror", lambda: self.add("mirror")), ("Hole", lambda: self.add("hole")),
                ("Relief…", self.add_relief),
                ("Edit sketch", self.open_sketch),
                ("Remove", self.remove), ("Up", lambda: self.move(-1)), ("Down", lambda: self.move(1)))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            grid.addWidget(b, i // 3, i % 3)
        bl.addLayout(grid)
        col.addWidget(box)

        self.form = DataForm()
        from .model import NUMERIC
        self.form.expressions = set(NUMERIC)
        self.form.check_expression = self._check_expression
        self.form.changed.connect(self._edited)
        fbox = QGroupBox("Feature")
        QVBoxLayout(fbox).addWidget(self.form)
        col.addWidget(fbox)
        col.addStretch()
        CHOICES.update(FEATURE_CHOICES)
        LABELS.update({k: v for k, v in FEATURE_LABELS.items() if k not in LABELS or "." in k})

    # ---- part ------------------------------------------------------------------------

    def set_part(self, part: Part) -> None:
        self.part = part
        self._fill()

    def _fill(self, row: int = 0) -> None:
        self.l_features.blockSignals(True)
        self.l_features.clear()
        for i, f in enumerate(self.part.features):
            text = f"{i + 1}. {f.name or f.type.capitalize()}  ·  {f.type}"
            if f.type in ("extrude", "revolve", "loft", "sweep"):
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
        if f is not None and f.type in ("extrude", "revolve", "sweep", "hole"):
            self._sketch_combo(f, "Sketch", "sketch")
        if f is not None and f.type == "sweep":
            self._sketch_combo(f, "Path sketch", "path")
        if f is not None and f.type == "loft":
            self.status.emit("Loft: list the sketches bottom to top, separated by ; (e.g. sketch1;sketch2)")
        self.selection.emit(f)

    def current(self) -> Feature | None:
        row = self.l_features.currentRow()
        feats = self.part.features if self.part else []
        return feats[row] if 0 <= row < len(feats) else None

    def picked(self, kind: str, point, shift: bool) -> None:
        """A click in the 3D view: edges for fillet/chamfer, faces for shell, else a face
        to sketch on."""
        from .model import pick_token
        f = self.current()
        if not kind:
            return
        if f is not None and kind == "edge" and f.type in ("fillet", "chamfer"):
            self._toggle(f, "edges", pick_token(point))
        elif f is not None and kind == "face" and f.type == "shell":
            self._toggle(f, "faces", pick_token(point))
        elif kind == "face":
            self.picked_face = point
            self.status.emit("Face selected. Press Sketch on face to draw on it.")
            self.selection.emit(("face", point))

    def _toggle(self, f: Feature, attr: str, token: str) -> None:
        from .model import _picks
        current = getattr(f, attr)
        tokens = [t.strip() for t in current.split(";") if t.strip().startswith("@")]
        new_pt = _picks(token)[0]
        near = [t for t in tokens if max(abs(a - b) for a, b in zip(_picks(t)[0], new_pt)) < 0.75]
        tokens = [t for t in tokens if t not in near] if near else tokens + [token]
        setattr(f, attr, ";".join(tokens) or ("|Z" if attr == "edges" else ">Z"))
        self.form.set(f, FEATURE_FIELDS.get(f.type))
        self._edited()
        self.selection.emit(f)
        self.status.emit(f"{len(tokens)} {attr} picked. Click again to remove one.")

    def sketch_on_face(self) -> None:
        """New sketch on the picked flat face, any orientation; its normal points out of the
        material, so a positive extrude adds and a negative one cuts."""
        if self.picked_face is None:
            self.status.emit("Click a flat face in the 3D view first.")
            return
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane
        from OCP.TopAbs import TopAbs_FACE
        from .model import _nearest
        from . import geometry
        try:
            shape = self.part.build()
        except ModelError as e:
            self.status.emit(str(e))
            return
        face = _nearest(geometry.unique(shape, TopAbs_FACE), self.picked_face)
        surf = BRepAdaptor_Surface(face) if face is not None else None
        if surf is None or surf.GetType() != GeomAbs_Plane:
            self.status.emit("Sketch on face needs a flat face.")
            return
        from OCP.TopAbs import TopAbs_REVERSED
        d = surf.Plane().Axis().Direction()
        normal = [d.X(), d.Y(), d.Z()]
        if face.Orientation() == TopAbs_REVERSED:          # make it point out of the material
            normal = [-c for c in normal]
        n = 1
        while f"sketch{n}" in self.part.sketches:
            n += 1
        name = f"sketch{n}"
        self.part.sketches[name] = Sketch.on_plane(name, self.picked_face, normal)
        self.picked_face = None
        self.changed.emit()
        self.edit_sketch.emit(name)

    def _sketch_combo(self, f: Feature, label: str, attr: str) -> None:
        """Replace a free-text sketch field with a list of the part's sketches."""
        lay = self.form.layout_
        for i in range(lay.rowCount()):
            if lay.itemAt(i, lay.ItemRole.LabelRole).widget().text() == label:
                combo = QComboBox()
                combo.addItems(sorted(self.part.sketches))
                combo.setCurrentText(getattr(f, attr))
                combo.currentTextChanged.connect(lambda t: (setattr(f, attr, t), self._edited()))
                lay.removeRow(i)
                lay.insertRow(i, label, combo)
                break

    def _edited(self) -> None:
        row = self.l_features.currentRow()
        if 0 <= row < len(self.part.features):
            f = self.part.features[row]
            item = self.l_features.item(row)
            text = f"{row + 1}. {f.name or f.type.capitalize()}  ·  {f.type}"
            if f.type in ("extrude", "revolve", "loft", "sweep"):
                text += f" {f.sketch}, {f.operation}"
            item.setText(text + ("  (off)" if f.suppressed else ""))
        self.changed.emit()

    def _check_expression(self, text: str):
        from .params import ParameterError, evaluate
        try:
            evaluate(text, self.part.parameter_values())
            return None
        except (ParameterError, ModelError) as e:
            return str(e)

    def edit_parameters(self) -> None:
        """Table of named parameters: name, expression, current value."""
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem)
        from .params import ParameterError, resolve
        dlg = QDialog(self)
        dlg.setWindowTitle("Parameters")
        dlg.resize(520, 360)
        col = QVBoxLayout(dlg)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Name", "Expression", "Value (mm or count)"])
        table.horizontalHeader().setStretchLastSection(True)
        col.addWidget(table)
        note = QLabel()
        note.setWordWrap(True)
        col.addWidget(note)

        def add_row(name="", expr=""):
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(name))
            table.setItem(r, 1, QTableWidgetItem(expr))
            item = QTableWidgetItem("")
            item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
            table.setItem(r, 2, item)

        def rows():
            out = {}
            for r in range(table.rowCount()):
                n, e = table.item(r, 0), table.item(r, 1)
                if n and e and n.text().strip():
                    out[n.text().strip()] = e.text().strip() or "0"
            return out

        def refresh():
            table.blockSignals(True)
            try:
                values = resolve(rows())
                note.setText("Use these names in dimensions and feature values, e.g. width / 2.")
                note.setStyleSheet("")
            except ParameterError as e:
                values = {}
                note.setText(str(e))
                note.setStyleSheet("color: #e5534b")
            for r in range(table.rowCount()):
                n = table.item(r, 0).text().strip() if table.item(r, 0) else ""
                table.item(r, 2).setText(f"{values[n]:g}" if n in values else "")
            table.blockSignals(False)

        for name, expr in self.part.parameters.items():
            add_row(name, expr)
        table.itemChanged.connect(lambda _: refresh())
        buttons = QHBoxLayout()
        b_add, b_del = QPushButton("Add"), QPushButton("Remove")
        b_add.clicked.connect(lambda: (add_row(f"p{table.rowCount() + 1}", "10"), refresh()))
        b_del.clicked.connect(lambda: (table.removeRow(table.currentRow()), refresh()))
        buttons.addWidget(b_add)
        buttons.addWidget(b_del)
        buttons.addStretch()
        col.addLayout(buttons)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        col.addWidget(box)
        refresh()
        if dlg.exec() == QDialog.Accepted:
            try:
                resolve(rows())
            except ParameterError as e:
                self.status.emit(f"Parameters not saved: {e}")
                return
            self.part.parameters = rows()
            self.changed.emit()
            self.status.emit(f"{len(self.part.parameters)} parameters.")

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
        if kind in ("loft", "sweep"):
            if len(sketches) < 2:
                self.status.emit(f"{kind.capitalize()} needs two sketches: a profile and a "
                                 + ("second profile" if kind == "loft" else "path"))
                return
            f.sketch = ";".join(sketches[-2:]) if kind == "loft" else sketches[-2]
            if kind == "sweep":
                f.path = sketches[-1]
            f.operation = "new" if not self.part.features else "join"
        if kind == "hole":
            if not sketches:
                self.status.emit("Put points in a sketch first (Sketch on face, then Point).")
                return
            f.sketch = sketches[-1]
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

    def add_relief(self) -> None:
        """A relief feature from a picture, copied into the project's images/ folder."""
        import shutil
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Picture for the relief", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        folder = Path(getattr(self.part, "folder", "."))
        dest = folder / "images" / Path(path).name
        dest.parent.mkdir(exist_ok=True)
        if Path(path).resolve() != dest.resolve():
            shutil.copy2(path, dest)
        width, ok = QInputDialog.getDouble(self, "Relief", "Width (mm)", 100.0, 1, 5000, 2)
        if not ok:
            return
        depth, ok = QInputDialog.getDouble(self, "Relief", "Depth from darkest to lightest (mm)", 5.0, 0.1, 500, 2)
        if not ok:
            return
        f = Feature("relief", name=f"Relief {sum(1 for x in self.part.features if x.type == 'relief') + 1}",
                    image=str(dest.relative_to(folder)), width=width, depth=depth,
                    operation="new" if not self.part.features else "join")
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


def _trace_dialog(view) -> None:
    from PySide6.QtWidgets import QFileDialog
    path, _ = QFileDialog.getOpenFileName(view, "Picture to trace", str(Path.home()),
                                          "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
    if not path:
        return
    width, ok = QInputDialog.getDouble(view, "Trace image", "Width of the traced shape (mm)", 80.0, 1, 5000, 2)
    if not ok:
        return
    which, ok = QInputDialog.getItem(view, "Trace image", "Trace the", ["dark areas", "light areas"], 0, False)
    if not ok:
        return
    made = view.sketch.trace_image(path, 0.0, 0.0, width, invert=which.startswith("light"))
    view.commit()
    view.fit()
    view.status.emit(f"Traced {len(made)} lines into {len(view.sketch.profiles())} closed outlines.")


def _export_dxf(view) -> None:
    from PySide6.QtWidgets import QFileDialog
    from . import dxf
    path, _ = QFileDialog.getSaveFileName(view, "Save DXF", f"{view.sketch.name}.dxf", "DXF (*.dxf)")
    if path:
        dxf.export_dxf(view.sketch, path)
        view.status.emit(f"Saved {path}")


def sketch_toolbar(view, on_finish) -> QWidget:
    """Two rows for a SketchView: drawing tools and actions, then constraints."""
    from .sketcher import RULES
    tools, cons = QToolBar("Sketch"), QToolBar("Constraints")
    for tb in (tools, cons):
        tb.setMovable(False)
    for key, text in (("select", "Select"), ("line", "Line"), ("rectangle", "Rectangle"),
                      ("circle", "Circle"), ("arc", "Arc"), ("spline", "Spline"), ("polygon", "Polygon"), ("slot", "Slot"),
                      ("text", "Text"), ("point", "Point"), ("trim", "Trim")):
        a = tools.addAction(text)
        a.setCheckable(True)
        a.setChecked(key == "select")
        a.setData(key)
        a.triggered.connect(lambda _=False, k=key: (view.set_tool(k),
                            [x.setChecked(x.data() == k) for x in tools.actions() if x.isCheckable()]))
    tools.addSeparator()
    for text, slot, tip in (("Fillet", view.fillet_selected, "Round the selected corner point"),
                            ("Offset", view.offset_selected, "Parallel copy of the selected closed shape"),
                            ("Mirror", view.mirror_selected, "Mirror the selection across the last selected line")):
        a = tools.addAction(text)
        a.setToolTip(tip)
        a.triggered.connect(lambda _=False, f=slot: f())
    tools.addSeparator()
    a = tools.addAction("Dimension")
    a.setToolTip("Length, distance, diameter or angle of the selection")
    a.triggered.connect(lambda: view.dimension())
    a = tools.addAction("Construction")
    a.setToolTip("Toggle construction (reference) geometry for the selected lines and circles")

    def toggle_construction():
        s = view.sketch
        for e in view.selected:
            ent = s.lines.get(e) or s.circles.get(e) or s.arcs.get(e) or s.splines.get(e)
            if ent:
                ent.construction = not ent.construction
        view.commit()
    a.triggered.connect(toggle_construction)
    tools.addSeparator()
    a = tools.addAction("Trace image…")
    a.setToolTip("Trace a picture's dark areas into outlines")
    a.triggered.connect(lambda: _trace_dialog(view))
    a = tools.addAction("Export DXF…")
    a.triggered.connect(lambda: _export_dxf(view))
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
