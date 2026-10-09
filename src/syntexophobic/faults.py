"""Fault reports: when Claude in the Assistant panel hits a failing tool, gives up on what
the user asked, or calls report_problem, the program files a GitHub issue on its own
repository saying exactly what happened: version and build, OS, the user's request, every
tool call with its arguments and result, Claude's answer, the log tail and a screenshot.

The same fault (same signature: the failing tools and their errors with paths and numbers
taken out) is added as a comment to the open issue that has it instead of a new issue. The
repository is private, so this needs a GitHub login: SYNTEXOPHOBIC_GITHUB_TOKEN, GH_TOKEN
or GITHUB_TOKEN, else `gh auth token`, else the token git stores for github.com, else the
`gh` command itself. Without one, or offline, nothing is reported and nothing is said.
SYNTEXOPHOBIC_NO_FAULT_REPORTS=1 turns reporting off."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .automation import STATE
from .update import token  # the same GitHub login the update check uses

REPO = "phobicdotno/syntexophobic"
API = f"https://api.github.com/repos/{REPO}"
LABEL = "fault-report"
SHOTS_BRANCH = "fault-screenshots"        # screenshots are committed here (issues take no uploads)
LOG = STATE / "syntexophobic.log"
MARK = "<!-- fault-signature: {} -->"

log = logging.getLogger("syntexophobic")

# Claude's answer when it could not do what was asked
GAVE_UP = re.compile(r"\b(I )?(couldn['’]t|could not|can['’]t|cannot|was unable to|wasn['’]t able to|"
                     r"am unable to|won['’]t)\b.{0,80}\b(make|create|build|do|open|finish|complete|"
                     r"generate|save|export|get|draw|add)", re.I)


class FaultError(Exception):
    pass


def setup_logging() -> Path:
    """Everything the program logs goes to ~/.cache/syntexophobic/syntexophobic.log (the
    window and the MCP server both write to it); a report includes its last lines."""
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        STATE.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(LOG, maxBytes=1 << 20, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return LOG


def log_tail(lines: int = 60, path: Path | None = None) -> str:
    path = path or LOG
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


# ---- what happened --------------------------------------------------------------------------

@dataclass
class Call:
    name: str
    args: dict
    result: str = ""
    error: bool = False
    done: bool = False


@dataclass
class Turn:
    """One message to Claude and everything it did, from Claude Code's stream-json output."""
    request: str
    calls: dict[str, Call] = field(default_factory=dict)       # tool_use id -> call, in order
    texts: list[str] = field(default_factory=list)
    reported: list[str] = field(default_factory=list)          # report_problem summaries
    run_error: str = ""
    session_id: str = ""

    def feed_line(self, line: str):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(ev, dict):
            self.feed(ev)

    def feed(self, ev: dict):
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self.session_id = ev.get("session_id", "") or self.session_id
        elif t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    self.texts.append(b["text"])
                elif b.get("type") == "tool_use":
                    name = b.get("name", "").replace("mcp__syntexophobic__", "")
                    self.calls[b.get("id", str(len(self.calls)))] = Call(name, b.get("input") or {})
                    if name == "report_problem":
                        args = b.get("input") or {}
                        self.reported.append(" ".join(str(args.get(k, "")) for k in ("summary", "details")).strip())
        elif t == "user":
            content = ev.get("message", {}).get("content", [])
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in self.calls:
                    c = self.calls[b["tool_use_id"]]
                    c.result, c.error, c.done = result_text(b.get("content")), bool(b.get("is_error")), True
        elif t == "result" and ev.get("is_error"):
            self.run_error = str(ev.get("result") or ev.get("subtype") or "failed")

    def failed_calls(self) -> list[Call]:
        """Calls that returned an error, or an empty result (a tool that says nothing failed)."""
        return [c for c in self.calls.values() if c.done and (c.error or not c.result.strip())]

    def answer(self) -> str:
        return self.texts[-1] if self.texts else ""

    def fault(self) -> str:
        """Why this turn is a fault, or "" when it is not."""
        if self.reported:
            return f"Claude reported a problem: {self.reported[0]}"
        bad = self.failed_calls()
        if bad:
            names = sorted({c.name for c in bad})
            return f"{', '.join(names)} failed" + (f" ({len(bad)} times)" if len(bad) > 1 else "")
        if self.run_error:
            return f"Claude Code stopped with an error: {self.run_error[:200]}"
        if GAVE_UP.search(self.answer()):
            return "Claude could not do what was asked"
        return ""


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]"
                         for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def _normal(text: str) -> str:
    """An error with the parts that change between runs taken out: paths, numbers, quotes."""
    text = re.sub(r"(~|\.{0,2})?/[\w./~-]+", "<path>", text)
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "<s>", text)
    text = re.sub(r"\d+(\.\d+)?", "<n>", text)
    return " ".join(text.split())[:200]


