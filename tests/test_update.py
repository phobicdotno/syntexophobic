import io
import os
import zipfile

import pytest

from syntexophobic import update

ASSET_SUFFIX = update.asset_suffix


@pytest.fixture(autouse=True)
def on_linux(monkeypatch):
    """The tests read releases as the Linux program does, whatever they run on."""
    monkeypatch.setattr(update, "asset_suffix", lambda *a: ".AppImage")

NOTES_070 = "**CAM**\n- Faster roughing\n\n**Testing**\n- more\n"
NOTES_061 = "**Fixes**\n- A crash\n"


def gh(tag, body="", draft=False, prerelease=False, asset=True):
    v = tag.lstrip("v")
    assets = [{"name": f"Syntexophobic-{v}-macOS-arm64.zip", "url": f"https://api/assets/{v}.zip", "size": 9},
              {"name": f"Syntexophobic-{v}-windows-x64.zip", "url": f"https://api/assets/{v}-win.zip", "size": 8},
              {"name": f"Syntexophobic-{v}-x86_64.AppImage", "url": f"https://api/assets/{v}", "size": 4}] if asset else []
    return {"tag_name": tag, "name": f"Syntexophobic {v}", "body": body, "html_url": f"https://gh/{tag}",
            "draft": draft, "prerelease": prerelease, "assets": assets}


DATA = [gh("v0.6.1", NOTES_061), gh("v0.7.0", NOTES_070), gh("v0.8.0", draft=True),
        gh("v0.9.0-rc1"), gh("v0.7.1", prerelease=True), gh("v0.6.0"), gh("nightly")]


def test_versions_compare_as_numbers():
    assert update.newer("0.10.0", "0.9.1") and update.newer("v1.0", "0.99.99") and update.newer("0.6.1", "0.6")
    assert not update.newer("0.6.0", "0.6.0") and not update.newer("0.6", "0.6.0")
    assert not update.newer("nightly", "0.1") and not update.newer("0.1", "garbage")


def test_check_lists_newer_published_releases_newest_first():
    up = update.check("0.6.0", fetch=lambda tok: DATA, tok="t")
    assert [r.version for r in up.releases] == ["0.7.0", "0.6.1"]       # no drafts, pre-releases, odd tags
    assert up.latest.asset == "Syntexophobic-0.7.0-x86_64.AppImage" and up.latest.asset_size == 4
    assert up.areas() == ["CAM", "Fixes"]                                 # Testing left out
    assert up.notes().index("Syntexophobic 0.7.0") < up.notes().index("Syntexophobic 0.6.1")
    assert update.check("0.7.0", fetch=lambda tok: DATA, tok="t") is None
    assert update.check("0.7.0", fetch=lambda tok: [], tok="t") is None


def test_each_platform_gets_its_own_build():
    assert ASSET_SUFFIX("linux") == ".AppImage"
    assert ASSET_SUFFIX("darwin", "arm64") == "-macOS-arm64.zip"
    mac = update.releases(DATA, ASSET_SUFFIX("darwin", "arm64"))[0]
    assert mac.asset == "Syntexophobic-0.7.0-macOS-arm64.zip" and mac.asset_url.endswith(".zip")
    assert mac.asset_size == 9 and mac.blocks_url == ""
    assert update.releases(DATA, ASSET_SUFFIX("darwin", "x86_64"))[0].asset == ""   # no Intel build
    assert ASSET_SUFFIX("win32") == "-windows-x64.zip"
    assert update.releases(DATA, ASSET_SUFFIX("win32"))[0].asset == "Syntexophobic-0.7.0-windows-x64.zip"


def test_check_passes_the_token_and_reports_no_access(monkeypatch):
    monkeypatch.setenv("SYNTEXOPHOBIC_GITHUB_TOKEN", "secret")
    seen = []
    update.check("0.6.0", fetch=lambda tok: seen.append(tok) or DATA)
    assert seen == ["secret"]

    def denied(url, tok, accept=None):
        raise update.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(update, "_request", denied)
    with pytest.raises(update.UpdateError, match="no access"):
        update.check("0.6.0")


