"""Regenerates docs/screenshots/*.png from the demo part. Run: uv run python tools/screenshots.py"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter

from syntexophobic import app as A
from syntexophobic import simulate
from syntexophobic.project import Project

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"


def capture(win, name):
    """The window with the VTK view painted in (a plain widget grab shows it black)."""
    for _ in range(5):                  # let Qt lay out widgets that were just created
        QApplication.processEvents()
    win.viewer.GetRenderWindow().Render()
    f = vtkWindowToImageFilter()
    f.SetInput(win.viewer.GetRenderWindow())
    f.ReadFrontBufferOff()
    f.Update()
    tmp = Path(tempfile.mkdtemp()) / "view.png"
    w = vtkPNGWriter()
    w.SetFileName(str(tmp))
    w.SetInputConnection(f.GetOutputPort())
    w.Write()
    shot = win.grab().toImage()
    if win.viewer.isVisible():          # paint the GL view where it sits in the window
        view = QImage(str(tmp))
        p = QPainter(shot)
        top_left = win.viewer.mapTo(win, QPoint(0, 0))
        r = QRect(top_left, win.viewer.size())
        p.drawImage(r, view.scaled(r.size()))
        p.end()
    shot = shot.scaledToWidth(1600, Qt.SmoothTransformation)
    shot.save(str(OUT / name))
    print("saved", OUT / name)


def design_project():
    """A small designed bracket: dimensioned base sketch, pocket, hole pattern, fillets."""
    from syntexophobic.model import Feature, Part
    from syntexophobic.sketch import Sketch

    base = Sketch(name="base")
    ls = base.rectangle(0, 0, 90, 60)
    corner = base.lines[ls[0]].a
    base.constrain("fixed", corner)
    base.constrain("distance", ls[0], value=90)
    base.constrain("distance", ls[1], value=60)
    hole = base.circle((12, 12), 3)
    base.constrain("diameter", hole, value=6.5)
    base.constrain("horizontal_distance", corner, base.circles[hole].center, value=12)
    base.constrain("vertical_distance", corner, base.circles[hole].center, value=12)
    base.solve()
    pocket = Sketch(name="pocket", offset=16)
    pls = pocket.rectangle(25, 15, 65, 45)
    pocket.constrain("distance", pls[0], value=40)
    part = Part("bracket", [
        Feature("extrude", "Base", sketch="base", distance=16),
        Feature("extrude", "Pocket", sketch="pocket", distance=-9, operation="cut"),
        Feature("fillet", "Round corners", radius=6, edges="|Z"),
    ], {"base": base, "pocket": pocket})
    folder = Path(tempfile.mkdtemp()) / "bracket"
    proj = Project.create_design(folder, "bracket")
    part.save(folder)
    proj.history.save("Bracket: base, pocket, rounded corners")
    proj.reload()
    return proj


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    qa = QApplication(sys.argv)
    A._dark_palette(qa)
    folder = Path(tempfile.mkdtemp()) / "demo-plate-cam"
    proj = Project.create(folder, ROOT / "examples" / "demo-plate.step")
    job = proj.job
    job.name = "demo-plate"
    for op in job.operations:
        op.bottom = 4.0
    proj.save_job()
    proj.history.save("Rough and finish down to 4 mm")
    job.operations[0].stepover = 0.4
    proj.save_job()
    proj.history.save("Tighter stepover for a cleaner pocket floor")
    proj.history.new_branch("fast-roughing")
    job.operations[0].stepdown = 3.0
    job.machine.rpm = 20000
    proj.save_job()
    proj.history.save("3 mm stepdown, 20k rpm")
    proj.history.switch("main")
    proj.reload()

    win = A.MainWindow()
    win.resize(1680, 1000)
    win.show()
    win.load(proj)
    win._generated(proj.toolpaths())

    def step1():
        win.viewer.view_iso()
        capture(win, "toolpaths.png")
        win._simulated(simulate.simulate(proj.setup(), win.toolpaths))
        QTimer.singleShot(800, step2)

    def step2():
        capture(win, "simulation.png")
        win.c_model.setChecked(True)
        win.viewer.clear_simulation()
        win.c_paths.setChecked(True)
        win.l_versions.setCurrentRow(0)               # newest version: the fast-roughing variant
        QTimer.singleShot(500, step3)

    def step3():
        capture(win, "history.png")
        win.load(design_project())
        QTimer.singleShot(800, step4)

    def step4():
        win.viewer.view_iso()
        win.design_panel.l_features.setCurrentRow(2)
        capture(win, "design.png")
        win.open_sketch("base")
        QTimer.singleShot(400, win.sketch_view.fit)
        QTimer.singleShot(900, step5)

    def step5():
        win.sketch_view.selected = [next(iter(win.part.sketches["base"].circles))]
        win.sketch_view.draw()
        capture(win, "sketch.png")
        qa.quit()

    QTimer.singleShot(1500, step1)
    qa.exec()


if __name__ == "__main__":
    main()
