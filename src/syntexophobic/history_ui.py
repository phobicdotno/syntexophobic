"""The History panel's subway map: every variant as a coloured line, every saved version a station.

Hover a station to see what that version changed; click it to pick it (the diff below and
"Restore this version" follow, as with the list)."""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .history import Station

# line colours, the first for main: the app's accent blue, then phobiCCCP's amber and red,
# then more that stay apart on the dark background
LINES = ["#40a0ff", "#f2a536", "#5cc977", "#e05050", "#b38cf0", "#4fd1c5", "#f07cb4", "#c8d26a"]
ROW = 30
LANE = 20
MARGIN = 14


def line_colour(i: int) -> QColor:
    return QColor(LINES[i % len(LINES)])


class SubwayMap(QWidget):
    picked = Signal(str)               # hash of the clicked version

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stations: list[Station] = []
        self.selected = ""
        self.describe: Callable[[str], str] | None = None      # hash -> what it changed
        self._tips: dict[str, str] = {}
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    # ---- data ---------------------------------------------------------------------------

    def set_stations(self, stations: list[Station]) -> None:
        self.stations = stations
        self._tips = {}
        self.updateGeometry()
        self.setMinimumHeight(len(stations) * ROW)
        self.update()

    def select(self, hash_: str) -> None:
        self.selected = hash_
        self.update()

    def lanes(self) -> int:
        return 1 + max((max([s.lane, *(max(a, b) for a, b, _ in s.top + s.bottom)]) for s in self.stations),
                       default=0)

    def sizeHint(self) -> QSize:
        return QSize(MARGIN + self.lanes() * LANE + 260, max(ROW, len(self.stations) * ROW))

    def row_at(self, y: float) -> int:
        r = int(y // ROW)
        return r if 0 <= r < len(self.stations) else -1

    @staticmethod
    def x(lane: int) -> float:
        return MARGIN + lane * LANE

    # ---- drawing ------------------------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        text_x = MARGIN + self.lanes() * LANE
        clip = self.visibleRegion().boundingRect()
        first, last = max(0, clip.top() // ROW - 1), min(len(self.stations), clip.bottom() // ROW + 2)
        for r in range(first, last):
            s = self.stations[r]
            if s.version.hash == self.selected:
                p.fillRect(QRectF(0, r * ROW, self.width(), ROW), pal.highlight().color().darker(220))
            y = r * ROW + ROW / 2
            for a, b, c in s.top:
                self._track(p, a, y - ROW / 2, b, y, c)
            for a, b, c in s.bottom:
                self._track(p, a, y, b, y + ROW / 2, c)
        for r in range(first, last):
            self._station(p, r, text_x)

    def _track(self, p: QPainter, a: int, y0: float, b: int, y1: float, colour: int):
        p.setPen(QPen(line_colour(colour), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(self.x(a), y0))
        if a == b:
            path.lineTo(self.x(b), y1)
        else:                                              # a smooth bend, like a subway map
            mid = (y0 + y1) / 2
            path.cubicTo(self.x(a), mid, self.x(b), mid, self.x(b), y1)
        p.drawPath(path)

    def _station(self, p: QPainter, r: int, text_x: float):
        s = self.stations[r]
        y = r * ROW + ROW / 2
        colour = line_colour(s.colour)
        here = s.version.refs.startswith("HEAD")
        p.setPen(QPen(colour, 3))
        p.setBrush(colour if here else self.palette().base().color())
        p.drawEllipse(QPointF(self.x(s.lane), y), 6.5, 6.5)
        if here:                                           # the version the files are at now
            p.setPen(QPen(self.palette().base().color(), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(self.x(s.lane), y), 3, 3)

        x = text_x
        font = QFont(self.font())
        fm = QFontMetrics(font)
        for ref in _variants(s.version.refs):              # variant names as line badges
            bold = QFont(font)
            bold.setBold(True)
            p.setFont(bold)
            w = QFontMetrics(bold).horizontalAdvance(ref) + 12
            rect = QRectF(x, y - 9, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(rect, 9, 9)
            p.setPen(QColor("#10141a"))
            p.drawText(rect, Qt.AlignCenter, ref)
            x += w + 6
        p.setFont(font)
        date = s.version.date
        date_w = fm.horizontalAdvance(date) + 8
        if self.width() - x - date_w < 180:               # a narrow panel: the note matters more
            date, date_w = "", 0
        room = self.width() - x - date_w - 6
        p.setPen(self.palette().text().color())
        p.drawText(QRectF(x, y - ROW / 2, max(0, room), ROW), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(s.version.message, Qt.ElideRight, max(0, int(room))))
        p.setPen(self.palette().placeholderText().color())
        p.drawText(QRectF(self.width() - date_w, y - ROW / 2, date_w - 6, ROW), Qt.AlignVCenter | Qt.AlignRight, date)

    # ---- mouse --------------------------------------------------------------------------

    def mousePressEvent(self, e):
        r = self.row_at(e.position().y())
        if r >= 0:
            self.select(self.stations[r].version.hash)
            self.picked.emit(self.selected)

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            r = self.row_at(e.pos().y())
            if r < 0:
                QToolTip.hideText()
                e.ignore()
                return True
            QToolTip.showText(e.globalPos(), self.tooltip(r), self, QRectF(0, r * ROW, self.width(), ROW).toRect())
            return True
        return super().event(e)

    def tooltip(self, row: int) -> str:
        """Rich text for a station: its note, when and where, and what it changed."""
        s = self.stations[row]
        v = s.version
        if v.hash not in self._tips:
            try:
                changes = self.describe(v.hash) if self.describe else ""
            except Exception as e:  # a tooltip must never take the window down
                changes = f"(could not read the changes: {e})"
            where = ", ".join(_variants(v.refs))
            head = f"<b>{html.escape(v.message)}</b><br>{v.date} · {v.short}" + (f" · {html.escape(where)}" if where else "")
            body = f"<pre style='margin-top:6px'>{html.escape(changes)}</pre>" if changes else ""
            self._tips[v.hash] = head + body
        return self._tips[v.hash]


def _variants(refs: str) -> list[str]:
    """Branch names from git's %D ("HEAD -> main, fast" -> ["main", "fast"])."""
    out = []
    for r in refs.split(","):
        r = r.strip().removeprefix("HEAD -> ")
        if r and r != "HEAD" and not r.startswith("tag: "):
            out.append(r)
    return out