def test_install_downloads_next_to_the_appimage_and_moves_the_link(tmp_path, monkeypatch):
    apps = tmp_path / "Applications"
    apps.mkdir()
    old = apps / "Syntexophobic-0.6.0-x86_64.AppImage"
    old.write_text("old")
    link = apps / "Syntexophobic.AppImage"
    link.symlink_to(old.name)                      # relative, as `ln -s` in that folder makes it
    monkeypatch.setattr(update, "LINK", link)
    got = []

    def fetch(url, dest, tok, size, progress):
        got.append((url, tok))
        dest.write_text("new")
    up = update.check("0.6.0", fetch=lambda tok: DATA, tok="t")
    done = update.install(up.latest, old, tok="t", fetch=fetch)
    assert got == [("https://api/assets/0.7.0", "t")]
    assert done.path == apps / "Syntexophobic-0.7.0-x86_64.AppImage" and done.links == [link] and done.start == link
    assert os.readlink(link) == "Syntexophobic-0.7.0-x86_64.AppImage" and link.read_text() == "new"
    assert old.read_text() == "old"                # kept: pointing the link back undoes the update
    assert not list(apps.glob("*.new"))

    with pytest.raises(update.UpdateError, match="no AppImage"):
        update.install(update.releases([gh("v0.8.0", asset=False)])[0], old, tok="t", fetch=fetch)


def test_install_without_a_link_leaves_the_new_file_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "LINK", tmp_path / "nowhere" / "Syntexophobic.AppImage")
    old = tmp_path / "Syntexophobic-0.6.0-x86_64.AppImage"
    old.write_text("old")
    up = update.check("0.6.0", fetch=lambda tok: DATA, tok="t")
    done = update.install(up.latest, old, tok="t", fetch=lambda url, dest, *a: dest.write_text("new"))
    assert done.links == [] and done.start == done.path == tmp_path / up.latest.asset


def _app(folder, text):
    (folder / "Syntexophobic.app/Contents/MacOS").mkdir(parents=True)
    (folder / "Syntexophobic.app/Contents/MacOS/Syntexophobic").write_text(text)
    return folder / "Syntexophobic.app"


def test_install_app_puts_the_new_macos_app_in_place_and_keeps_the_old(tmp_path, monkeypatch):
    monkeypatch.setattr("syntexophobic.about.version", lambda: "0.6.0")
    apps, build = tmp_path / "Applications", tmp_path / "build"
    apps.mkdir()
    build.mkdir()
    current = _app(apps, "old")
    _app(build, "new")

    def fetch(url, dest, tok, size, progress):
        assert url == "https://api/assets/0.7.0.zip" and tok == "t"
        with zipfile.ZipFile(dest, "w") as z:          # as `ditto -c -k --keepParent` lays it out
            z.write(build / "Syntexophobic.app/Contents/MacOS/Syntexophobic",
                    "Syntexophobic.app/Contents/MacOS/Syntexophobic")
        progress(1.0)

    def unpack(archive, dest):
        zipfile.ZipFile(archive).extractall(dest)
    release = update.releases(DATA, ASSET_SUFFIX("darwin", "arm64"))[0]
    steps = []
    done = update.install_app(release, current, "t", lambda f, what: steps.append(what), fetch, unpack,
                              previous=tmp_path / "previous")
    assert done.path == done.start == current.resolve() and done.links == []
    assert (current / "Contents/MacOS/Syntexophobic").read_text() == "new"
    kept = tmp_path / "previous/Syntexophobic-0.6.0.app/Contents/MacOS/Syntexophobic"
    assert kept.read_text() == "old"                   # the old app, to put back
    assert sorted(p.name for p in apps.iterdir()) == ["Syntexophobic.app"]   # no download left behind
    assert steps == ["Downloading", "Unpacking"]

    with pytest.raises(update.UpdateError, match="holds no app"):
        update.install_app(release, current, "t", None, lambda url, dest, *a: dest.write_text("x"),
                           lambda archive, dest: dest.mkdir(), previous=tmp_path / "previous")
    assert (current / "Contents/MacOS/Syntexophobic").read_text() == "new"
    assert sorted(p.name for p in apps.iterdir()) == ["Syntexophobic.app"]

    moved = _app(tmp_path / "private/var/folders/x/AppTranslocation/1234/d", "old")
    with pytest.raises(update.UpdateError, match="move Syntexophobic.app to Applications"):
        update.install_app(release, moved, "t", fetch=fetch, unpack=unpack)


