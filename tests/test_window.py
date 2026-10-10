"""The main window on a real display. Qt's offscreen platform cannot host the VTK view (it
draws into an X11 window that does not exist, and X ends the process), so these checks
run in a child process on the desktop's display, and are skipped where there is none."""

import os
import subprocess
import sys
import textwrap

import pytest

needs_display = pytest.mark.skipif(not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                                   reason="needs a desktop display for the 3D view")


def _run(body: str):
    code = textwrap.dedent("""
        import time
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from syntexophobic.app import MainWindow
        win = MainWindow()
        win.show()
        app.processEvents()

        def settle(check, seconds=3.0):
            # the window manager applies full screen / maximize asynchronously
            end = time.monotonic() + seconds
            while not check() and time.monotonic() < end:
                app.processEvents()
                time.sleep(0.02)
            return check()
    """) + textwrap.dedent(body) + "\nwin.close()\nprint('OK')\n"
    env = dict(os.environ, QT_QPA_PLATFORM="xcb")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


@needs_display
def test_window_fits_a_laptop_screen():
    # A minimum height above the screen's stops the window manager maximizing or going full screen.
    _run("""
        assert win.minimumSizeHint().height() <= 700
        assert win.design_dock.isVisible() != win.job_dock.isVisible()
    """)


@needs_display
def test_full_screen_toggles():
    _run("""
        win.a_fullscreen.trigger()
        assert settle(win.isFullScreen)
        win.a_fullscreen.trigger()
        assert settle(lambda: not win.isFullScreen()) and not win.a_fullscreen.isChecked()
    """)
