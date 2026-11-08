"""Draws the Windows installer's pictures, in the program's own colours, into a folder:

  splash.bmp  493 x 312, the first page of the installer (the WiX dialog picture's size): the
              icon and the name on the dark background of the 3D view, the text the installer
              writes over it on the right
  banner.bmp  493 x 58, across the top of the progress page, light, as Windows draws its text
              over it in black
  dialog.bmp  493 x 312, behind the pages that say setup failed or was cancelled: the icon in a
              dark band on the left, white where Windows writes in black

Run with the build's Python, which has PySide6:  python installer_art.py <folder>
"""

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer

SVG = Path(__file__).resolve().parents[2] / "src" / "syntexophobic" / "assets" / "syntexophobic.svg"
AMBER = QColor("#f2a536")
INK = QColor("#d8dce4")


def _canvas(w: int, h: int) -> tuple[QImage, QPainter]:
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    return img, p


def _font(px: int, bold: bool = False) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def splash() -> QImage:
    img, p = _canvas(493, 312)
    bg = QLinearGradient(0, 0, 0, 312)
    bg.setColorAt(0, QColor("#22252b"))
    bg.setColorAt(1, QColor("#15171c"))
    p.fillRect(img.rect(), bg)
    # the 20-unit grid of the sketcher, faint, fading out to the right
    for x in range(0, 493, 20):
        c = QColor(INK)
        c.setAlpha(max(0, 18 - x // 30))
        p.setPen(QPen(c, 1))
        p.drawLine(x, 0, x, 312)
    for y in range(0, 312, 20):
        c = QColor(INK)
        c.setAlpha(10)
        p.setPen(QPen(c, 1))
        p.drawLine(0, y, 200, y)
    # a toolpath-like amber arc behind the icon, with its clicked points
    p.setPen(QPen(AMBER, 1.6))
    p.drawArc(QRectF(20, 46, 180, 180), 30 * 16, 250 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#9fc8f2"))
    for pt in (QPointF(187.9, 91), QPointF(64.7, 220.4)):
        p.drawEllipse(pt, 3.2, 3.2)
    QSvgRenderer(str(SVG)).render(p, QRectF(42, 68, 136, 136))
    p.setPen(QColor("#ffffff"))
    p.setFont(_font(30, bold=True))
    p.drawText(QRectF(222, 40, 260, 46), Qt.AlignLeft | Qt.AlignVCenter, "Syntexophobic")
    p.setPen(QColor("#9aa3b2"))
    p.setFont(_font(14))
    p.drawText(QRectF(224, 84, 260, 22), Qt.AlignLeft | Qt.AlignVCenter, "CAD with git-style history")
    p.setPen(QPen(AMBER, 2))
    p.drawLine(224, 114, 284, 114)
    p.end()
    return img


def banner() -> QImage:
    img, p = _canvas(493, 58)
    p.fillRect(img.rect(), QColor("#f7f8fa"))
    p.setPen(QPen(AMBER, 2))
    p.drawLine(0, 57, 493, 57)
    QSvgRenderer(str(SVG)).render(p, QRectF(435, 6, 46, 46))
    p.end()
    return img


def dialog() -> QImage:
    img, p = _canvas(493, 312)
    p.fillRect(img.rect(), QColor("#ffffff"))
    bg = QLinearGradient(0, 0, 0, 312)
    bg.setColorAt(0, QColor("#22252b"))
    bg.setColorAt(1, QColor("#15171c"))
    p.fillRect(QRectF(0, 0, 164, 312), bg)
    p.setPen(QPen(AMBER, 2))
    p.drawLine(164, 0, 164, 312)
    QSvgRenderer(str(SVG)).render(p, QRectF(30, 40, 104, 104))
    p.end()
    return img


def main(folder: str):
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (fonts need it)
    for name, img in (("splash.bmp", splash()), ("banner.bmp", banner()), ("dialog.bmp", dialog())):
        if not img.save(str(out / name), "BMP"):
            sys.exit(f"could not write {out / name}")


if __name__ == "__main__":
    main(sys.argv[1])
