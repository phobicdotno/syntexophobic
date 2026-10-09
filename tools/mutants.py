"""Mutation check for the safety tests: put known bugs back and make sure a test fails.

Each mutant edits a copy of the source; the tests run against that copy. A mutant that
'survives' (all tests pass) means the tests would not catch that bug.
Run: uv run python tools/mutants.py [words in mutant names, to run only those]
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTANTS = [
    ("rough ignores the part (empty union)", "cam.py",
     "        return list(clip) if op == pyclipper.CT_UNION else []", "        return []"),
    ("tool radius left out of roughing offset", "cam.py",
     "    region = _boolean(_offset(setup.stock_rect(), 0.9 * r), _offset(forbidden, r + op.leave),",
     "    region = _boolean(_offset(setup.stock_rect(), 0.9 * r), _offset(forbidden, op.leave),"),
    ("ball drop-cutter ignores triangle edges", "surface.py",
     "    for k in range(3):                                   # edges", "    for k in range(0):                                   # edges"),
    ("ball drop-cutter ignores triangle faces", "surface.py", "        if c > 1e-9:\n            if ball:", "        if c > 1e9:\n            if ball:"),
    ("work offset sign flipped in the post", "gcode.py", "                    v -= o\n", "                    v += o\n"),
    ("tabs not lifted", "cam.py", "                if tabs and z < tab_top:", "                if False:"),
    ("ramp ignored (straight plunge)", "cam.py", "    if ramp_angle <= 0 or perimeter < 0.5:", "    if True:"),
    ("adaptive may touch the part (no keep-out)", "cam.py",
     "        allowed = _boolean(_offset(setup.stock_rect(), r + 2.0), keep_out, pyclipper.CT_DIFFERENCE)",
     "        allowed = _offset(setup.stock_rect(), r + 2.0)"),
    ("drill pecks from the hole top", "cam.py", "nxt = max(bottom, depth - op.stepdown)", "nxt = max(bottom, min(depth, h.top) - op.stepdown)"),
    ("ball waterline widens bands by their lower width", "cam.py",
     "            out = _boolean(out, _offset(above, width(higher_h) + leave), pyclipper.CT_UNION)",
     "            out = _boolean(out, _offset(above, width(h) + leave), pyclipper.CT_UNION)"),
    ("point simplifier compares neighbours only", "surface.py",
     "        if worst > tol * tol:\n            keep[at] = True",
     "        if worst > tol * tol and False:\n            keep[at] = True"),
    ("loop outline chained from the segment start", "model.py",
     "        end = seg_pts[-1]\n", "        end = seg_pts[0]\n"),
    ("holes attached with a fixed reversal", "model.py",
     "    if (area > 0) != ccw:\n        wire = TopoDS.Wire(wire.Reversed())",
     "    if not ccw:\n        wire = TopoDS.Wire(wire.Reversed())"),
    ("adaptive leftovers not cleaned up", "cam.py", "        if left.any():\n            leftover", "        if False:\n            leftover"),
    ("arc direction swapped (G2/G3)", "gcode.py", 'out.append(("G3 " if ccw else "G2 ")', 'out.append(("G2 " if ccw else "G3 ")'),
    ("bottom side not mirrored back", "simulate.py", "        bottom = 2 * cz - under[::-1, :]", "        bottom = 2 * cz - under"),
    ("rough tool kept inside the stock outline", "cam.py",
     "    region = _boolean(_offset(setup.stock_rect(), 0.9 * r), _offset(forbidden, r + op.leave),",
     "    region = _boolean(setup.stock_rect(), _offset(forbidden, r + op.leave),"),
    ("first tool change skipped (no M6 T1)", "gcode.py", '            if post["tool_change"] == "M6":', '            if post["tool_change"] == "M6" and current_tool is not None:'),
    ("engraving counted as gouges", "simulate.py",
     '        if tp.op.type in ("vcarve", "engrave") and tp.op.source:', '        if tp.op.type == "vcarve" and tp.op.source:'),
    ("engrave lines not joined end to end", "cam.py", "    tol = 1e-4\n    near =", "    tol = -1.0\n    near ="),
    ("rapid check lets shaped tools through stock", "simulate.py",
     "                        under = pz + math.sqrt(d2) / k", "                        under = pz + 1e9"),
    ("engrave depth from the stock top", "cam.py",
     "        surface = float(np.max(surface_heights(setup, xy[:, 0], xy[:, 1])))", "        surface = top"),
    ("v-carve depth from the stock top", "cam.py", "        z = surface - depth\n", "        z = top - depth\n"),
    ("engrave open lines not reversed between passes", "cam.py",
     "            if not closed:\n                path = path[::-1]", "            if False:\n                path = path[::-1]"),
]


def run(name, filename, old, new):
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "src", tmp / "src")
    path = tmp / "src" / "syntexophobic" / filename
    text = path.read_text()
    if old not in text:
        return name, "PATTERN NOT FOUND (update this mutant)"
    path.write_text(text.replace(old, new, 1))
    env = dict(os.environ, PYTHONPATH=str(tmp / "src"), NUMBA_CACHE_DIR=str(tmp / "numba"))
    r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
                       cwd=tmp, env=env, capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return name, "caught" if r.returncode != 0 else "SURVIVED"


if __name__ == "__main__":
    picked = [m for m in MUTANTS if not sys.argv[1:] or any(a in m[0] for a in sys.argv[1:])]
    results = []
    for m in picked:                                 # report each as it finishes
        name, verdict = run(*m)
        results.append((name, verdict))
        print(f"{verdict:10} {name}", flush=True)
    # a mutant whose code pattern is gone checks nothing: that is a failure too
    sys.exit(0 if all(v == "caught" for _, v in results) else 1)
