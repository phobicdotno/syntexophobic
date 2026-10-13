#!/usr/bin/env bash
# Builds dist/Syntexophobic-<version>-windows-x64.zip: a Syntexophobic folder with
# Syntexophobic.exe, its own Python and every library, and git (MinGit). Run it on Windows in
# Git Bash (the release workflow does, on GitHub's Windows runners). Needs uv, gcc and windres
# (MinGW), 7-Zip, and the GitHub CLI for MinGit.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-uv}
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
OUT=build/windows/Syntexophobic
rm -rf build/windows && mkdir -p "$OUT" dist

# a relocatable Python (python-build-standalone, as uv installs it)
"$UV" python install 3.12 >/dev/null
PY=$(cygpath -u "$("$UV" python find --managed-python 3.12)")
cp -a "$(dirname "$PY")" "$OUT/python"
rm -f "$OUT"/python/Lib/EXTERNALLY-MANAGED
PYTHON="$OUT/python/python.exe"

"$UV" build --wheel -o build/wheel >/dev/null
"$UV" pip install --python "$PYTHON" build/wheel/syntexophobic-"$VERSION"-*.whl
rm -rf "$OUT/python/Scripts"                           # console scripts: Syntexophobic.exe starts it
# trim what the program never uses: Qt's web engine, QML, 3D, most of VTK...
"$PYTHON" tools/appimage_prune.py "$OUT/python"

# git, for project history: most Windows machines have none. MinGit is Git for Windows'
# own minimal git for programs that use it; Syntexophobic.exe uses it when git is missing.
gh release download --repo git-for-windows/git --pattern 'MinGit-*-64-bit.zip' --dir build/windows --clobber
rm -f build/windows/MinGit-*busybox*
7z x -bd -y -o"$OUT/git" build/windows/MinGit-*-64-bit.zip >/dev/null

find "$OUT/python" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$OUT"/python/Lib/{test,idlelib,tkinter,turtledemo} "$OUT"/python/tcl \
       "$OUT"/python/Lib/site-packages/{pip,setuptools}* \
       "$OUT"/python/Lib/site-packages/PySide6/{qml,translations,examples,glue,include,typesystems} \
       "$OUT"/python/Lib/site-packages/PySide6/*.exe

# Syntexophobic.exe, with the program's icon
QT_QPA_PLATFORM=offscreen "$PYTHON" -c "
import sys; from PySide6.QtGui import QGuiApplication, QImage; from syntexophobic import desktop
app = QGuiApplication([])
QImage.fromData(desktop.png(256)).save(sys.argv[1], 'ICO')" build/windows/syntexophobic.ico
cp tools/windows/launcher.c tools/windows/launcher.rc build/windows/
(cd build/windows && windres launcher.rc -O coff -o launcher.res &&
 gcc -O2 -municode -mwindows -static launcher.c launcher.res -o Syntexophobic/Syntexophobic.exe)

ZIP="dist/Syntexophobic-$VERSION-windows-x64.zip"
rm -f "$ZIP"
(cd build/windows && 7z a -bd -tzip -mx=7 "../../$ZIP" Syntexophobic >/dev/null)
echo "$ZIP ($(du -h "$ZIP" | cut -f1))"
