#!/usr/bin/env bash
# CI only: GitHub's Windows runners have no GPU, and Windows' own OpenGL is version 1.1, so
# VTK's view crashes there. Mesa's software OpenGL (llvmpipe), put next to the python.exe that
# runs, stands in for it. Never part of a build. Usage: ci-mesa.sh <folder of python.exe>
set -euo pipefail
dest=$1
tmp=$(mktemp -d)
gh release download --repo pal1000/mesa-dist-win --pattern 'mesa3d-*-release-msvc.7z' --dir "$tmp"
7z x -bd -y -o"$tmp/mesa" "$tmp"/mesa3d-*.7z >/dev/null
# opengl32.dll loads the driver (libgallium_wgl.dll) and its helpers from its own folder
cp "$tmp"/mesa/x64/*.dll "$dest/"
ls "$tmp"/mesa/x64
echo "GALLIUM_DRIVER=llvmpipe" >> "${GITHUB_ENV:-/dev/null}"
