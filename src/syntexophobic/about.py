"""Help > About: logo, version and build, and a "What's new" line per release, read from
CHANGELOG.md, as in phobiCCCP. The changelog is the single place release notes live."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HEAD = re.compile(r"^## v(\S+)\s*(?:\(build (\d+)\))?\s*[—-]*\s*(\S*)")
SECTION = re.compile(r"^\*\*(.+?)\*\*\s*$")


@dataclass
class Release:
    version: str
    build: int = 0
    date: str = ""
    areas: list[str] = field(default_factory=list)


def changelog_text() -> str:
    """CHANGELOG.md: from the source tree, or the copy packaged with the program."""
    here = Path(__file__).resolve()
    for p in (here.parents[2] / "CHANGELOG.md", here.parent / "assets" / "CHANGELOG.md"):
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def summarize(markdown: str) -> list[Release]:
    """One entry per '## vX.Y.Z (build N) — date' heading with the bold section titles of
    that release (Testing left out). A release without sections is summed up by its first
    item: "Trim (X): click …" gives "Trim (X)", otherwise its first sentence."""
    out: list[Release] = []
    first: str = ""
    in_first = False

    def close():
        nonlocal first
        if out and not out[-1].areas and first:
            t = re.sub(r"[*`]", "", " ".join(first.split()))
            colon = t.find(": ")
            if 0 < colon <= 40:
                t = t[:colon]
            else:
                dot = t.find(". ")
                if dot > 0:
                    t = t[:dot]
                if len(t) > 60:
                    t = t[:t.rfind(" ", 0, 58)] + " …"
            out[-1].areas.append(t)
        first = ""

    for line in markdown.splitlines():
        if line.startswith("- ") and out:
            in_first = not first
            if in_first:
                first = line[2:].strip()
        elif in_first and line.startswith("  "):
            first += " " + line.strip()
        else:
            in_first = False
        h = HEAD.match(line)
        if h:
            close()
            out.append(Release(h.group(1), int(h.group(2) or 0), h.group(3)))
            continue
        s = SECTION.match(line.strip())
        if s and out:
            title = s.group(1).strip()
            if title.lower() != "testing" and title not in out[-1].areas:
                out[-1].areas.append(title)
    close()
    return out


def version() -> str:
    try:
        from importlib.metadata import version as v
        return v("syntexophobic")
    except Exception:  # running from a source tree that is not installed
        import tomllib
        return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"]["version"]


def build() -> int:
    return next((r.build for r in summarize(changelog_text()) if r.version == version()), 0)


SOFTWARE_GL = ("llvmpipe", "softpipe", "swiftshader", "gdi generic", "microsoft basic render",
               "apple software renderer")


def graphics(report: str) -> str:
    """The graphics line from VTK's ReportCapabilities(): "AMD Radeon(TM) 860M Graphics
    (OpenGL 4.6 …)", with a warning when the 3D view is drawn in software on the processor.
    Empty when the report has no renderer (the view has not been drawn yet)."""
    found = dict(m.groups() for m in re.finditer(r"^OpenGL (renderer|version) string:\s*(.+?)\s*$", report, re.M))
    name = found.get("renderer")
    if not name:
        return ""
    gl = found.get("version", "").split(" ")[0]
    line = f"{name}{f' (OpenGL {gl})' if gl else ''}"
    if any(s in name.lower() for s in SOFTWARE_GL):
        line += " — software rendering on the processor, not the graphics card"
    return line


def dialog(parent=None):
    import html
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle("About Syntexophobic")
    dlg.resize(560, 600)
    lay = QVBoxLayout(dlg)
    logo = QLabel()
    logo.setAlignment(Qt.AlignCenter)
    logo.setPixmap(QIcon(str(Path(__file__).with_name("assets") / "syntexophobic.svg")).pixmap(96, 96))
    lay.addWidget(logo)
    b = build()
    gpu = ""
    viewer = getattr(parent, "viewer", None)
    if viewer is not None:
        try:
            gpu = graphics(viewer.GetRenderWindow().ReportCapabilities())
        except Exception:  # no OpenGL context: leave the line out
            pass
    gpu_line = f"<span style='color:#8a919c'>3D view: {html.escape(gpu)}</span><br>" if gpu else ""
    title = QLabel(
        "<div align=center><span style='font-size:20pt; font-weight:bold'>Syntexophobic</span><br>"
        "<i>σύντηξις: melting together, fusion</i><br>"
        f"Version {version()}{f' (build {b})' if b else ''}<br>"
        f"{gpu_line}<br>"
        "Parametric CAD and CAM for CNC routers (GRBL / Carbide Motion), with a git-style history"
        " and Claude through MCP<br>"
        "<a href='https://github.com/phobicdotno/syntexophobic'>github.com/phobicdotno/syntexophobic</a>"
        "</div>")
    title.setTextFormat(Qt.RichText)
    title.setOpenExternalLinks(True)
    title.setWordWrap(True)
    lay.addWidget(title)
    rows = "".join(
        f"<tr><td valign=top style='white-space:nowrap'><b>v{html.escape(r.version)}</b></td>"
        f"<td valign=top style='white-space:nowrap; color:#8a919c'>{html.escape(r.date)}</td>"
        f"<td valign=top>{html.escape(' · '.join(r.areas) or '—')}</td></tr>"
        for r in summarize(changelog_text()))
    log = QTextBrowser()
    log.setOpenExternalLinks(True)
    log.setHtml(f"<h3>What's new</h3><table cellspacing=0 cellpadding=3>{rows}</table>")
    lay.addWidget(log, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    return dlg
