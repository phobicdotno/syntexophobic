"""The program's icon on the desktop. GNOME (and most docks) show a window's icon only from the
.desktop entry whose name or StartupWMClass matches the window's class, never from the window
itself, so the AppImage installs that entry and its icon in the user's ~/.local/share when it
starts: the dock, the window switcher and the app grid then show the Syntexophobic icon with
no manual steps. Rewritten when the AppImage has moved; left alone when someone wrote their own.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

APP_ID = "syntexophobic"          # .desktop name, icon name, and QGuiApplication.desktopFileName
WM_CLASS = "Syntexophobic"        # the window's class: Qt takes it from the application name
SVG = Path(__file__).with_name("assets") / "syntexophobic.svg"
MARK = "X-Syntexophobic-AppImage"  # our entries carry it; an entry without it is someone else's
PNG_SIZES = (48, 128, 256)


def entry(exec_path: str) -> str:
    # Exec quoting, then the string-level escaping every value gets (desktop entry spec)
    quoted = '"' + re.sub(r'(["`$\\])', r'\\\1', exec_path).replace("%", "%%") + '"'
    quoted, tryexec = quoted.replace("\\", "\\\\"), exec_path.replace("\\", "\\\\")
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Syntexophobic\n"
            "Comment=Parametric CAD and CAM for CNC routers\n"
            f"Exec={quoted} %f\n"
            f"TryExec={tryexec}\n"
            f"Icon={APP_ID}\n"
            f"StartupWMClass={WM_CLASS}\n"
            "Categories=Graphics;Engineering;\n"
            "Terminal=false\n"
            f"{MARK}=true\n")


def png(size: int) -> bytes:
    """The icon drawn at size x size (needs a QGuiApplication)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    QSvgRenderer(str(SVG)).render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def _write(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def install(exec_path: str, data_home: Path | None = None) -> bool:
    """Write the menu entry and icons under data_home (~/.local/share); True when anything changed."""
    data_home = data_home or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    desktop = data_home / "applications" / f"{APP_ID}.desktop"
    if desktop.exists() and MARK not in desktop.read_text(errors="replace"):
        return False                                   # hand-made entry: theirs to keep
    icons = data_home / "icons" / "hicolor"
    changed = _write(icons / "scalable/apps" / f"{APP_ID}.svg", SVG.read_bytes())
    for size in PNG_SIZES:
        changed |= _write(icons / f"{size}x{size}/apps" / f"{APP_ID}.png", png(size))
    changed |= _write(desktop, entry(exec_path).encode())
    if changed:
        _refresh(icons, desktop.parent)
    return changed


def _refresh(icons: Path, applications: Path):
    """A stale icon-theme.cache hides new icons: rebuild it, or drop it so the folder is scanned."""
    os.utime(icons)                                    # the mtime tells GTK the theme changed
    cache = icons / "icon-theme.cache"
    if cache.exists():
        tool = shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache")
        ok = tool and subprocess.run([tool, "-f", "-t", "-q", str(icons)], capture_output=True).returncode == 0
        if not ok:
            cache.unlink(missing_ok=True)
    if tool := shutil.which("update-desktop-database"):
        subprocess.run([tool, "-q", str(applications)], capture_output=True)


def integrate():
    """At start-up: when running as an AppImage on Linux, make sure its menu entry and icon exist."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage or not os.path.isfile(appimage) or os.environ.get("SYNTEXOPHOBIC_NO_DESKTOP_ENTRY"):
        return
    try:
        install(appimage)
    except OSError:
        pass                                           # read-only home and the like: just no icon
