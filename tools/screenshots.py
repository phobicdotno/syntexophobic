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


def adaptive_project():
    """The demo plate roughed with adaptive clearing and finished on the walls."""
    from syntexophobic.job import Operation, Tool
    folder = Path(tempfile.mkdtemp()) / "demo-adaptive"
    proj = Project.create(folder, ROOT / "examples" / "demo-plate.step")
    proj.job.name = "demo-adaptive"
    proj.job.tools = [Tool(1, "6 mm flat endmill", 6.0, 2, "flat")]
    proj.job.operations = [Operation(type="adaptive", name="Adaptive", tool=1, stepdown=6.0, stepover=0.15,
                                     leave=0.3, bottom=4.0, feed=2270, plunge=540),
                           Operation(type="finish", name="Finish walls", tool=1, stepdown=6.0, leave=0.0, bottom=4.0)]
    proj.save_job()
    proj.history.save("Adaptive roughing")
    return proj


def dome_project():
    """A domed cap finished with a ball end (parallel passes), for the 3D finishing view."""
    import math as m
    from syntexophobic.job import Operation, Tool
    from syntexophobic.model import Feature, Part
    from syntexophobic.sketch import Sketch
    prof = Sketch(name="profile", plane="XZ")
    o, a, b = prof.point(0, 0), prof.point(30, 0), prof.point(0, 14)
    prof.line(o, a)
    # circle through (30, 0) and (0, 14) with its centre on the axis: (0, (14² - 30²) / 28)
    prof.arc((0, (14 ** 2 - 30 ** 2) / (2 * 14)), a, b)
    axis = prof.line(b, o)
    base = Sketch(name="base", offset=-6)
    base.rectangle(-38, -38, 38, 38)
    part = Part("cap", [Feature("revolve", "Dome", sketch="profile", axis=axis),
                        Feature("extrude", "Base", sketch="base", distance=6, operation="join"),
                        Feature("fillet", "Round base", radius=8, edges="|Z")],
                {"profile": prof, "base": base})
    folder = Path(tempfile.mkdtemp()) / "cap"
    proj = Project.create_design(folder, "cap")
    part.save(folder)
    proj.reload()
    proj.job.tools = [Tool(1, "6 mm flat endmill", 6.0, 2, "flat"), Tool(2, "3 mm ball", 3.0, 2, "ball")]
    proj.job.operations = [
        Operation(type="adaptive", name="Adaptive", tool=1, stepdown=6.0, stepover=0.15, leave=0.3, bottom=0.0),
        Operation(type="parallel", name="3D finish", tool=2, stepover=0.2, leave=0.0, bottom=0.0)]
    proj.save_job()
    proj.history.save("Domed cap, 3D finishing")
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
        win.finish_sketch(save=False)
        win.load(adaptive_project())
        win.c_workspace.setCurrentText("Manufacture")
        win._generated(win.project.toolpaths())
        QTimer.singleShot(800, step6)

    def step6():
        win.viewer.view_iso()
        win.job_dock.raise_()
        win.l_ops.setCurrentRow(0)
        capture(win, "adaptive.png")
        win.load(dome_project())
        win.c_workspace.setCurrentText("Manufacture")
        win._generated(win.project.toolpaths())
        win._simulated(simulate.simulate(win.project.setup(), win.toolpaths, res=0.15))
        QTimer.singleShot(800, step7)

    def step7():
        win.viewer.view_iso()
        win.l_ops.setCurrentRow(1)
        capture(win, "finish3d.png")
        qa.quit()

    QTimer.singleShot(1500, step1)
    qa.exec()


if __name__ == "__main__":
    main()
