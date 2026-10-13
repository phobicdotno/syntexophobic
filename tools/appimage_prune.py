"""Shrink the AppImage: keep the Qt and VTK modules Syntexophobic imports, the Qt plugins a
desktop window needs, and every library those link against; delete the rest (Qt's web
engine, QML, multimedia, 3D, most of VTK). Run with the AppDir's own Python:

    AppDir/usr/python/bin/python3 tools/appimage_prune.py AppDir

The macOS app is pruned the same way, its libraries being Mach-O files and Qt's frameworks
(build-macos.sh runs it on Syntexophobic.app/Contents, the app's Python prefix).
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
MAC = sys.platform == "darwin"
if MAC:     # the macOS style, the camera (Take photo…) and the camera permission prompt
    PLUGINS |= {"styles", "multimedia", "permissions"}
MACHO = {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}     # 64-bit and universal Mach-O


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
    if MAC:     # @rpath/QtCore.framework/Versions/A/QtCore, @rpath/libvtkCommonCore-9.6.dylib: by file name
        out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True).stdout
        return [Path(line.split(" (")[0].strip()).name for line in out.splitlines()[1:] if " (" in line]
    out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


def libraries(root: Path) -> list[Path]:
    """The shared libraries under root: *.so* files, or on macOS every Mach-O file (dylibs,
    extension modules, framework binaries such as QtCore.framework/Versions/A/QtCore)."""
    if not MAC:
        return [f for f in root.rglob("*.so*") if f.is_file()]
    out = []
    for f in root.rglob("*"):
        if f.is_file() and not f.is_symlink():
            with open(f, "rb") as fh:
                if fh.read(4) in MACHO:
                    out.append(f)
    return out


def framework(f: Path) -> Path | None:
    """QtFoo.framework for its binary QtFoo.framework/Versions/A/QtFoo."""
    fw = next((p for p in f.parents if p.suffix == ".framework"), None)
    return fw if fw is not None and f.name == fw.stem else None


def main(appdir: Path):
    site = next(p for lib in (appdir / "usr/python/lib", appdir / "lib") for p in lib.glob("python3.*/site-packages"))
    pyside, vtk = site / "PySide6", site / "vtkmodules"
    pruned = [pyside, vtk]                      # only these two trees are trimmed
    by_name: dict[str, Path] = {}
    for root in site.iterdir():
        if root.is_dir():
            for f in libraries(root):
                by_name.setdefault(f.name, f)
    seeds = {Path(f) for f in imported_modules()}
    seeds |= {f for d in PLUGINS for f in (pyside / "Qt/plugins" / d).glob("*.dylib" if MAC else "*.so")}
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
    kept_names = {k.name for k in keep}
    for p in pruned:
        for f in libraries(p):
            if f.exists() and f not in keep and f.resolve() not in keep and f.name not in kept_names:
                fw = framework(f) if MAC else None
                if fw is not None:
                    shutil.rmtree(fw)               # the whole framework: headers, resources, links
                elif fw is None and not (MAC and any(q.suffix == ".framework" for q in f.parents)):
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
