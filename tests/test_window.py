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


def test_sketch_ribbon_fits_a_laptop_screen(window):
    # The sketch ribbon is wider than a laptop screen. It used to push the window's minimum
    # width past the screen (full screen could not fit it) and grow to half the window's
    # height (its VLine separators grow vertically). It scrolls sideways at its own height.
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from syntexophobic.design_ui import sketch_toolbar
    from syntexophobic.sketch import Sketch
    from syntexophobic.sketcher import SketchView
    win, app = window
    view = SketchView(Sketch())
    page = QWidget()
    col = QVBoxLayout(page)
    ribbon = sketch_toolbar(view, lambda *_: None)
    col.addWidget(ribbon)
    col.addWidget(view)
    win.stack.addWidget(page)
    win.stack.setCurrentWidget(page)
    win.resize(1400, 860)
    app.processEvents()
    try:
        assert win.minimumSizeHint().width() <= 1280
        assert ribbon.height() == ribbon.sizeHint().height() < 140
        assert view.height() > 400
    finally:
        win.stack.setCurrentWidget(win.viewer)
        win.stack.removeWidget(page)
        page.deleteLater()


def test_slice_for_fabrication_dialog(window, tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from syntexophobic.fabslice_ui import FabSliceDialog
    win, app = window
    shape = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 60, 40).Shape()
    dlg = FabSliceDialog(shape, win.viewer, stem="boxy", parent=win)
    dlg.show()
    app.processEvents()
    assert dlg.sheets and "parts" in dlg.summary.text()
    assert win.viewer.shape is not shape                  # the 3D view shows the slices
    for i in range(dlg.style.count()):                   # every style builds sheets
        dlg.style.setCurrentIndex(i)
        dlg.recompute()
        assert dlg.sheets, (dlg.style.currentText(), dlg.error)
    dlg.hide()
    files = dlg.export(str(tmp_path))
    assert files and all(f.exists() for f in files)
    dlg.reject()
    assert win.viewer.shape is shape                      # back to the model
    assert win.a_fabslice.icon().isNull() is False