def signature(turn: Turn) -> str:
    bad = turn.failed_calls()
    if bad:
        key = sorted({f"{c.name}: {_normal(c.result.splitlines()[0] if c.result.strip() else '(no output)')}"
                      for c in bad})
    elif turn.reported:
        key = ["report: " + _normal(turn.reported[0])[:80].lower()]
    elif turn.run_error:
        key = ["run: " + _normal(turn.run_error)]
    else:
        key = ["gave up after: " + " ".join(dict.fromkeys(c.name for c in turn.calls.values()))]
    return hashlib.sha1("\n".join(key).encode()).hexdigest()[:12]


# ---- the report -----------------------------------------------------------------------------

def environment() -> dict[str, str]:
    from .about import build, version
    env = {"Version": f"{version()} (build {build()})" if build() else version()}
    env["Running as"] = "AppImage " + os.environ["APPIMAGE"] if os.environ.get("APPIMAGE") else "source / installed"
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        try:
            env["Commit"] = subprocess.run(["git", "-C", str(root), "describe", "--always", "--dirty", "--tags"],
                                           capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        osr = platform.freedesktop_os_release()
        env["OS"] = f"{osr.get('PRETTY_NAME', osr.get('NAME', ''))}, {platform.machine()}"
    except OSError:
        env["OS"] = f"{platform.system()} {platform.release()}, {platform.machine()}"
    env["Kernel"] = platform.release()
    env["Python"] = sys.version.split()[0]
    try:
        import PySide6
        env["Qt"] = f"PySide6 {PySide6.__version__}, platform {os.environ.get('QT_QPA_PLATFORM', 'default')}"
    except ImportError:
        pass
    env["Session"] = os.environ.get("XDG_SESSION_TYPE", "")
    return {k: v for k, v in env.items() if v}


def _clip(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + f"\n… ({len(text) - n} more characters)"


def _fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text}\n{ticks}"


def title(turn: Turn) -> str:
    return f"[fault] {turn.fault()} — “{_clip(' '.join(turn.request.split()), 60)}”"[:200]


def body(turn: Turn, sig: str, env: dict[str, str] | None = None, logs: str = "",
         screenshot_url: str = "", project: str = "") -> str:
    env = environment() if env is None else env
    rows = "\n".join(f"| {k} | {v} |" for k, v in env.items())
    steps = []
    for i, c in enumerate(turn.calls.values(), 1):
        args = json.dumps(c.args, ensure_ascii=False)
        res = "error" if c.error else ("no output" if c.done and not c.result.strip() else
                                       ("ok" if c.done else "no result"))
        steps.append(f"{i}. `{c.name}({_clip(args, 300)})` → **{res}**")
        if c.done and (c.error or len(c.result) < 400) and c.result.strip():
            steps.append(_indent(_fence(_clip(c.result, 1500))))
        elif c.error:
            steps.append("   _(no error message)_")
    parts = [
        f"**What went wrong:** {turn.fault()}",
        "Filed automatically by Syntexophobic's Assistant panel.",
        f"| | |\n|---|---|\n{rows}" + (f"\n| Project | `{project}` |" if project else ""),
        "### What the user asked\n" + _fence(_clip(turn.request, 2000)),
        "### What Claude did\n" + ("\n".join(steps) if steps else "_No tool calls._"),
    ]
    if turn.reported:
        parts.append("### Claude's report\n" + "\n\n".join(_fence(r) for r in turn.reported))
    if turn.run_error:
        parts.append("### Claude Code error\n" + _fence(_clip(turn.run_error, 2000)))
    parts.append("### Claude's answer\n" + ("\n".join("> " + l for l in _clip(turn.answer(), 3000).splitlines())
                                            or "_None._"))
    if screenshot_url:
        parts.append(f"### Window at the time\n![screenshot]({screenshot_url})")
    if logs:
        parts.append("<details><summary>Log (last lines)</summary>\n\n" + _fence(_clip(logs, 6000)) + "\n</details>")
    parts.append(MARK.format(sig))
    return "\n\n".join(parts)


def _indent(text: str) -> str:
    return "\n".join("   " + l for l in text.splitlines())


# ---- GitHub ---------------------------------------------------------------------------------

class GitHub:
    """The few REST calls a report needs, with a token, or through the `gh` command."""

    def __init__(self, tok: str | None = None, use_gh: bool | None = None):
        self.tok = token() if tok is None else tok
        self.use_gh = (not self.tok and shutil.which("gh") is not None) if use_gh is None else use_gh
        if not self.tok and not self.use_gh:
            raise FaultError("no GitHub login")

    def call(self, method: str, path: str, data: dict | None = None):
        url = path if path.startswith("https://") else f"{API}{path}"
        if self.tok:
            req = urllib.request.Request(url, method=method, data=None if data is None else json.dumps(data).encode(),
                                         headers={"Authorization": f"Bearer {self.tok}",
                                                  "Accept": "application/vnd.github+json",
                                                  "User-Agent": "syntexophobic"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = r.read()
            except urllib.error.HTTPError as e:
                raise FaultError(f"GitHub {e.code} on {method} {path}") from e
            except (urllib.error.URLError, OSError) as e:
                raise FaultError(f"GitHub unreachable: {e}") from e
            return json.loads(raw) if raw else None
        cmd = ["gh", "api", "--method", method, url.removeprefix("https://api.github.com/")]
        if data is not None:
            cmd += ["--input", "-"]
        try:
            p = subprocess.run(cmd, input=None if data is None else json.dumps(data), capture_output=True,
                               text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise FaultError(f"gh failed: {e}") from e
        if p.returncode != 0:
            raise FaultError(f"gh api {method} {path}: {(p.stderr or p.stdout).strip()[:300]}")
        return json.loads(p.stdout) if p.stdout.strip() else None

    def open_issue(self, sig: str) -> dict | None:
        mark = MARK.format(sig)
        for page in range(1, 4):
            issues = self.call("GET", f"/issues?state=open&labels={LABEL}&per_page=100&page={page}") or []
            for i in issues:
                if mark in (i.get("body") or "") and "pull_request" not in i:
                    return i
            if len(issues) < 100:
                break
        return None

    def upload(self, png: Path, name: str) -> str:
        """Commits the picture to the fault-reports branch; returns a link that shows it to
        anyone who can read the repository."""
        try:
            self.call("GET", f"/branches/{SHOTS_BRANCH}")
        except FaultError:
            repo = self.call("GET", "")
            sha = self.call("GET", f"/git/ref/heads/{repo['default_branch']}")["object"]["sha"]
            self.call("POST", "/git/refs", {"ref": f"refs/heads/{SHOTS_BRANCH}", "sha": sha})
        path = f"screenshots/{name}"
        self.call("PUT", f"/contents/{path}", {"message": f"Fault report screenshot {name}", "branch": SHOTS_BRANCH,
                                                "content": base64.b64encode(png.read_bytes()).decode()})
        return f"https://github.com/{REPO}/blob/{SHOTS_BRANCH}/{path}?raw=true"


@dataclass
class Filed:
    url: str
    number: int
    new: bool


def report(turn: Turn, screenshot: Path | None = None, project: str = "", gh: GitHub | None = None,
           extra_labels: list[str] = ()) -> Filed | None:
    """Files the turn's fault on GitHub (or adds it to the open issue with the same
    signature). None when the turn is not a fault or reporting is off."""
    if not turn.fault() or os.environ.get("SYNTEXOPHOBIC_NO_FAULT_REPORTS"):
        return None
    gh = gh or GitHub()
    sig = signature(turn)
    url = ""
    if screenshot is not None and screenshot.exists():
        try:
            url = gh.upload(screenshot, f"{time.strftime('%Y%m%d-%H%M%S')}-{sig}.png")
        except FaultError as e:                  # the report matters more than the picture
            log.warning("fault report screenshot not uploaded: %s", e)
    text = body(turn, sig, logs=log_tail(), screenshot_url=url, project=project)
    existing = gh.open_issue(sig)
    if existing:
        gh.call("POST", f"/issues/{existing['number']}/comments",
                {"body": "**Happened again.**\n\n" + text})
        log.info("fault %s added to issue #%s", sig, existing["number"])
        return Filed(existing["html_url"], existing["number"], False)
    issue = gh.call("POST", "/issues", {"title": title(turn), "body": text, "labels": [LABEL, *extra_labels]})
    log.info("fault %s filed as issue #%s", sig, issue["number"])
    return Filed(issue["html_url"], issue["number"], True)
