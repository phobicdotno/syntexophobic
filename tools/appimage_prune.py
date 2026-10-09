"""Shrink the AppImage: keep the Qt and VTK modules Syntexophobic imports, the Qt plugins a
desktop window needs, and every library those link against; delete the rest (Qt's web
engine, QML, multimedia, 3D, most of VTK). Run with the AppDir's own Python:

    AppDir/usr/python/bin/python3 tools/appimage_prune.py AppDir
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGINS = {"platforms", "platformthemes", "platforminputcontexts", "xcbglintegrations", "egldeviceintegrations",
           "imageformats", "iconengines", "generic", "tls", "networkinformation", "wayland-decoration-client",
           "wayland-graphics-integration-client", "wayland-shell-integration", "printsupport"}


def imported_modules() -> set[str]:
    """Extension modules loaded by importing every Syntexophobic module (no window)."""
    code = ("import os, pkgutil, importlib, sys, json; os.environ['QT_QPA_PLATFORM']='offscreen'\n"
            "import syntexophobic\n"
            "for m in pkgutil.iter_modules(syntexophobic.__path__):\n    if m.name != '__main__': importlib.import_module('syntexophobic.' + m.name)\n"
            "import vtkmodules.qt.QVTKRenderWindowInteractor\n"
            # imports inside functions too: every Qt / VTK module named in the source
            "import re\n"
            "src = ''.join(open(f, encoding='utf-8').read() for f in __import__('glob').glob(syntexophobic.__path__[0] + '/*.py'))\n"
            "for name in set(re.findall(r'\\b(PySide6\\.Qt\\w+|vtkmodules\\.[\\w.]+)', src)): importlib.import_module(name.rstrip('.'))\n"
            "print(json.dumps(sorted(getattr(m, '__file__', '') or '' for m in list(sys.modules.values()))))")
    import json
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout
    return {f for f in json.loads(out.strip().splitlines()[-1]) if f.endswith(".so")}


def needed(path: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


def main(appdir: Path):
    site = next((appdir / "usr/python/lib").glob("python3.*/site-packages"))
    pyside, vtk = site / "PySide6", site / "vtkmodules"
    pruned = [pyside, vtk]                      # only these two trees are trimmed
    by_name: dict[str, Path] = {}
    for root in site.iterdir():
        if root.is_dir():
            for f in root.rglob("*.so*"):
                if f.is_file():
                    by_name.setdefault(f.name, f)
    seeds = {Path(f) for f in imported_modules()}
    seeds |= {f for d in PLUGINS for f in (pyside / "Qt/plugins" / d).glob("*.so")}
    keep, todo = set(), list(seeds)
    while todo:
        f = todo.pop()
        if f in keep:
            continue
        keep.add(f)
        todo += [by_name[n] for n in needed(f) if n in by_name]
    # symlinked sonames (libfoo.so.6 -> libfoo.so.6.9.1): keep both ends
    keep |= {f.resolve() for f in keep}
    before = sum(f.stat().st_size for p in pruned for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    for p in pruned:
        for f in list(p.rglob("*.so*")):
            if f.is_file() and f not in keep and f.resolve() not in keep and not any(
                    k.name == f.name for k in keep):
                f.unlink()
    for d in (pyside / "Qt/plugins").iterdir():
        if d.name not in PLUGINS:
            shutil.rmtree(d)
    for junk in ("Qt/resources", "Qt/libexec", "Qt/qml", "Qt/translations", "Qt/metatypes", "include",
                 "typesystems", "glue", "scripts"):
        shutil.rmtree(pyside / junk, ignore_errors=True)
    after = sum(f.stat().st_size for p in pruned for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"Qt + VTK: {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
