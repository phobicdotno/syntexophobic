"""Assistant panel: chat with Claude inside Syntexophobic, using Claude Code (your
subscription login, no API key). Claude gets only this program's MCP tools: no shell, no
file editing, no web. Each message runs `claude -p ... --resume <session>` so the
conversation carries on; its tool calls drive this window through the MCP server.

Pictures go with a message: pasted (Ctrl+V), dropped, picked from a file or taken with a
camera. Links in a message are read here (Claude has no web): the page's title, description
and main picture go with it. A picture or link of a router bit ends up in the tool library.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PySide6.QtCore import (QObject, QProcess, QProcessEnvironment, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from . import automation, faults

PROMPT = ("You are working inside Syntexophobic, a parametric CAD/CAM program for CNC routers, "
          "through its MCP tools. Start with status. Keep sketches fully constrained, check "
          "simulate before calling anything safe to run, and take a screenshot to show results. "
          "Answer briefly; the user sees the window update as you work. When the user shares a "
          "picture or link of a router bit or other cutter, add it to their tool library with "
          "library_add without asking (cutting diameter in mm, flutes, shape; put cutting length, "
          "shank, upcut/downcut and coating in the name). If they say they own another size than "
          "the one shown, add the size they own. Then say what you added. If a tool fails or "
          "misbehaves, or you cannot do what the user asked, call report_problem with what you "
          "tried and what went wrong (it files a GitHub issue for the developer), then tell the user.")

READABLE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}      # what Claude's Read tool shows as a picture
PICTURES = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.avif *.heic *.heif *.tif *.tiff)"
LINK = re.compile(r"https?://[^\s<>\"']+")
AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/130.0 Safari/537.36")


# ---- pictures --------------------------------------------------------------------------------

def _free(path: Path) -> Path:
    n = 1
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return out


def prepare_image(src: str | Path, folder: Path) -> Path:
    """A copy of the picture in `folder` that Claude can read: PNG, JPEG, GIF and WebP as they
    are, anything else (AVIF, HEIC, BMP, TIFF...) converted to PNG."""
    src = Path(src).expanduser()
    if not src.exists():
        raise ValueError(f"No file at {src}")
    folder.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in READABLE:
        if src.resolve().parent == folder.resolve():
            return src.resolve()
        dest = _free(folder / src.name)
        shutil.copy2(src, dest)
        return dest
    img = QImage(str(src))
    dest = _free(folder / (src.stem + ".png"))
    if not img.isNull():
        img.save(str(dest), "PNG")
        return dest
    try:                                     # Qt without an AVIF / HEIC plugin
        from PIL import Image
        with Image.open(src) as im:
            im.convert("RGBA" if "A" in im.getbands() else "RGB").save(dest, "PNG")
        return dest
    except Exception as e:
        raise ValueError(f"Cannot read the picture {src.name}: {e}") from e


def save_qimage(img: QImage, folder: Path, stem: str = "pasted") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = _free(folder / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.png")
    if not img.save(str(dest), "PNG"):
        raise ValueError("Could not save the picture")
    return dest


# ---- links -----------------------------------------------------------------------------------

def find_links(text: str) -> list[str]:
    out = []
    for m in LINK.findall(text):
        m = m.rstrip(".,;:!?)]}")
        if m not in out:
            out.append(m)
    return out


def short_url(url: str) -> str:
    """The address without its query (shops add long tracking strings)."""
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._in_ld = False
        self.ld: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.ld.append("")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self.ld[-1] += data


def page_info(text: str, url: str) -> dict:
    """Title, description and main picture of a web page (Open Graph, JSON-LD or <title>)."""
    p = _Page()
    try:
        p.feed(text)
    except Exception:  # broken markup: keep what was read
        pass
    m = p.meta
    title = m.get("og:title") or m.get("twitter:title") or " ".join(p.title.split())
    desc = m.get("og:description") or m.get("description") or m.get("twitter:description") or ""
    image = m.get("og:image") or m.get("og:image:url") or m.get("twitter:image") or ""
    for block in p.ld:                       # shops: a schema.org Product
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("@type") == "Product":
                title = title or d.get("name", "")
                desc = desc or d.get("description", "")
                img = d.get("image")
                image = image or (img[0] if isinstance(img, list) and img else img if isinstance(img, str) else "")
    return {"title": title.strip()[:300], "description": " ".join(desc.split())[:1500],
            "image": urljoin(url, image) if image else ""}


def _get(url: str, limit: int, timeout: float) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit), r.headers.get_content_charset() or "utf-8"


def fetch_link(url: str, folder: Path, timeout: float = 15.0) -> dict:
    """Read a page for Claude: title, description and its main picture (saved in `folder`).
    Never raises; a page that cannot be read gives an `error`."""
    info = {"url": url, "title": "", "description": "", "image": "", "picture": "", "error": ""}
    try:
        body, charset = _get(url, 3 << 20, timeout)
        info.update(page_info(body.decode(charset, errors="replace"), url))
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"[:200]
        return info
    if info["image"]:
        try:
            data, _ = _get(info["image"], 20 << 20, timeout)
            name = Path(urlsplit(info["image"]).path).name or "link-picture"
            tmp = _free(folder / ("link-" + name))
            folder.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            pic = prepare_image(tmp, folder)
            if pic.resolve() != tmp.resolve():      # (pic is resolved: compare like with like)
                tmp.unlink()
            info["picture"] = str(pic)
        except Exception:  # the text alone still helps
            pass
    return info


def _linked(text: str) -> str:
    """Message text as HTML, links shown short (without their tracking query)."""
    out, at = [], 0
    for m in LINK.finditer(text):
        url = m.group(0).rstrip(".,;:!?)]}")
        out.append(html.escape(text[at:m.start()]))
        out.append(f'<a style="color:#58a6ff" href="{html.escape(url)}">{html.escape(short_url(url))}</a>')
        at = m.start() + len(url)
    return "".join(out) + html.escape(text[at:])


def link_note(info: dict) -> str:
    """What Claude is told about a link."""
    if info["error"] or not (info["title"] or info["description"]):
        why = info["error"] or "no title or description on the page"
        return (f"- {short_url(info['url'])}: could not be read ({why}). Judge it from its address "
                "and from what the user wrote and attached.")
    out = f"- {short_url(info['url'])}: \"{info['title']}\""
    if info["description"]:
        out += f". Description: {info['description']}"
    if info["picture"]:
        out += f". Its picture: {info['picture']}"
    return out


def mcp_config() -> Path:
    """MCP config for the assistant: only this program's server."""
    cmd = automation.server_command()
    server = {"command": cmd[0], "args": cmd[1:]}
    cfg = {"mcpServers": {"syntexophobic": server}}
    path = automation.STATE / "assistant-mcp.json"
    automation.STATE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1))
    return path


