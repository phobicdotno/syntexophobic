import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from syntexophobic import desktop  # noqa: E402

app = QApplication.instance() or QApplication([])


def keys(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_entry_matches_the_window_and_quotes_the_path():
    e = keys(desktop.entry('/home/a b/$x "y"/100%/Syntexophobic.AppImage'))
    assert e["StartupWMClass"] == "Syntexophobic"
    assert e["Icon"] == "syntexophobic"
    assert e["Exec"] == r'"/home/a b/\\$x \\"y\\"/100%%/Syntexophobic.AppImage" %f'


def test_install_writes_entry_and_icons_once_and_follows_a_moved_appimage(tmp_path):
    assert desktop.install("/opt/one.AppImage", tmp_path)
    entry = tmp_path / "applications/syntexophobic.desktop"
    assert '"/opt/one.AppImage" %f' in entry.read_text()
    assert (tmp_path / "icons/hicolor/scalable/apps/syntexophobic.svg").read_bytes() == desktop.SVG.read_bytes()
    img = QImage(str(tmp_path / "icons/hicolor/256x256/apps/syntexophobic.png"))
    assert img.width() == 256 and img.pixelColor(128, 128).alpha() == 255      # drawn, not blank
    assert not desktop.install("/opt/one.AppImage", tmp_path)                 # nothing to do
    assert desktop.install("/opt/two.AppImage", tmp_path)
    assert '"/opt/two.AppImage" %f' in entry.read_text()


def test_hand_made_entry_is_left_alone(tmp_path):
    entry = tmp_path / "applications/syntexophobic.desktop"
    entry.parent.mkdir(parents=True)
    entry.write_text("[Desktop Entry]\nName=Mine\nExec=/somewhere/else\n")
    assert not desktop.install("/opt/one.AppImage", tmp_path)
    assert entry.read_text().startswith("[Desktop Entry]\nName=Mine")


def test_stale_icon_cache_is_not_left_hiding_the_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)          # no cache tool
    cache = tmp_path / "icons/hicolor/icon-theme.cache"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"old")
    desktop.install("/opt/one.AppImage", tmp_path)
    assert not cache.exists()


def test_integrate_only_for_an_appimage(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("APPIMAGE", raising=False)
    desktop.integrate()
    assert not (tmp_path / "applications").exists()                         # running from source
    image = tmp_path / "Syntexophobic.AppImage"
    image.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(image))
    desktop.integrate()
    assert str(image) in (tmp_path / "applications/syntexophobic.desktop").read_text()
