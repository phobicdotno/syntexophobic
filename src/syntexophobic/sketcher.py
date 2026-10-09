"""Interactive 2D sketch editor (Qt Graphics View) on top of sketch.Sketch.

Units are millimetres with Y up. Clicking near an existing point reuses it, which joins
geometry without extra constraints. After every change the sketch is solved; geometry
is drawn blue while it can still move and white once fully constrained.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QInputDialog

from .sketch import Sketch

FREE = QColor(90, 170, 255)
FIXED = QColor(235, 238, 242)
SELECTED = QColor(255, 176, 59)
CONSTRUCTION = QColor(140, 146, 156)
CONFLICT = QColor(235, 70, 70)
SNAP_PX = 9

# Which selections each constraint button accepts: tuple of entity kinds in any order.
RULES = {
    "coincident": [("point", "point")],
    "horizontal": [("line",), ("point", "point")],
    "vertical": [("line",), ("point", "point")],
    "parallel": [("line", "line")],
    "perpendicular": [("line", "line")],
    "equal": [("line", "line"), ("round", "round")],
    "tangent": [("line", "round"), ("round", "round")],
    "concentric": [("round", "round")],
    "midpoint": [("point", "line")],
    "point_on": [("point", "line"), ("point", "round")],
    "fixed": [("point",)],
    "symmetric": [("point", "point", "line")],
}


class SketchView(QGraphicsView):
    changed = Signal()                  # geometry or constraints changed (and were solved)
    status = Signal(str)                # human-readable solver state

    def __init__(self, sketch: Sketch | None = None, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setBackgroundBrush(QColor(30, 33, 38))
        self.scale(4, -4)                                # 4 px per mm, Y up
        self.sketch = sketch or Sketch()
        self.tool = "select"
        self.selected: list[str] = []
        self.pending: list[str] = []                     # points placed by the current tool
        self.dragging: str | None = None
        self.preview = None
        self.result = None
        self.conflicts: set[str] = set()
        self.setMouseTracking(True)
        self.setSceneRect(QRectF(-2000, -2000, 4000, 4000))
        self.solve_and_draw()
        self.centerOn(0, 0)

    # ---- helpers ---------------------------------------------------------------------

    def kind(self, eid: str) -> str:
        s = self.sketch
        if eid in s.points:
            return "point"
        if eid in s.lines:
            return "line"
        if eid in s.splines:
            return "spline"
        return "round"

    def px(self, n: float) -> float:
        """n screen pixels in scene millimetres."""
        return n / max(self.transform().m11(), 1e-9)

    def at(self, pos: QPointF):
        """Entity under a scene position: points first, then lines, circles and arcs."""
        s, tol = self.sketch, self.px(SNAP_PX)
        best, bd = None, tol
        for p in s.points.values():
            d = math.hypot(p.x - pos.x(), p.y - pos.y())
            if d < bd:
                best, bd = p.id, d
        if best:
            return best
        for ln in s.lines.values():
            a, b = s.points[ln.a], s.points[ln.b]
            d = _seg_dist((pos.x(), pos.y()), (a.x, a.y), (b.x, b.y))
            if d < bd:
                best, bd = ln.id, d
        for c in list(s.circles.values()) + list(s.arcs.values()):
            ctr = s.points[c.center]
            d = abs(math.hypot(pos.x() - ctr.x, pos.y() - ctr.y) - c.r)
            if d < bd:
                best, bd = c.id, d
        for sp in s.splines.values():
            pts = self._spline_pts(sp.id)
            for a, b in zip(pts, pts[1:]):
                d = _seg_dist((pos.x(), pos.y()), a, b)
                if d < bd:
                    best, bd = sp.id, d
        return best

    def _spline_pts(self, sid):
        try:
            return self.sketch.spline_samples(sid, 48)
        except Exception:                 # points on top of each other: draw straight
            return [(self.sketch.points[p].x, self.sketch.points[p].y) for p in self.sketch.splines[sid].points]

    def point_at(self, pos: QPointF) -> str:
        """Existing point under the cursor, or a new one there."""
        hit = self.at(pos)
        if hit and hit in self.sketch.points:
            return hit
        return self.sketch.point(round(pos.x(), 3), round(pos.y(), 3))

    # ---- tools -------------------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.pending = []
        self.preview = None
        self.setCursor(Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)
        self.status.emit({"select": "Select. Drag points to move them.",
                          "line": "Line: click points; Esc or right-click ends the chain.",
                          "rectangle": "Rectangle: click two corners.",
                          "circle": "Circle: click the centre, then a point on it.",
                          "arc": "Arc: click the centre, the start, then the end (counter-clockwise).",
                          "point": "Point: click to place.",
                          "polygon": "Polygon: click the centre, then a corner.",
                          "spline": "Spline: click points; Enter or right-click ends it, clicking the first point closes it.",
                          "slot": "Slot: click both centre points, then the width.",
                          "trim": "Trim: click the piece of a line to remove."}.get(tool, tool))
        self.draw()

    def mousePressEvent(self, ev):
        pos = self.mapToScene(ev.position().toPoint())
        if ev.button() == Qt.MiddleButton:
            self._pan_from = ev.position()
            return
        if ev.button() == Qt.RightButton:
            self.finish_spline()
            self.pending = []
            self.preview = None
            self.draw()
            return
        s = self.sketch
        if self.tool == "select":
            hit = self.at(pos)
            if ev.modifiers() & Qt.ShiftModifier:
                if hit:
                    self.selected = [e for e in self.selected if e != hit] if hit in self.selected else self.selected + [hit]
            else:
                self.selected = [hit] if hit else []
            if hit and hit in s.points:
                self.dragging = hit
            self.draw()
        elif self.tool == "point":
            self.point_at(pos)
            self.commit()
        elif self.tool == "line":
            p = self.point_at(pos)
            if self.pending and self.pending[-1] != p:
                s.line(self.pending[-1], p)
                self.commit(redraw=False)
            self.pending = [p]
            self.draw()
        elif self.tool == "rectangle":
            if not self.pending:
                self.pending = [(pos.x(), pos.y())]
            else:
                (x0, y0), (x1, y1) = self.pending[0], (pos.x(), pos.y())
                s.rectangle(round(min(x0, x1), 3), round(min(y0, y1), 3), round(max(x0, x1), 3), round(max(y0, y1), 3))
                self.pending = []
                self.commit()
        elif self.tool == "circle":
            if not self.pending:
                self.pending = [self.point_at(pos)]
            else:
                c = s.points[self.pending[0]]
                s.circle(self.pending[0], max(0.1, round(math.hypot(pos.x() - c.x, pos.y() - c.y), 3)))
                self.pending = []
                self.commit()
        elif self.tool == "polygon":
            if not self.pending:
                self.pending = [(pos.x(), pos.y())]
            else:
                (cx, cy), r = self.pending[0], math.hypot(pos.x() - self.pending[0][0], pos.y() - self.pending[0][1])
                sides, ok = (self._sides, True) if hasattr(self, "_sides") else \
                    QInputDialog.getInt(self, "Polygon", "Sides", 6, 3, 64)
                self.pending = []
                if ok and r > 0.1:
                    s.polygon(round(cx, 3), round(cy, 3), round(r, 3), sides)
                    self.commit()
        elif self.tool == "slot":
            self.pending.append((pos.x(), pos.y()))
            if len(self.pending) == 3:
                (x0, y0), (x1, y1), (wx, wy) = self.pending
                self.pending = []
                L = math.hypot(x1 - x0, y1 - y0)
                if L > 0.1:
                    width = 2 * abs((x1 - x0) * (wy - y0) - (y1 - y0) * (wx - x0)) / L
                    s.slot(x0, y0, x1, y1, max(0.2, round(width, 3)))
                    self.commit()
            self.draw()
        elif self.tool == "spline":
            p = self.point_at(pos)
            if len(self.pending) >= 3 and p == self.pending[0]:
                s.spline(self.pending, closed=True)
                self.pending = []
                self.commit()
            elif not self.pending or p != self.pending[-1]:
                self.pending.append(p)
                self.draw()
        elif self.tool == "trim":
            hit = self.at(pos)
            if hit in s.lines:
                s.trim(hit, pos.x(), pos.y())
                self.selected = []
                self.commit()
        elif self.tool == "arc":
            self.pending.append(self.point_at(pos))
            if len(self.pending) == 3:
                c, st, en = self.pending
                s.arc(c, st, en)
                self.pending = []
                self.commit()
            self.draw()

    def mouseMoveEvent(self, ev):
        pos = self.mapToScene(ev.position().toPoint())
        if getattr(self, "_pan_from", None) is not None:
            d = ev.position() - self._pan_from
            self._pan_from = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self.dragging:
            self.drag_point(self.dragging, pos.x(), pos.y())
            return
        self.preview = pos if self.pending else None
        if self.pending:
            self.draw()

    def mouseReleaseEvent(self, ev):
        self._pan_from = None
        if self.dragging:
            self.dragging = None
            self.changed.emit()

    def wheelEvent(self, ev):
        f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self.draw()

    def finish_spline(self) -> None:
        if self.tool == "spline" and len(self.pending) >= 2:
            self.sketch.spline(self.pending)
            self.pending = []
            self.commit()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_spline()
            return
        if ev.key() == Qt.Key_Escape:
            if self.pending:
                self.pending = []
                self.preview = None
                self.draw()
            else:
                self.set_tool("select")
        elif ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            for eid in self.selected:
                if eid in self.sketch.points or eid in self.sketch.lines or eid in self.sketch.circles \
                        or eid in self.sketch.arcs:
                    self.sketch.delete(eid)
            self.selected = []
            self.commit()
        else:
            super().keyPressEvent(ev)

    def drag_point(self, pid: str, x: float, y: float) -> None:
        """Move a point to (x, y) and let the rest of the sketch follow."""
        s = self.sketch
        s.points[pid].x, s.points[pid].y = x, y
        kx = s.constrain("x", pid, value=x)
        ky = s.constrain("y", pid, value=y)
        res = s.solve()
        s.constraints = [c for c in s.constraints if c.id not in (kx, ky)]
        if not res.ok:                     # the point can't go there: solve without forcing it
            s.solve()
        self.solve_and_draw()

    # ---- constraints -------------------------------------------------------------------

    def can_apply(self, name: str) -> bool:
        kinds = sorted(self.kind(e) for e in self.selected)
        return any(sorted(r) == kinds for r in RULES.get(name, []))

    def apply_constraint(self, name: str) -> bool:
        """Add a constraint between the selected entities (order-insensitive)."""
        if not self.can_apply(name):
            self.status.emit(f"{name.replace('_', ' ').capitalize()} needs: " +
                             " or ".join(" + ".join(r) for r in RULES[name]))
            return False
        s, sel = self.sketch, list(self.selected)
        by = lambda k: [e for e in sel if self.kind(e) == k]       # noqa: E731
        if name in ("horizontal", "vertical") and len(sel) == 2:
            a, b = sel
            s.constrain("horizontal_distance" if name == "vertical" else "vertical_distance", a, b, value=0.0)
        elif name == "point_on":
            p = by("point")[0]
            other = [e for e in sel if e != p][0]
            s.constrain("point_on_line" if self.kind(other) == "line" else "point_on_circle", p, other)
        elif name == "midpoint":
            s.constrain("midpoint", by("point")[0], by("line")[0])
        elif name == "tangent" and by("line"):
            s.constrain("tangent", by("line")[0], by("round")[0])
        elif name == "symmetric":
            pts = by("point")
            s.constrain("symmetric", pts[0], pts[1], by("line")[0])
        else:
            s.constrain(name, *sel)
        self.commit()
        return True

    def dimension(self, value: float | None = None) -> bool:
        """Length of a line, distance of two points or point-line, radius, or angle."""
        s, sel = self.sketch, list(self.selected)
        kinds = sorted(self.kind(e) for e in sel)
        if kinds == ["line"]:
            ln = s.lines[sel[0]]
            a, b = s.points[ln.a], s.points[ln.b]
            current, kind, refs = math.hypot(b.x - a.x, b.y - a.y), "distance", sel
        elif kinds == ["point", "point"]:
            a, b = (s.points[e] for e in sel)
            current, kind, refs = math.hypot(b.x - a.x, b.y - a.y), "distance", sel
        elif kinds == ["line", "point"]:
            p = [e for e in sel if self.kind(e) == "point"][0]
            ln = [e for e in sel if self.kind(e) == "line"][0]
            L = s.lines[ln]
            a, b, q = s.points[L.a], s.points[L.b], s.points[p]
            current = abs((b.x - a.x) * (q.y - a.y) - (b.y - a.y) * (q.x - a.x)) / (math.hypot(b.x - a.x, b.y - a.y) or 1)
            kind, refs = "distance", [p, ln]
        elif kinds == ["round"]:
            e = s.circles.get(sel[0]) or s.arcs[sel[0]]
            current, kind, refs = e.r * 2, "diameter", sel
        elif kinds == ["line", "line"]:
            l1, l2 = (s.lines[e] for e in sel)
            t = lambda L: math.atan2(s.points[L.b].y - s.points[L.a].y, s.points[L.b].x - s.points[L.a].x)  # noqa: E731
            current, kind, refs = math.degrees(t(l2) - t(l1)) % 360, "angle", sel
        else:
            self.status.emit("Dimension needs: a line, two points, a point and a line, a circle or arc, or two lines")
            return False
        expr = None
        if value is None:
            unit = "°" if kind == "angle" else "mm"
            text, ok = QInputDialog.getText(self, "Dimension",
                                            f"{kind.capitalize()} ({unit}): a number or an expression like width / 2",
                                            text=f"{current:.3f}".rstrip("0").rstrip("."))
            if not ok or not text.strip():
                return False
            from .params import ParameterError, evaluate
            try:
                value = evaluate(text, self.sketch.param_values)
            except ParameterError as e:
                self.status.emit(str(e))
                return False
            if not _is_plain_number(text):
                expr = text.strip()
        cid = s.constrain(kind, *refs, value=value)
        if expr:
            next(k for k in s.constraints if k.id == cid).expr = expr
        self.commit()
        return True

    def fillet_selected(self, radius: float | None = None) -> bool:
        pts = [e for e in self.selected if self.kind(e) == "point"]
        if len(pts) != 1:
            self.status.emit("Fillet: select the corner point where two lines meet.")
            return False
        if radius is None:
            radius, ok = QInputDialog.getDouble(self, "Fillet", "Radius (mm)", 3.0, 0.01, 1e5, 3)
            if not ok:
                return False
        try:
            self.sketch.fillet(pts[0], radius)
        except ValueError as e:
            self.status.emit(str(e))
            return False
        self.selected = []
        self.commit()
        return True

    def offset_selected(self, distance: float | None = None) -> bool:
        lines = [e for e in self.selected if self.kind(e) == "line"]
        if len(lines) < 3:
            self.status.emit("Offset: select all lines of one closed shape.")
            return False
        if distance is None:
            distance, ok = QInputDialog.getDouble(self, "Offset", "Distance (mm, negative = inwards)", 2.0, -1e5, 1e5, 3)
            if not ok:
                return False
        try:
            new = self.sketch.offset_lines(lines, distance)
        except ValueError as e:
            self.status.emit(str(e))
            return False
        self.selected = new
        self.commit()
        return True

    def mirror_selected(self) -> bool:
        """Mirror the selection across the last selected line."""
        if len(self.selected) < 2 or self.kind(self.selected[-1]) != "line":
            self.status.emit("Mirror: select what to mirror, then the mirror line last.")
            return False
        *items, axis = self.selected
        self.selected = self.sketch.mirror(items, axis)
        self.commit()
        return True

    # ---- solve + draw -----------------------------------------------------------------

    def commit(self, redraw: bool = True) -> None:
        self.solve_and_draw() if redraw else self.sketch.solve()
        self.changed.emit()

    def solve_and_draw(self) -> None:
        self.result = self.sketch.solve()
        self.conflicts = set(self.result.conflicting)
        r = self.result
        if not r.ok:
            self.status.emit(f"Conflicting constraints: {', '.join(r.conflicting)}. Delete one of them.")
        elif r.dof == 0:
            self.status.emit("Fully constrained.")
        else:
            self.status.emit(f"{r.dof} degrees of freedom left.")
        self.draw()

    def draw(self) -> None:
        sc, s = self.scene_, self.sketch
        sc.clear()
        w = self.px(1.6)
        done = self.result is not None and self.result.ok and self.result.dof == 0
        base = FIXED if done else FREE

        def pen(eid, construction=False):
            colour = SELECTED if eid in self.selected else CONSTRUCTION if construction else base
            p = QPen(colour, w * (1.6 if eid in self.selected else 1.0))
            p.setCosmetic(False)
            if construction:
                p.setStyle(Qt.DashLine)
            return p

        # grid
        grid = QPen(QColor(48, 52, 60), self.px(1))
        for i in range(-200, 201, 10):
            sc.addLine(i, -2000, i, 2000, grid) if i % 50 else None
            sc.addLine(-2000, i, 2000, i, grid) if i % 50 else None
        axis = QPen(QColor(70, 76, 88), self.px(1.2))
        sc.addLine(-2000, 0, 2000, 0, axis)
        sc.addLine(0, -2000, 0, 2000, axis)

        for ln in s.lines.values():
            a, b = s.points[ln.a], s.points[ln.b]
            sc.addLine(a.x, a.y, b.x, b.y, pen(ln.id, ln.construction))
        for c in s.circles.values():
            ctr = s.points[c.center]
            sc.addEllipse(ctr.x - c.r, ctr.y - c.r, 2 * c.r, 2 * c.r, pen(c.id, c.construction))
        for a in s.arcs.values():
            ctr, st, en = s.points[a.center], s.points[a.start], s.points[a.end]
            t0 = math.degrees(math.atan2(st.y - ctr.y, st.x - ctr.x))
            t1 = math.degrees(math.atan2(en.y - ctr.y, en.x - ctr.x))
            span = (t1 - t0) % 360 or 360
            path = QPainterPath()
            rect = QRectF(ctr.x - a.r, ctr.y - a.r, 2 * a.r, 2 * a.r)
            path.arcMoveTo(rect, -t0)                    # Qt angles run clockwise with our Y-up flip
            path.arcTo(rect, -t0, -span)
            sc.addPath(path, pen(a.id, a.construction))
        for sp in s.splines.values():
            pts = self._spline_pts(sp.id)
            path = QPainterPath(QPointF(*pts[0]))
            for q in pts[1:]:
                path.lineTo(QPointF(*q))
            sc.addPath(path, pen(sp.id, sp.construction))
        r = self.px(3.2)
        for p in s.points.values():
            colour = SELECTED if p.id in self.selected else base
            sc.addEllipse(p.x - r, p.y - r, 2 * r, 2 * r, QPen(colour, self.px(1)), QBrush(colour))
        self._draw_constraint_labels()

        if self.preview is not None and self.pending:
            pv = QPen(SELECTED, self.px(1.2), Qt.DashLine)
            first = self.pending[0]
            fx, fy = (s.points[first].x, s.points[first].y) if isinstance(first, str) else first
            x, y = self.preview.x(), self.preview.y()
            if self.tool == "rectangle":
                sc.addRect(QRectF(QPointF(fx, fy), QPointF(x, y)).normalized(), pv)
            elif self.tool == "circle":
                rr = math.hypot(x - fx, y - fy)
                sc.addEllipse(fx - rr, fy - rr, 2 * rr, 2 * rr, pv)
            else:
                last = self.pending[-1]
                lx, ly = (s.points[last].x, s.points[last].y) if isinstance(last, str) else last
                sc.addLine(lx, ly, x, y, pv)

    def _draw_constraint_labels(self) -> None:
        s, sc = self.sketch, self.scene_
        glyph = {"horizontal": "H", "vertical": "V", "parallel": "∥", "perpendicular": "⊥", "equal": "=",
                 "tangent": "T", "coincident": "●", "concentric": "◎", "midpoint": "M", "fixed": "⚓",
                 "symmetric": "⇋", "point_on_line": "•", "point_on_circle": "•"}
        font = QFont("sans", 8)
        stacked: dict[tuple, int] = {}                  # labels already at an anchor
        for k in s.constraints:
            anchor = None
            ref = k.refs[0]
            if ref in s.lines:
                ln = s.lines[ref]
                a, b = s.points[ln.a], s.points[ln.b]
                anchor = ((a.x + b.x) / 2, (a.y + b.y) / 2)
            elif ref in s.points:
                anchor = (s.points[ref].x, s.points[ref].y)
            elif ref in s.circles or ref in s.arcs:
                e = s.circles.get(ref) or s.arcs[ref]
                c = s.points[e.center]
                anchor = (c.x + e.r * 0.7, c.y + e.r * 0.7)
            if anchor is None:
                continue
            if k.value is not None:
                text = f"{k.value:g}°" if k.type == "angle" else (f"Ø{k.value:g}" if k.type == "diameter" else f"{k.value:g}")
            else:
                text = glyph.get(k.type, k.type[:2])
            item = sc.addSimpleText(text, font)
            item.setBrush(CONFLICT if k.id in self.conflicts else QColor(200, 205, 212))
            item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
            key = (round(anchor[0], 3), round(anchor[1], 3))
            n = stacked.get(key, 0)
            stacked[key] = n + 1
            item.setPos(anchor[0] + self.px(5), anchor[1] + self.px(5 + 13 * n))   # one row per label

    def fit(self) -> None:
        s = self.sketch
        xs = [p.x for p in s.points.values()] or [0]
        ys = [p.y for p in s.points.values()] or [0]
        r = QRectF(min(xs) - 10, min(ys) - 10, max(xs) - min(xs) + 20, max(ys) - min(ys) + 20)
        self.fitInView(r, Qt.KeepAspectRatio)
        self.draw()


def _is_plain_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _seg_dist(p, a, b) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
    return math.hypot(p[0] - a[0] - ax * t, p[1] - a[1] - ay * t)
