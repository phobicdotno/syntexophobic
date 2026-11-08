"""Every error while the program is in use goes back to its GitHub repository for analysis,
not only the Assistant's faults (faults.py): exceptions nobody caught (in the window, in a
thread), errors the program logs, error and warning dialogs shown to the user, Qt's own
critical errors, and a crash (segfault, abort) of the last run, found at the next start.

Each one is filed as an issue (labels fault-report, app-error) with the version and build,
OS, what went wrong, the traceback and the log tail: the menu and toolbar actions and
commands that led up to it. Repeats do not flood the repository:

- an error is known by its signature (kind, exception type, where in the program it was
  raised, the message with paths and numbers taken out);
- a repeat goes to the open issue with that signature as a comment, never a new issue;
- in one run, each signature is sent once; later repeats are only counted;
- after a send, the same signature waits a day before its next comment, which says how many
  times it happened in between;
- at most 20 reports go out per run.

Reports are sent in the background with the GitHub login the update check uses; without one,
or offline, nothing is sent and nothing is shown. Help > Send error reports automatically,
SYNTEXOPHOBIC_NO_FAULT_REPORTS=1, or running under CI turns it off."""

from __future__ import annotations

import faulthandler
import hashlib
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import faults
from .automation import STATE

LABEL = "app-error"
SENT = STATE / "error-reports.json"        # signature -> issue, last send, repeats since
CRASH = STATE / "crash.log"
QUIET = 24 * 3600                          # seconds between two comments for one signature
MAX_PER_RUN = 20
PACKAGE = Path(__file__).resolve().parent


@dataclass
class Incident:
    kind: str                  # "Unhandled exception", "Logged error", "Error dialog", ...
    message: str
    detail: str = ""           # traceback or the whole text
    where: str = ""            # module:function in this program where it was raised
    exc_type: str = ""
    project: str = ""
    when: float = 0.0

    def signature(self) -> str:
        key = "\n".join((self.kind, self.exc_type, self.where, faults._normal(self.message)))
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def title(self) -> str:
        what = f"{self.exc_type}: {self.message}" if self.exc_type else self.message
        return f"[error] {self.kind}: {faults._clip(' '.join(what.split()), 110)}"[:200]

    def body(self, env: dict[str, str] | None = None, logs: str = "", repeats: int = 0) -> str:
        env = faults.environment() if env is None else env
        rows = "\n".join(f"| {k} | {v} |" for k, v in env.items())
        parts = [f"**What went wrong:** {self.kind}" + (f" in `{self.where}`" if self.where else ""),
                 "Sent automatically by Syntexophobic.",
                 f"| | |\n|---|---|\n{rows}" + (f"\n| Project | `{self.project}` |" if self.project else ""),
                 "### Message\n" + faults._fence(faults._clip(self.message, 2000))]
        if repeats:
            parts.insert(1, f"**Happened {repeats} more time{'s' if repeats > 1 else ''} since the last report.**")
        if self.detail.strip() and self.detail.strip() != self.message.strip():
            parts.append("### Details\n" + faults._fence(faults._clip(self.detail, 8000)))
        if logs:
            parts.append("<details><summary>Log: what led up to it (last lines)</summary>\n\n"
                         + faults._fence(faults._clip(logs, 6000)) + "\n</details>")
        parts.append(faults.MARK.format(self.signature()))
        return "\n\n".join(parts)


def from_exception(kind: str, exc: BaseException, tb=None) -> Incident:
    tb = tb or exc.__traceback__
    frames = traceback.extract_tb(tb) if tb else []
    ours = [f for f in frames if Path(f.filename).resolve().is_relative_to(PACKAGE)]
    top = (ours or frames or [None])[-1]
    where = f"{Path(top.filename).stem}:{top.name}" if top else ""
    detail = "".join(traceback.format_exception(type(exc), exc, tb))
    return Incident(kind, str(exc) or "(no message)", detail, where, type(exc).__name__)


def send_to_github(inc: Incident, repeats: int, gh: faults.GitHub | None = None) -> faults.Filed:
    """Files the incident, or adds it to the open issue with its signature."""
    gh = gh or faults.GitHub()
    text = inc.body(logs=faults.log_tail(), repeats=repeats)
    existing = gh.open_issue(inc.signature())
    if existing:
        gh.call("POST", f"/issues/{existing['number']}/comments", {"body": "**Happened again.**\n\n" + text})
        return faults.Filed(existing["html_url"], existing["number"], False)
    issue = gh.call("POST", "/issues", {"title": inc.title(), "body": text, "labels": [faults.LABEL, LABEL]})
    return faults.Filed(issue["html_url"], issue["number"], True)


