"""Assistant panel: chat with Claude inside Syntexophobic, using Claude Code (your
subscription login, no API key). Claude gets only this program's MCP tools: no shell, no
file editing, no web. Each message runs `claude -p ... --resume <session>` so the
conversation carries on; its tool calls drive this window through the MCP server.
"""

from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from . import automation, faults

PROMPT = ("You are working inside Syntexophobic, a parametric CAD/CAM program for CNC routers, "
          "through its MCP tools. Start with status. Keep sketches fully constrained, check "
          "simulate before calling anything safe to run, and take a screenshot to show results. "
          "Answer briefly; the user sees the window update as you work. If a tool fails or "
          "misbehaves, or you cannot do what the user asked, call report_problem with what you "
          "tried and what went wrong (it files a GitHub issue for the developer), then tell the user.")


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
        col = QVBoxLayout(self)
        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        col.addWidget(self.log, 1)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask Claude to design or machine something, e.g. "
                                      "\"Make a 100 × 60 × 12 plate with 4 corner holes and machine it\"")
        self.input.setMaximumHeight(90)
        col.addWidget(self.input)
        row = QHBoxLayout()
        self.b_send = QPushButton("Send (Ctrl+Enter)")
        self.b_send.clicked.connect(self.send)
        self.b_stop = QPushButton("Stop")
        self.b_stop.clicked.connect(self.stop)
        self.b_stop.setEnabled(False)
        self.b_attach = QPushButton("Attach image…")
        self.b_attach.clicked.connect(self.attach)
        self.attached: list[str] = []
        self.b_new = QPushButton("New chat")
        self.b_new.clicked.connect(self.new_chat)
        for b in (self.b_send, self.b_attach, self.b_stop, self.b_new):
            row.addWidget(b)
        col.addLayout(row)
        self.note = QLabel()
        self.note.setWordWrap(True)
        col.addWidget(self.note)
        if shutil.which("claude") is None:
            self.note.setText("Claude Code is not installed (the 'claude' command). Install it and log in "
                              "with your subscription to chat here.")
            self.b_send.setEnabled(False)
        else:
            self.note.setText("Uses your Claude Code login. Claude only gets Syntexophobic's tools here.")
        self.input.installEventFilter(self)

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
        else:
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setMarkdown(text)                    # Claude answers in Markdown
            body = doc.toHtml()
        self.log.append(body)

    def attach(self):
        """Pick a picture; it is copied into the project's images/ folder and handed to
        Claude with the next message."""
        import shutil
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Picture for Claude", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        project = self.window.project
        if project is not None:
            dest = project.folder / "images" / Path(path).name
            dest.parent.mkdir(exist_ok=True)
            if Path(path).resolve() != dest.resolve():
                shutil.copy2(path, dest)
            path = str(dest)
        self.attached.append(path)
        self.note.setText(f"Attached {Path(path).name}; it goes with your next message.")
        if not self.input.toPlainText().strip():
            self.input.setPlainText("Design a 3D figure from this picture.")

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.proc is not None:
            return
        self.turn = faults.Turn(text)
        self._stopped = False
        if self.attached:
            text += "\n\nAttached image(s), look at them with the Read tool: " + ", ".join(self.attached) + \
                    ". Use trace_image for outlines or add_relief for a height relief, or model the shape " \
                    "with sketches and features, as fits the request."
            self.attached = []
        self.input.clear()
        self._add("user", text)
        self.proc = QProcess(self)
        cwd = str(self.window.project.folder) if self.window.project else str(Path.home())
        self.proc.setWorkingDirectory(cwd)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("SYNTEXOPHOBIC_PANEL", "1")            # report_problem: the panel files the report
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self._buf = b""
        self.proc.start("claude", command(text, self.session_id, cwd))
        self.b_send.setEnabled(False)
        self.b_stop.setEnabled(True)
        self.note.setText("Claude is working…")

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
        self.note.setText("Uses your Claude Code login. Claude only gets Syntexophobic's tools here.")

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
