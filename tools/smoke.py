"""Starts a built Syntexophobic far enough to show that the build works: every module imports,
a project is made from the demo STEP, its toolpaths are computed and simulated (OpenCascade,
numba) and the window opens on it (Qt, VTK). Run with the build's own Python:

    build/AppDir/usr/python/bin/python3 tools/smoke.py
    build/Syntexophobic.app/Contents/MacOS/python3 tools/smoke.py
"""

import importlib
import os
import pkgutil
import shutil
import sys
import tempfile
from pathlib import Path

if sys.platform.startswith("linux"):           # the macOS runner has a desktop; Linux CI has none
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SYNTEXOPHOBIC_NO_DESKTOP_ENTRY", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
import syntexophobic  # noqa: E402

for m in pkgutil.iter_modules(syntexophobic.__path__):
    if m.name != "__main__":
        importlib.import_module("syntexophobic." + m.name)
from syntexophobic import simulate  # noqa: E402
from syntexophobic.app import MainWindow  # noqa: E402
from syntexophobic.project import Project  # noqa: E402

tmp = Path(tempfile.mkdtemp())
step = Path(shutil.copy(Path(__file__).resolve().parents[1] / "examples/demo-plate.step", tmp))
project = Project.create(tmp / "demo-cam", step)
paths = project.toolpaths()
result = simulate.simulate(project.setup(), paths, res=1.0)
win = MainWindow()
win.show()
win.load(project)
for _ in range(20):
    app.processEvents()
win.close()
shutil.rmtree(tmp, ignore_errors=True)
print(f"smoke test passed: {syntexophobic.__file__}, {len(paths)} toolpaths, "
      f"{result.removed_cm3:.1f} cm³ removed, Qt platform {app.platformName()}")
