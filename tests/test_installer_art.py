"""The Windows installer's pictures (tools/windows/installer_art.py): drawn at the sizes WiX
stretches its dialog and banner pictures from, as BMP, which is all Windows Installer reads."""

import importlib.util
from pathlib import Path

from PySide6.QtGui import QImage

ART = Path(__file__).resolve().parents[1] / "tools" / "windows" / "installer_art.py"


def test_installer_pictures(tmp_path):
    spec = importlib.util.spec_from_file_location("installer_art", ART)
    art = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(art)
    art.main(str(tmp_path))
    sizes = {}
    for name in ("splash", "banner", "dialog"):
        path = tmp_path / f"{name}.bmp"
        assert path.read_bytes()[:2] == b"BM"
        img = QImage(str(path))
        sizes[name] = (img.width(), img.height())
    assert sizes == {"splash": (493, 312), "banner": (493, 58), "dialog": (493, 312)}
    splash = QImage(str(tmp_path / "splash.bmp"))
    # dark where the installer writes its light text, on the right under the name
    assert splash.pixelColor(400, 200).lightness() < 60