def test_a_cut_download_leaves_nothing(tmp_path, monkeypatch):
    class Body(io.BytesIO):
        headers = {"Content-Length": "10"}
    monkeypatch.setattr(update, "_request", lambda *a: Body(b"12345"))
    dest = tmp_path / "new.AppImage"
    with pytest.raises(update.UpdateError, match="5 of 10"):
        update.download("u", dest, "t", 10)
    assert list(tmp_path.iterdir()) == []
    seen = []
    monkeypatch.setattr(update, "_request", lambda *a: Body(b"1234567890"))
    update.download("u", dest, "t", 10, seen.append)
    assert dest.read_bytes() == b"1234567890" and os.access(dest, os.X_OK) and seen[-1] == 1.0


def test_update_bar(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings, QThreadPool
    from PySide6.QtWidgets import QApplication
    from syntexophobic import update_ui
    app = QApplication.instance()                              # made by conftest's qt_app
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    QSettings.setDefaultFormat(QSettings.NativeFormat)
    app.setOrganizationName("syntexophobic-tests")
    QSettings().remove(update_ui.SKIP_KEY)
    pool = QThreadPool()

    def settle():
        pool.waitForDone()
        for _ in range(5):
            app.processEvents()

    bar = update_ui.UpdateBar(pool=pool)
    fake = lambda: update.check("0.6.0", fetch=lambda tok: DATA, tok="t")  # noqa: E731
    bar.check(check=lambda: None)                      # up to date: stays hidden, says nothing
    settle()
    assert bar.isHidden()
    bar.check(check=fake)
    settle()
    assert not bar.isHidden() and "0.7.0 is available" in bar.text.text() and "CAM · Fixes" in bar.text.text()

    bar.dismiss()                                      # ✕: hidden, and the periodic check leaves it so
    bar.check(check=fake)
    settle()
    assert bar.isHidden()
    bar.check(check=fake, quiet=False)                 # asked for: shown again
    settle()
    assert not bar.isHidden()
    bar.skip()
    assert QSettings().value(update_ui.SKIP_KEY) == "0.7.0"
    fresh = update_ui.UpdateBar(pool=pool)             # next start: the skipped version stays quiet
    fresh.check(check=fake)
    settle()
    assert fresh.isHidden()

    appimage = tmp_path / "Syntexophobic-0.6.0-x86_64.AppImage"
    appimage.write_text("old")
    monkeypatch.setattr(update, "running_appimage", lambda: appimage)

    def install(release, current, progress):
        progress(0.5, "Downloading")
        return update.Installed(tmp_path / release.asset, [tmp_path / "Syntexophobic.AppImage"],
                                tmp_path / "Syntexophobic.AppImage")
    bar.show_update(fake())
    bar.install(install=install)
    settle()
    assert "0.7.0 is installed" in bar.text.text() and "Restart to use it" in bar.text.text()
    assert bar.b_update.isHidden() and not bar.b_restart.isHidden()
    bar.check(check=fake)                              # a later check keeps the restart prompt
    settle()
    assert "is installed" in bar.text.text()
