"""The "Update available" bar above the 3D view: the new version, what changed in it, and
an Update button that installs it (the AppImage or the macOS app) or shows the command
that does."""

from __future__ import annotations

import html

from PySide6.QtCore import QObject, QProcess, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTextBrowser,
                               QToolButton, QVBoxLayout)

from . import update

SKIP_KEY = "update/skip"


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)


class _Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(self.signals.progress.emit))
        except update.UpdateError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - an update must never take the window down
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


def notes_dialog(up: update.Update, parent=None) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"What's new in {up.latest.version}")
    dlg.resize(620, 560)
    lay = QVBoxLayout(dlg)
    text = QTextBrowser()
    text.setOpenExternalLinks(True)
    text.setMarkdown(up.notes() + f"\n\n[The release on GitHub]({up.latest.page})")
    lay.addWidget(text, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    return dlg


def confirm_install(up: update.Update, parent=None) -> bool:
    """Ask before downloading, showing what's new and what will happen (like phobiCCCP)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Install update")
    dlg.resize(560, 480)
    lay = QVBoxLayout(dlg)
    head = QLabel(f"Install Syntexophobic {up.latest.version}?")
    head.setStyleSheet("font-weight: 600; font-size: 15px;")
    lay.addWidget(head)
    text = QTextBrowser()
    text.setOpenExternalLinks(True)
    text.setMarkdown(up.notes() + f"\n\n[The release on GitHub]({up.latest.page})")
    lay.addWidget(text, 1)
    size = f" (about {up.latest.asset_size / 1e6:.0f} MB)" if up.latest.asset_size else ""
    warn = QLabel(f"Downloads {html.escape(up.latest.asset or 'the new version')}{size} and "
                  "restarts Syntexophobic to use it.")
    warn.setWordWrap(True)
    lay.addWidget(warn)
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
    buttons.addButton("Install", QDialogButtonBox.AcceptRole)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    return dlg.exec() == QDialog.Accepted


class _UpgradeDialog(QDialog):
    """A progress dialog for the download, with a Details log and a Restart button on
    success — the phobiCCCP upgrade experience. Non-modal, driven by the install job."""

    def __init__(self, up: update.Update, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Installing Syntexophobic {up.latest.version}")
        self.resize(460, 170)
        self._phase = None
        lay = QVBoxLayout(self)
        self.head = QLabel(f"Installing Syntexophobic {up.latest.version}…")
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.b_details = QToolButton()
        self.b_details.setText("Details ▾")
        self.b_details.setCheckable(True)
        self.b_details.setAutoRaise(True)
        self.b_details.toggled.connect(self._toggle)
        row.addWidget(self.b_details)
        row.addStretch()
        lay.addLayout(row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace", 9))
        self.log.hide()
        lay.addWidget(self.log, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.b_restart = self.buttons.addButton("Restart now", QDialogButtonBox.AcceptRole)
        self.b_restart.hide()
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _toggle(self, on: bool):
        self.log.setVisible(on)
        self.b_details.setText("Details ▴" if on else "Details ▾")

    def progress(self, f: float, what: str):
        self.bar.setValue(max(0, min(100, int(f * 100))))
        self.head.setText(f"{what}… {f:.0%}")
        if what != self._phase:
            self._phase = what
            self.log.appendPlainText(what)

    def finished(self, message: str):
        self.bar.setValue(100)
        self.head.setText(message)
        self.log.appendPlainText("Done.")
        self.b_restart.show()

    def failed(self, err: str):
        self.head.setText("Update failed — nothing was changed.")
        self.log.appendPlainText(f"Error: {err}")
        self.b_details.setChecked(True)      # show the log so the failure is visible


class UpdateBar(QFrame):
    """Hidden until check() finds a newer release that was not skipped."""

    changed = Signal(str)          # a status line for the window's status bar

    def __init__(self, parent=None, pool: QThreadPool | None = None):
        super().__init__(parent)
        self.pool = pool or QThreadPool.globalInstance()
        self.pending: update.Update | None = None
        self.installed: update.Installed | None = None
        self._busy = False
        self._dismissed = ""           # version hidden with ✕: not shown again until the next start
        self._dlg: _UpgradeDialog | None = None
        self.setObjectName("update-bar")
        self.setStyleSheet("#update-bar { background: #1d3b5c; border-bottom: 1px solid #40a0ff; }"
                           "#update-bar QLabel { color: #e2e5ea; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 6, 5)
        self.text = QLabel()
        self.text.setTextFormat(Qt.RichText)
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.b_notes = QPushButton("What's new")
        self.b_update = QPushButton("Update")
        self.b_restart = QPushButton("Restart now")
        self.b_skip = QPushButton("Skip this version")
        self.b_close = QPushButton("✕")
        self.b_close.setToolTip("Hide until the next start")
        self.b_close.setFlat(True)
        self.b_close.setFixedWidth(28)
        for b in (self.b_notes, self.b_update, self.b_restart, self.b_skip, self.b_close):
            lay.addWidget(b)
        self.b_notes.clicked.connect(lambda: notes_dialog(self.pending, self).exec())
        self.b_update.clicked.connect(self.install)
        self.b_restart.clicked.connect(self.restart)
        self.b_skip.clicked.connect(self.skip)
        self.b_close.clicked.connect(self.dismiss)
        self.hide()

    # ---- checking ----------------------------------------------------------------------

    def check(self, quiet: bool = True, check=update.check):
        """Asks GitHub in the background. quiet: say nothing unless there is an update that
        was not skipped (no login, offline and up to date stay silent)."""
        if self._busy:
            return
        self._busy, self._quiet = True, quiet
        self._start(_Job(lambda _progress: check()), self._checked, self._check_failed)

    def _start(self, job: _Job, done, failed, progress=None):
        job.signals.done.connect(done)            # bound methods: delivered on the UI thread
        job.signals.failed.connect(failed)
        if progress:
            job.signals.progress.connect(progress)
        self._job = job                           # the signals live as long as the job
        self.pool.start(job)

    def _checked(self, up: update.Update | None):
        self._busy, quiet = False, self._quiet
        if up is None:
            if not quiet:
                from .about import version
                QMessageBox.information(self.window(), "Updates", f"Syntexophobic {version()} is the newest version.")
            return
        if self.installed is not None or not self.b_update.isEnabled():   # installed, or downloading
            return
        if quiet and up.latest.version in (QSettings().value(SKIP_KEY, ""), self._dismissed):
            return
        self.show_update(up)

    def _check_failed(self, err: str):
        self._busy, quiet = False, self._quiet
        if not quiet:
            QMessageBox.information(self.window(), "Updates", f"Could not check for updates: {err}.")

    # ---- showing -----------------------------------------------------------------------

    def show_update(self, up: update.Update):
        self.pending, self.installed = up, None
        areas = " · ".join(up.areas())
        behind = len(up.releases)
        more = f" ({behind} releases)" if behind > 1 else ""
        self.text.setText(
            f"<b>Syntexophobic {html.escape(up.latest.version)} is available</b>{more}, you have "
            f"{html.escape(up.current)}." + (f"<br>New: {html.escape(areas)}" if areas else ""))
        self.b_update.setText("Update")
        self.b_update.setEnabled(True)
        self.b_update.setToolTip(self._update_tip())
        for b in (self.b_notes, self.b_update, self.b_skip):
            b.show()
        self.b_restart.hide()
        self.show()
        self.changed.emit(f"Syntexophobic {up.latest.version} is available.")

    def _update_tip(self) -> str:
        app = update.running_build()
        if update.running_windows_build():
            return f"Shows where to download {self.pending.latest.asset or 'the new version'}"
        if app and self.pending.latest.asset_url:
            if app.suffix == ".app":
                return f"Download {self.pending.latest.asset} and put it in place of {app.name}"
            return f"Download {self.pending.latest.asset} next to {app.name} and start it from now on"
        return f"Shows how: {update.how_to_update()}"

    def dismiss(self):
        if self.installed is None:
            self._dismissed = self.pending.latest.version
        self.hide()

    def skip(self):
        QSettings().setValue(SKIP_KEY, self.pending.latest.version)
        self.hide()

    # ---- installing --------------------------------------------------------------------

    def install(self, install=update.install, confirm=confirm_install):
        up = self.pending
        app = update.running_build()
        if (folder := update.running_windows_build()) is not None:
            QMessageBox.information(
                self.window(), "Update Syntexophobic",
                f"Download {up.latest.asset or 'the Windows build'} from {up.latest.page}, close "
                f"Syntexophobic and unpack it in place of {folder}.")
            return
        if app is None or not up.latest.asset_url:
            cmd = update.how_to_update()
            QApplication.clipboard().setText(cmd)
            what = "the macOS app" if update.running_app() else "the AppImage"
            QMessageBox.information(
                self.window(), "Update Syntexophobic",
                f"This copy of Syntexophobic is not {what}. To update it, run:\n\n    {cmd}\n\n"
                "(copied to the clipboard), then start it again. Or download "
                f"{up.latest.asset or what} from {up.latest.page}")
            return
        if not confirm(up, self.window()):             # notes + warning before downloading
            return
        self.b_update.setEnabled(False)
        self.b_skip.hide()
        self.b_update.setText("Downloading…")
        self._dlg = _UpgradeDialog(up, self.window())
        self._dlg.b_restart.clicked.connect(self.restart)
        self._dlg.show()
        self._start(_Job(lambda progress: install(up.latest, app, progress=progress)),
                    self._installed, self._install_failed, self._progress)

    def _progress(self, f: float, what: str):
        self.b_update.setText(f"{what} {f:.0%}")
        if self._dlg is not None:
            self._dlg.progress(f, what)

    def _installed(self, done: update.Installed):
        self.installed = done
        def home(p):
            return str(p).replace(str(update.Path.home()), "~", 1)
        where = (f"{home(done.links[0])} now starts it" if done.links
                 else f"{home(done.path)} is the new version" if done.path.suffix == ".app"
                 else f"saved as {home(done.path)}")
        self.text.setText(f"<b>Syntexophobic {html.escape(self.pending.latest.version)} is installed</b>: "
                          f"{html.escape(where)}. Restart to use it.")
        self.b_update.hide()
        self.b_skip.hide()
        self.b_restart.show()
        size = self.pending.latest.asset_size
        fetched = (f" Downloaded {done.downloaded / 1e6:.1f} MB of {size / 1e6:.0f} MB (only what changed)."
                   if size and 0 < done.downloaded < size else "")
        if self._dlg is not None:
            self._dlg.finished(f"Syntexophobic {self.pending.latest.version} is installed. "
                               "Restart to use it.")
        self.changed.emit(f"Installed Syntexophobic {self.pending.latest.version}.{fetched}")

    def _install_failed(self, err: str):
        self.b_update.setText("Try again")
        self.b_update.setEnabled(True)
        self.b_skip.show()
        self.changed.emit(f"Update failed: {err}")
        if self._dlg is not None:
            self._dlg.failed(err)          # the dialog shows the error and its log
        else:
            QMessageBox.warning(self.window(), "Update failed", err)

    def restart(self):
        if not self.installed:
            return
        start = self.installed.start
        # a macOS app starts through `open`; -n: a new instance, as this one is still running
        started = (QProcess.startDetached("open", ["-n", str(start)]) if start.suffix == ".app"
                   else QProcess.startDetached(str(start), []))
        if started:
            self.window().close()
