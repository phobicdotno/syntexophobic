#!/usr/bin/env bash
# Builds dist/Syntexophobic-<version>-macOS-<arch>.zip: Syntexophobic.app with its own Python and
# every library, zipped. Run it on a Mac (the release workflow does, on GitHub's macOS runners).
# Needs uv and Apple's command line tools.
#
# The app's Contents folder is the Python prefix: the interpreter is Contents/MacOS/python3,
# so macOS takes the app's name, icon and Info.plist for the running program, and its
# libraries are in Contents/lib, where the interpreter finds them on its own.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-$(command -v uv || echo ~/.local/bin/uv)}
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
ARCH=$(uname -m)
APP=build/Syntexophobic.app
C=$APP/Contents
rm -rf "$APP" && mkdir -p "$C/MacOS" "$C/Resources" dist

# a relocatable Python (python-build-standalone, as uv installs it)
"$UV" python install 3.12 >/dev/null
PY=$("$UV" python find --managed-python 3.12)
PREFIX=$(dirname "$(dirname "$(readlink -f "$PY")")")
cp -a "$PREFIX/lib" "$C/lib"
cp "$(readlink -f "$PREFIX/bin/python3.12")" "$C/MacOS/python3"
rm -f "$C"/lib/python3.12/EXTERNALLY-MANAGED
"$C/MacOS/python3" -c "import sys; assert sys.prefix == '$(cd "$C" && pwd)', sys.prefix"

"$UV" build --wheel -o build/wheel >/dev/null
"$UV" pip install --python "$C/MacOS/python3" build/wheel/syntexophobic-"$VERSION"-*.whl
rm -rf "$C/bin"                                        # console scripts: the launcher below starts it
# trim what the program never uses: Qt's web engine, QML, 3D, most of VTK...
"$C/MacOS/python3" tools/appimage_prune.py "$C"

# git, for project history: macOS has only a stub that asks to install the developer tools.
# Apple's own git is one binary that needs only system libraries; the launcher uses it when
# the developer tools are missing.
GIT=$(xcrun -f git)
if otool -L "$GIT" | tail -n +2 | awk '{print $1}' | grep -qvE '^(/usr/lib/|/System/)'; then
    echo "not bundling git: $GIT needs libraries outside macOS" >&2
else
    mkdir -p "$C/Resources/git" && cp "$GIT" "$C/Resources/git/git"
fi

find "$C" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$C"/lib/python3.12/{test,idlelib,tkinter,turtledemo} "$C"/lib/{tcl,tk,itcl,thread}* \
       "$C"/lib/python3.12/site-packages/{pip,setuptools}* \
       "$C"/lib/python3.12/site-packages/PySide6/{Qt/qml,Qt/translations,qml,examples,glue,include,typesystems} \
       "$C"/lib/python3.12/site-packages/PySide6/{Assistant,Designer,Linguist,assistant,designer,linguist}*

cat > "$C/MacOS/Syntexophobic" <<'RUN'
#!/bin/sh
HERE=$(cd "$(dirname "$0")" && pwd)
CONTENTS=$(dirname "$HERE")
export SYNTEXOPHOBIC_APP=$(dirname "$CONTENTS")        # for the updater and the MCP server command
# the bundled git only without the developer tools, as /usr/bin/git is then just their installer
if ! xcode-select -p >/dev/null 2>&1 && [ -x "$CONTENTS/Resources/git/git" ]; then
    export PATH="$CONTENTS/Resources/git:$PATH" GIT_EXEC_PATH="$CONTENTS/Resources/git"
fi
export NUMBA_CACHE_DIR=${NUMBA_CACHE_DIR:-$HOME/Library/Caches/syntexophobic/numba}
exec "$HERE/python3" -m syntexophobic "$@"
RUN
chmod +x "$C/MacOS/Syntexophobic"

# the icon: every size macOS asks for, drawn from the program's SVG
ICONSET=build/syntexophobic.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
QT_QPA_PLATFORM=offscreen "$C/MacOS/python3" -c "
import sys; from PySide6.QtGui import QGuiApplication; from syntexophobic import desktop
app = QGuiApplication([])
for s in (16, 32, 128, 256, 512):
    open(f'{sys.argv[1]}/icon_{s}x{s}.png', 'wb').write(desktop.png(s))
    open(f'{sys.argv[1]}/icon_{s}x{s}@2x.png', 'wb').write(desktop.png(2 * s))" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$C/Resources/syntexophobic.icns"

cat > "$C/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Syntexophobic</string>
    <key>CFBundleDisplayName</key><string>Syntexophobic</string>
    <key>CFBundleIdentifier</key><string>no.phobic.syntexophobic</string>
    <key>CFBundleExecutable</key><string>Syntexophobic</string>
    <key>CFBundleIconFile</key><string>syntexophobic</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSCameraUsageDescription</key><string>Take photo… sends a picture of a router bit or a part to the assistant.</string>
</dict>
</plist>
PLIST

# every Mach-O file keeps the ad-hoc signature it came with (Apple silicon runs nothing
# unsigned); the app as a whole is not signed, so the first start needs Open Anyway in
# System Settings > Privacy & Security, or `xattr -dr com.apple.quarantine Syntexophobic.app`
OUT="dist/Syntexophobic-$VERSION-macOS-$ARCH.zip"
rm -f "$OUT"
ditto -c -k --keepParent "$APP" "$OUT"
echo "$OUT ($(du -h "$OUT" | cut -f1))"