class Reporter:
    """Collects incidents and sends them from a background thread, deduplicated and limited."""

    def __init__(self, send: Callable[[Incident, int], faults.Filed] = send_to_github,
                 state: Path = SENT, enabled: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float] = time.time, threaded: bool = True):
        self.send, self.state_path, self.enabled, self.clock = send, state, enabled, clock
        self.project: Callable[[], str] = lambda: ""
        self.seen: dict[str, int] = {}             # this run: signature -> times
        self.sent_this_run = 0
        self.lock = threading.Lock()
        self.local = threading.local()             # set while reporting: errors in it are not reported
        self.queue: queue.Queue = queue.Queue()
        if threaded:
            threading.Thread(target=self._work, name="error-reports", daemon=True).start()
        self.threaded = threaded

    def off(self) -> bool:
        return bool(os.environ.get("SYNTEXOPHOBIC_NO_FAULT_REPORTS") or os.environ.get("CI")
                    or not self.enabled())

    def capture(self, inc: Incident) -> bool:
        """True when the incident was queued to be sent."""
        if getattr(self.local, "busy", False) or self.off():
            return False
        sig = inc.signature()
        inc.when = inc.when or self.clock()
        if not inc.project:
            try:
                inc.project = self.project()
            except Exception:
                pass
        with self.lock:
            self.seen[sig] = self.seen.get(sig, 0) + 1
            if self.seen[sig] > 1:                  # already handled in this run
                self._count(sig)
                return False
            entry = self._load().get(sig, {})
            if self.clock() - entry.get("sent", 0) < QUIET or self.sent_this_run >= MAX_PER_RUN:
                self._count(sig)
                return False
            self.sent_this_run += 1
        if self.threaded:
            self.queue.put(inc)
        else:
            self._deliver(inc)
        return True

    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _count(self, sig: str):
        data = self._load()
        data.setdefault(sig, {})["repeats"] = data.get(sig, {}).get("repeats", 0) + 1
        self._save(data)

    def _work(self):
        while True:
            inc = self.queue.get()
            try:
                self._deliver(inc)
            finally:
                self.queue.task_done()

    def _deliver(self, inc: Incident):
        sig = inc.signature()
        self.local.busy = True
        try:
            with self.lock:
                repeats = self._load().get(sig, {}).get("repeats", 0)
            filed = self.send(inc, repeats)
            with self.lock:
                data = self._load()
                data[sig] = {"issue": filed.url, "sent": self.clock(), "repeats": 0}
                self._save(data)
            faults.log.info("error %s sent to issue #%s", sig, filed.number, extra={"no_report": True})
        except Exception as e:  # no login, offline, GitHub refused: try again next run
            faults.log.info("error %s not sent: %s", sig, e, extra={"no_report": True})
        finally:
            self.local.busy = False

    def wait(self, timeout: float = 10.0):
        """For tests and quitting: until the queue is empty."""
        end = time.time() + timeout
        while self.threaded and self.queue.unfinished_tasks and time.time() < end:
            time.sleep(0.02)


class _LogHandler(logging.Handler):
    """Errors the program logs (ERROR and up) are incidents too."""

    def __init__(self, reporter: Reporter):
        super().__init__(logging.ERROR)
        self.reporter = reporter

    def emit(self, record: logging.LogRecord):
        if getattr(record, "no_report", False):
            return
        if record.exc_info and record.exc_info[1] is not None:
            inc = from_exception("Logged error", record.exc_info[1], record.exc_info[2])
            inc.message = f"{record.getMessage()}: {inc.message}"
        else:
            inc = Incident("Logged error", record.getMessage(), where=f"{record.module}:{record.funcName}")
        self.reporter.capture(inc)


_reporter: Reporter | None = None
_crash_file = None


def reporter() -> Reporter | None:
    return _reporter


def install(enabled: Callable[[], bool] = lambda: True, qt: bool = True, crashes: bool = True) -> Reporter:
    """Hooks every source of errors into one Reporter (once per process). crashes: watch for
    a crash with faulthandler (only the window: one process per crash file)."""
    global _reporter, _crash_file
    if _reporter is not None:
        return _reporter
    faults.setup_logging()
    r = _reporter = Reporter(enabled=enabled)

    old_hook = sys.excepthook

    def excepthook(t, e, tb):
        old_hook(t, e, tb)
        if not issubclass(t, KeyboardInterrupt):
            faults.log.error("unhandled %s: %s", t.__name__, e, extra={"no_report": True})
            r.capture(from_exception("Unhandled exception", e, tb))
    sys.excepthook = excepthook

    old_thread_hook = threading.excepthook

    def thread_hook(args):
        old_thread_hook(args)
        if args.exc_value is not None:
            r.capture(from_exception(f"Unhandled exception in thread {args.thread.name if args.thread else '?'}",
                                     args.exc_value, args.exc_traceback))
    threading.excepthook = thread_hook

    faults.log.addHandler(_LogHandler(r))

    if crashes:
        _crash_file = watch_crashes(r)
    if qt:
        _install_qt(r)
    return r


def watch_crashes(r: Reporter, path: Path = CRASH):
    """Reports what faulthandler wrote when the last run crashed, then lets it write this
    run's crash (if any) to the same file. Returns the open file (keep it open)."""
    try:
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        f = open(path, "w", encoding="utf-8")
    except OSError:
        return None
    faulthandler.enable(f, all_threads=True)
    if old.strip():
        first = next(l for l in old.splitlines() if l.strip()).strip()
        where = next((l.split('File "')[1].split('"')[0] + ":" + l.rsplit(" in ", 1)[-1].strip()
                      for l in old.splitlines() if l.strip().startswith('File "') and " in " in l), "")
        r.capture(Incident("Crash in the previous run", first, old, Path(where).name if where else "", "crash"))
    return f


def _install_qt(r: Reporter):
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QMessageBox

    def message(kind, context, text):
        if sys.stderr is not None:                 # None in the Windows build (no console)
            sys.stderr.write(text + "\n")
        if kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            r.capture(Incident("Qt error", text, where=f"{context.file or ''}:{context.function or ''}".strip(":")))
    qInstallMessageHandler(message)

    # error and warning dialogs: what the user was told went wrong
    for name in ("critical", "warning"):
        original = getattr(QMessageBox, name)

        def shown(parent, title, text, *args, _original=original, _name=name, **kw):
            caller = traceback.extract_stack(limit=2)[0]
            r.capture(Incident(f"{'Error' if _name == 'critical' else 'Warning'} dialog “{title}”", str(text),
                               where=f"{Path(caller.filename).stem}:{caller.name}"))
            return _original(parent, title, text, *args, **kw)
        setattr(QMessageBox, name, staticmethod(shown))
