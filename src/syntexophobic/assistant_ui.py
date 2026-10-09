"""Assistant panel: chat with Claude inside Syntexophobic, using Claude Code (your
subscription login, no API key). Claude gets only this program's MCP tools: no shell, no
file editing, no web. Each message runs `claude -p ... --resume <session>` so the
conversation carries on; its tool calls drive this window through the MCP server.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from . import automation

PROMPT = ("You are working inside Syntexophobic, a parametric CAD/CAM program for CNC routers, "
          "through its MCP tools. Start with status. Keep sketches fully constrained, check "
          "simulate before calling anything safe to run, and take a screenshot to show results. "
          "Answer briefly; the user sees the window update as you work.")


def mcp_config() -> Path:
    """MCP config for the assistant: only this program's server."""
    root = Path(__file__).resolve().parents[2]
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    cfg = {"mcpServers": {"syntexophobic": {"command": uv,
                                             "args": ["run", "--project", str(root), "syntexophobic", "mcp"]}}}
    path = automation.STATE / "assistant-mcp.json"
    automation.STATE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1))
    return path


def command(message: str, session_id: str | None, cwd: str | None) -> list[str]:
    args = ["-p", message, "--output-format", "stream-json", "--verbose",
            "--mcp-config", str(mcp_config()), "--strict-mcp-config",
            "--tools", "", "--allowedTools", "mcp__syntexophobic",
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
    elif t == "result":
        if ev.get("is_error"):
            out.append(("error", str(ev.get("result", "failed"))))
        out.append(("done", ev.get("session_id", "")))
    return out


class AssistantPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.session_id: str | None = None
        self.proc: QProcess | None = None
        self._buf = b""
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
        self.b_new = QPushButton("New chat")
        self.b_new.clicked.connect(self.new_chat)
        for b in (self.b_send, self.b_stop, self.b_new):
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
        elif kind == "error":
            body = f'<p style="color:#e5534b">{html.escape(text)}</p>'
        else:
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setMarkdown(text)                    # Claude answers in Markdown
            body = doc.toHtml()
        self.log.append(body)

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or self.proc is not None:
            return
        self.input.clear()
        self._add("user", text)
        self.proc = QProcess(self)
        cwd = str(self.window.project.folder) if self.window.project else str(Path.home())
        self.proc.setWorkingDirectory(cwd)
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
            for kind, text in parse(line.decode(errors="replace")):
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
        self.proc = None
        self.b_send.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.note.setText("Uses your Claude Code login. Claude only gets Syntexophobic's tools here.")

    def stop(self):
        if self.proc is not None:
            self.proc.kill()

    def new_chat(self):
        self.stop()
        self.session_id = None
        self.log.clear()
