#!/usr/bin/env bash
# Builds dist/Syntexophobic-<version>-x86_64.AppImage: one file with its own Python and
# every library. Run it, or make it executable and double-click. Needs uv and appimagetool.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-$(command -v uv || echo ~/.local/bin/uv)}
TOOL=${APPIMAGETOOL:-$(command -v appimagetool || echo ~/.local/bin/appimagetool)}
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
APPDIR=build/AppDir
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr" dist

# a relocatable Python (python-build-standalone, as uv installs it)
"$UV" python install 3.12 >/dev/null
PY=$("$UV" python find 3.12)
cp -a "$(dirname "$(dirname "$(readlink -f "$PY")")")" "$APPDIR/usr/python"
rm -f "$APPDIR"/usr/python/lib/python3.12/EXTERNALLY-MANAGED

"$UV" build --wheel -o build/wheel >/dev/null
"$UV" pip install --python "$APPDIR/usr/python/bin/python3" build/wheel/syntexophobic-"$VERSION"-*.whl
# trim what the program never uses: Qt's web engine, QML, multimedia, most of VTK...
"$APPDIR/usr/python/bin/python3" tools/appimage_prune.py "$APPDIR"
# Qt's xcb plugin needs the xcb-util and xkb libraries, which many desktops lack (libxcb-cursor0 above
# all); they go next to libQt6XcbQpa, whose RUNPATH is $ORIGIN, so the bundled copies win
QTLIB="$APPDIR"/usr/python/lib/python3.12/site-packages/PySide6/Qt/lib
for lib in libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-image.so.0 libxcb-keysyms.so.1 \
           libxcb-render-util.so.0 libxcb-util.so.1 libxcb-xkb.so.1 libxkbcommon-x11.so.0; do
    src=$(/sbin/ldconfig -p | awk -v l="$lib" '$1 == l && /x86-64/ {print $NF; exit}')
    [ -n "$src" ] || { echo "$lib not found on this machine; install its libxcb-* package" >&2; exit 1; }
    cp -L "$src" "$QTLIB/$lib"
done
# git, for project history, on desktops that have none (Ubuntu ships without it). Every git
# command the app runs is built into the one binary; it needs only libc, zlib and pcre2.
mkdir -p "$APPDIR/usr/git"
cp -L "$(command -v git)" "$APPDIR/usr/git/git"
find "$APPDIR/usr/python" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$APPDIR"/usr/python/lib/python3.12/{test,idlelib,tkinter,turtledemo} \
       "$APPDIR"/usr/python/lib/python3.12/site-packages/{pip,setuptools}* \
       "$APPDIR"/usr/python/lib/python3.12/site-packages/PySide6/{Qt/qml,Qt/translations,qml,examples,glue,include,typesystems} \
       "$APPDIR"/usr/python/lib/python3.12/site-packages/PySide6/{assistant,designer,linguist}*

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}         # VTK's OpenGL view needs X11/XWayland
# the bundled git only when the system has none, so a configured system git keeps winning
command -v git >/dev/null 2>&1 || { export PATH="$HERE/usr/git:$PATH"; export GIT_EXEC_PATH="$HERE/usr/git"; }
export NUMBA_CACHE_DIR=${NUMBA_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/syntexophobic/numba}
exec "$HERE/usr/python/bin/python3" -m syntexophobic "$@"
RUN
chmod +x "$APPDIR/AppRun"
cp src/syntexophobic/assets/syntexophobic.svg "$APPDIR/syntexophobic.svg"
# .DirIcon: the icon file managers and AppImage launchers show for the .AppImage file itself
QT_QPA_PLATFORM=offscreen "$APPDIR/usr/python/bin/python3" -c "
import sys; from PySide6.QtGui import QGuiApplication; from syntexophobic import desktop
app = QGuiApplication([]); open(sys.argv[1], 'wb').write(desktop.png(256))" "$APPDIR/.DirIcon"
cat > "$APPDIR/syntexophobic.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Syntexophobic
Comment=Parametric CAD and CAM for CNC routers
Exec=syntexophobic %f
Icon=syntexophobic
StartupWMClass=Syntexophobic
Categories=Graphics;Engineering;
Terminal=false
DESK

OUT="dist/Syntexophobic-$VERSION-x86_64.AppImage"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --comp zstd "$APPDIR" "$OUT" >/dev/null
echo "$OUT ($(du -h "$OUT" | cut -f1))"