def command(message: str, session_id: str | None, cwd: str | None) -> list[str]:
    args = ["-p", message, "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config()), "--strict-mcp-config",
            "--tools", "Read", "--allowedTools", "mcp__syntexophobic", "Read",
            "--append-system-prompt", PROMPT]
    if session_id:
        args += ["--resume", session_id]
    return args


def parse(line: str):
    """One stream-json line -> list of (kind, text) to show; kind: text, tool, done, error, session."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return []
    out = []
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        out.append(("session", ev.get("session_id", "")))
    elif t == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(("text", block["text"]))
            elif block.get("type") == "tool_use":
                name = block.get("name", "").replace("mcp__syntexophobic__", "")
                args = block.get("input", {})
                brief = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                out.append(("tool", f"{name}({brief[:120]})"))
    elif t == "user" and isinstance(ev.get("message", {}).get("content"), list):
        for block in ev["message"]["content"]:
            if block.get("type") == "tool_result" and block.get("is_error"):
                text = faults.result_text(block.get("content")).strip() or "(no error message)"
                out.append(("tool_error", text[:300]))
    elif t == "result":
        if ev.get("is_error"):
            out.append(("error", str(ev.get("result", "failed"))))
        out.append(("done", ev.get("session_id", "")))
    return out


class _Signals(QObject):
    done = Signal(object)


class _Report(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, _Signals()

    def run(self):
        try:
            filed = self.fn()
        except Exception as e:  # no login, offline, GitHub refused: say nothing
            faults.log.warning("fault report not filed: %s", e)
            filed = None
        self.signals.done.emit(filed)


class ChatInput(QPlainTextEdit):
    """The message box: pasting or dropping a picture attaches it; links stay as text."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    @staticmethod
    def _files(mime) -> list[str]:
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def canInsertFromMimeData(self, mime):
        return mime.hasImage() or bool(self._files(mime)) or super().canInsertFromMimeData(mime)

    def insertFromMimeData(self, mime):
        files = self._files(mime)
        if files:
            for f in files:
                self.panel.add_picture(f)
        elif mime.hasImage():
            self.panel.add_qimage(QImage(mime.imageData()))
        else:
            super().insertFromMimeData(mime)


