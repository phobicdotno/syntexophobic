#!/usr/bin/env bash
# CI only: GitHub's Windows runners have no GPU, and Windows' own OpenGL is version 1.1, so
# VTK's view crashes there. Mesa's software OpenGL (llvmpipe), put next to the python.exe that
# runs, stands in for it. Never part of a build. Usage: ci-mesa.sh <folder of python.exe>
set -euo pipefail
dest=$1
tmp=$(mktemp -d)
gh release download --repo pal1000/mesa-dist-win --pattern 'mesa3d-*-release-msvc.7z' --dir "$tmp"
7z e -bd -y -o"$tmp/x64" "$tmp"/mesa3d-*.7z 'x64/*.dll' >/dev/null
for dll in opengl32.dll libgallium_wgl.dll libglapi.dll; do
    if [ -f "$tmp/x64/$dll" ]; then cp "$tmp/x64/$dll" "$dest/"; fi
done
ls "$dest"/opengl32.dll
