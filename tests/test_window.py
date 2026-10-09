import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from syntexophobic.app import MainWindow
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win, app
    win.close()


def test_window_fits_a_laptop_screen(window):
    # A minimum height above the screen's stops the window manager maximizing or going full screen.
    win, _ = window
    assert win.minimumSizeHint().height() <= 700
    assert win.design_dock.isVisible() != win.job_dock.isVisible()


def test_full_screen_toggles(window):
    win, app = window
    win.a_fullscreen.trigger()
    app.processEvents()
    assert win.isFullScreen()
    win.a_fullscreen.trigger()
    app.processEvents()
    assert not win.isFullScreen() and not win.a_fullscreen.isChecked()