class PhotoDialog:
    """Take a picture with a camera (webcam, phone as webcam). Returns a QImage or None."""

    @staticmethod
    def take(parent) -> QImage | None:
        from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox
        try:
            from PySide6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
            from PySide6.QtMultimediaWidgets import QVideoWidget
            cameras = QMediaDevices.videoInputs()
        except ImportError:
            cameras = []
        if not cameras:
            QMessageBox.information(parent, "Take photo", "No camera found. Paste a picture "
                                    "(Ctrl+V) or use Attach image… instead.")
            return None
        dlg = QDialog(parent)
        dlg.setWindowTitle("Take photo")
        col = QVBoxLayout(dlg)
        pick = QComboBox()
        pick.addItems([c.description() for c in cameras])
        col.addWidget(pick)
        video = QVideoWidget()
        video.setMinimumSize(480, 360)
        col.addWidget(video, 1)
        snap = QPushButton("Take photo")
        col.addWidget(snap)
        session = QMediaCaptureSession(dlg)
        capture = QImageCapture(dlg)
        session.setImageCapture(capture)
        session.setVideoOutput(video)
        got: list[QImage] = []
        state = {"camera": None}

        def use(i):
            if state["camera"] is not None:
                state["camera"].stop()
            cam = QCamera(cameras[i], dlg)
            session.setCamera(cam)
            cam.start()
            state["camera"] = cam
        pick.currentIndexChanged.connect(use)
        use(0)
        capture.imageCaptured.connect(lambda _id, img: (got.append(img), dlg.accept()))
        snap.clicked.connect(capture.capture)
        dlg.exec()
        if state["camera"] is not None:
            state["camera"].stop()
        return got[0] if got else None


class AssistantPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.session_id: str | None = None
        self.proc: QProcess | None = None
        self._buf = b""
        self.turn: faults.Turn | None = None
        self._stopped = False
        self.pool = QThreadPool(self)
        self.attached: list[str] = []
        self._fetch = None
        self._library_seen = self._library_keys()
        col = QVBoxLayout(self)
        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        col.addWidget(self.log, 1)
        self.thumbs = QWidget()
        self.thumb_row = QHBoxLayout(self.thumbs)
        self.thumb_row.setContentsMargins(0, 0, 0, 0)
        self.thumb_row.addStretch(1)
        self.thumbs.hide()
        col.addWidget(self.thumbs)
        self.input = ChatInput(self)
        self.input.setPlaceholderText("Ask Claude to design or machine something, e.g. "
                                      "\"Make a 100 × 60 × 12 plate with 4 corner holes and machine it\". "
                                      "Paste a picture or a link (Ctrl+V), e.g. of a bit for the tool library.")
        self.input.setMaximumHeight(90)
        col.addWidget(self.input)
        row = QHBoxLayout()
        self.b_send = QPushButton("Send (Ctrl+Enter)")
        self.b_send.clicked.connect(self.send)
        self.b_stop = QPushButton("Stop")
        self.b_stop.clicked.connect(self.stop)
        self.b_stop.setEnabled(False)
        self.b_new = QPushButton("New chat")
        self.b_new.clicked.connect(self.new_chat)
        for b in (self.b_send, self.b_stop, self.b_new):
            row.addWidget(b)
        col.addLayout(row)
        row2 = QHBoxLayout()
        self.b_attach = QPushButton("Attach image…")
        self.b_attach.clicked.connect(self.attach)
        self.b_photo = QPushButton("Take photo…")
        self.b_photo.clicked.connect(self.take_photo)
        for b in (self.b_attach, self.b_photo):
            row2.addWidget(b)
        col.addLayout(row2)
        self.note = QLabel()
        self.note.setWordWrap(True)
        col.addWidget(self.note)
        if shutil.which("claude") is None:
            self.note.setText("Claude Code is not installed (the 'claude' command). Install it and log in "
                              "with your subscription to chat here.")
            self.b_send.setEnabled(False)
        else:
            self._idle_note()
        self.input.installEventFilter(self)

    def _idle_note(self):
        self.note.setText("Uses your Claude Code login. Claude only gets Syntexophobic's tools here.")

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and ev.modifiers() & Qt.ControlModifier:
            self.send()
            return True
        return super().eventFilter(obj, ev)

    def _add(self, kind: str, text: str):
        if kind == "user":
            body = f'<p style="margin:8px 0 2px 0"><b>You:</b> {html.escape(text)}</p>'
        elif kind == "tool":
            body = f'<div style="color:#8a929c;font-family:monospace;font-size:11px">→ {html.escape(text)}</div>'
        elif kind == "tool_error":
            body = f'<div style="color:#e5534b;font-family:monospace;font-size:11px">✗ {html.escape(text)}</div>'
        elif kind == "error":
            body = f'<p style="color:#e5534b">{html.escape(text)}</p>'
        elif kind == "reported":
            body = f'<p style="color:#8a929c"><i>{text}</i></p>'          # our own HTML
        elif kind in ("html", "library"):
            body = text
        else:
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setMarkdown(text)                    # Claude answers in Markdown
            body = doc.toHtml()
        self.log.append(body)

    # ---- pictures ---------------------------------------------------------------------------

    def picture_folder(self) -> Path:
        """The project's images/ folder (history keeps them), or the cache with no project."""
        from . import automation
        project = self.window.project
        return project.folder / "images" if project is not None else automation.STATE / "chat"

    def add_picture(self, path: str):
        """Attach a picture file to the next message (copied, converted to PNG if needed)."""
        try:
            dest = prepare_image(path, self.picture_folder())
        except ValueError as e:
            self.note.setText(str(e))
            return
        self._attach(dest)

    def add_qimage(self, img: QImage, stem: str = "pasted"):
        if img.isNull():
            return
        self._attach(save_qimage(img, self.picture_folder(), stem))

    def _attach(self, path: Path):
        self.attached.append(str(path))
        b = QToolButton()
        b.setIcon(QIcon(QPixmap(str(path)).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        b.setIconSize(QSize(56, 56))
        b.setToolTip(f"{path.name}: click to remove")
        b.setAutoRaise(True)
        b.clicked.connect(lambda: self._detach(str(path), b))
        self.thumb_row.insertWidget(self.thumb_row.count() - 1, b)
        self.thumbs.show()
        n = len(self.attached)
        self.note.setText(f"{n} picture{'s' * (n > 1)} attached; {'they go' if n > 1 else 'it goes'} "
                          "with your next message.")

    def _detach(self, path: str, button):
        if path in self.attached:
            self.attached.remove(path)
        button.deleteLater()
        if not self.attached:
            self.thumbs.hide()
            self._idle_note()

    def _clear_attachments(self):
        self.attached = []
        while self.thumb_row.count() > 1:
            self.thumb_row.takeAt(0).widget().deleteLater()
        self.thumbs.hide()

    def attach(self):
        """Pick a picture; it goes to Claude with the next message."""
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self, "Picture for Claude", str(Path.home()), PICTURES)
        for path in paths:
            self.add_picture(path)

    def take_photo(self):
        img = PhotoDialog.take(self)
        if img is not None:
            self.add_qimage(img, "photo")

    # ---- sending ----------------------------------------------------------------------------

    def send(self):
        text = self.input.toPlainText().strip()
        if (not text and not self.attached) or self.proc is not None or self._fetch is not None:
            return
        self.turn = faults.Turn(text)
        self._stopped = False
        pictures, self.attached = self.attached, []
        self._clear_attachments()
        self.input.clear()
        shown = _linked(text) if text else "<i>(picture)</i>"
        imgs = "".join(f'<img src="{QUrl.fromLocalFile(p).toString()}" height="96"> ' for p in pictures)
        self._add("html", f'<p style="margin:8px 0 2px 0"><b>You:</b> {shown}</p>' + (f"<p>{imgs}</p>" if imgs else ""))
        links = find_links(text)[:3]
        if not links:
            self._launch(text, pictures, [])
            return
        from concurrent.futures import ThreadPoolExecutor
        self.note.setText("Reading " + ", ".join(urlsplit(u).netloc for u in links) + "…")
        self.b_send.setEnabled(False)
        pool = ThreadPoolExecutor(max_workers=len(links))
        folder = self.picture_folder()
        self._fetch = [pool.submit(fetch_link, u, folder) for u in links]
        pool.shutdown(wait=False)

        def poll():
            if not all(f.done() for f in self._fetch):
                QTimer.singleShot(100, poll)
                return
            infos = [f.result() for f in self._fetch]
            self._fetch = None
            for info in infos:
                if info["error"] or not info["title"]:
                    self._add("tool", f"{urlsplit(info['url']).netloc} could not be read "
                                      f"({info['error'] or 'empty page'}); Claude gets the address only")
                else:
                    self._add("tool", f"read {urlsplit(info['url']).netloc}: {info['title'][:100]}")
            self._launch(text, pictures, infos)
        QTimer.singleShot(50, poll)

    def _launch(self, text: str, pictures: list[str], links: list[dict]):
        message = text or "Here is a picture."
        if pictures:
            message += ("\n\nAttached picture(s), look at them with the Read tool: " + ", ".join(pictures) +
                        ". If one shows a router bit or other cutter, add it to the tool library with "
                        "library_add. Otherwise use trace_image for outlines or add_relief for a height "
                        "relief, or model the shape with sketches and features, as fits the request.")
        if links:
            message += ("\n\nLinks in this message, read by Syntexophobic (you have no web access):\n" +
                        "\n".join(link_note(i) for i in links))
            pics = [i["picture"] for i in links if i["picture"]]
            if pics:
                message += "\nLook at the linked pictures with the Read tool too."
            message += ("\nIf a link is a product page for a router bit or other cutter, add it to the "
                        "tool library with library_add.")
        self.proc = QProcess(self)
        cwd = str(self.window.project.folder) if self.window.project else str(Path.home())
        self.proc.setWorkingDirectory(cwd)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("SYNTEXOPHOBIC_PANEL", "1")            # report_problem: the panel files the report
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        self._buf = b""
        self._library_seen = self._library_keys()
        self.proc.start("claude", command(message, self.session_id, cwd))
        self.b_send.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.note.setText("Claude is working…")

    # ---- tool library -----------------------------------------------------------------------

    @staticmethod
    def _library_keys() -> list[tuple]:
        from . import library
        try:
            return [(t.name, t.diameter, t.flutes, t.shape, t.angle) for t in library.load()]
        except Exception:
            return []

    def library_changed(self, quiet: bool = False):
        """Claude ran library_add: show what the library gained. Called by the window, and
        quietly when Claude finishes (its tools ran without the window if they could not
        reach it)."""
        now = self._library_keys()
        new = [k for k in now if k not in self._library_seen]
        self._library_seen = now
        if not new:
            if not quiet:
                self._add("library", '<p style="color:#8a929c">Tool library: already has this bit.</p>')
            return
        from . import library
        for name, d, flutes, shape, angle in new:
            what = f"Ø{d:g} mm, {flutes} flute{'s' * (flutes > 1)}, {shape}" + (f" {angle:g}°" if shape == "vbit" else "")
            self._add("library",
                      '<table cellpadding="6" style="margin:4px 0; border:1px solid #3f8f5a; '
                      'background-color:#1e3a2a"><tr><td>'
                      f'<b style="color:#7ee2a0">✓ Added to your tool library</b><br>'
                      f'<b>{html.escape(name)}</b><br>{html.escape(what)}<br>'
                      f'<span style="color:#8a929c;font-size:11px">{html.escape(str(library.path()))}</span>'
                      '</td></tr></table>')
        self.note.setText(f"Tool library: {len(now)} tools. Tools tab > From library… puts one in a job.")

    # ---- Claude's output --------------------------------------------------------------------

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            line = line.decode(errors="replace")
            if self.turn is not None:
                self.turn.feed_line(line)
            for kind, text in parse(line):
                if kind == "session" and text:
                    self.session_id = text
                elif kind == "done":
                    self.session_id = text or self.session_id
                else:
                    self._add(kind, text)

    def _finished(self, code, status):
        err = bytes(self.proc.readAllStandardError()).decode(errors="replace").strip()
        if code != 0 and err:
            self._add("error", err[-800:])
            if self.turn is not None and not self.turn.run_error and not self._stopped:
                self.turn.run_error = err[-2000:]
        self.proc = None
        if self.turn is not None and not self._stopped:
            self.report(self.turn)
        self.turn = None
        self.b_send.setEnabled(True)
        self.b_stop.setEnabled(False)
        self._idle_note()
        self.library_changed(quiet=True)

    def _error(self, error):
        """`claude` did not start (no finished signal comes then): say so and free the panel."""
        if error != QProcess.ProcessError.FailedToStart or self.proc is None:
            return
        self._add("error", f"Could not start claude: {self.proc.errorString()}")
        self.proc, self.turn = None, None
        self.b_send.setEnabled(True)
        self.b_stop.setEnabled(False)
        self._idle_note()

    def stop(self):
        if self.proc is not None:
            self._stopped = True                      # stopped by the user: not a fault
            self.proc.kill()

    def report(self, turn: faults.Turn):
        """A turn that went wrong is filed on GitHub in the background; the chat gets the link.
        Without a GitHub login or network nothing is shown."""
        why = turn.fault()
        if not why or os.environ.get("SYNTEXOPHOBIC_NO_FAULT_REPORTS"):
            return
        faults.log.warning("assistant fault: %s; request %r", why, turn.request[:200])
        shot = None
        try:
            shot = Path(self.window.screenshot(str(automation.STATE / "fault-screenshot.png")))
        except Exception as e:  # a missing picture must not stop the report
            faults.log.warning("fault screenshot failed: %s", e)
        project = str(self.window.project.folder) if getattr(self.window, "project", None) else ""
        job = _Report(lambda: faults.report(turn, shot, project))
        job.signals.done.connect(self._reported)
        job.setAutoDelete(False)
        self._job = job                                   # kept alive until the next report
        self.pool.start(job)

    def _reported(self, filed):
        if filed is None:
            return
        what = "Reported this to GitHub" if filed.new else "Added this to the GitHub issue"
        self._add("reported", f'{what}: <a href="{html.escape(filed.url)}">#{filed.number}</a>')

    def new_chat(self):
        self.stop()
        self.session_id = None
        self.log.clear()
