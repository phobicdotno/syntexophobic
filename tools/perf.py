"""Times what a user waits for in the window, to compare platforms (CI prints it on all three).

    uv run python tools/perf.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", {"darwin": "cocoa", "win32": "windows"}.get(sys.platform, "offscreen"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
t0 = time.perf_counter()
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from syntexophobic.app import MainWindow  # noqa: E402

rows: list[tuple[str, float]] = [("import the program", time.perf_counter() - t0)]


def timed(name: str, fn, n: int = 1):
    fn()                                         # warm up
    t = time.perf_counter()
    for _ in range(n):
        fn()
    rows.append((name + (f" (each of {n})" if n > 1 else ""), (time.perf_counter() - t) / n))


def spawn_git():
    subprocess.run(["git", "--version"], capture_output=True)


timed("start git once", spawn_git, 10)
t = time.perf_counter()
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()
rows.append(("open the window", time.perf_counter() - t))

import screenshots  # noqa: E402

proj = screenshots.design_project()
t = time.perf_counter()
win.load(proj)
app.processEvents()
rows.append(("load a design project", time.perf_counter() - t))
timed("history panel refresh", win._refresh_history, 3)
timed("history status (git status)", win._refresh_history_status, 10)


def edit():
    win._design_changed()
    win._rebuild_timer.stop()
    win._rebuild_part()
    app.processEvents()


timed("design edit and rebuild", edit, 5)


def orbit():
    win.viewer.style.orbit(4, 1)
    app.processEvents()


timed("orbit the view one step", orbit, 30)
win.open_sketch("base")
app.processEvents()
view = win.sketch_view
timed("redraw the sketch", lambda: (view.solve_and_draw(), app.processEvents()), 10)
win.finish_sketch(save=False)

for name, s in rows:
    print(f"{s * 1000:9.1f} ms  {name}")
win.close()
