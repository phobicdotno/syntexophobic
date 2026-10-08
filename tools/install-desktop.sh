#!/bin/sh
# Adds Syntexophobic to the desktop app menu for the current user (no root needed).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$ROOT/src/syntexophobic/assets/syntexophobic.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/syntexophobic.svg"
cat > "$HOME/.local/share/applications/syntexophobic.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Syntexophobic
GenericName=CAM
Comment=2.5D CAM for GRBL machines with git-style history
Exec=$UV run --project $ROOT syntexophobic %f
Icon=syntexophobic
Terminal=false
Categories=Graphics;Engineering;
MimeType=model/step;application/step;
StartupWMClass=syntexophobic
DESKTOP
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed: Syntexophobic is in the app menu."
