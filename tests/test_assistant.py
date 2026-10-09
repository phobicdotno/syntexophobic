"""Assistant chat: pictures (pasted, dropped, converted), links read for Claude, and bits
added to the tool library."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from syntexophobic import assistant_ui as U
from syntexophobic import automation as A
from syntexophobic import library
from syntexophobic.job import Tool

PRODUCT = """<html><head><title>Shop</title>
<meta property="og:title" content="1 Flute Spiral End Mill DLC 3.175mm Shank">
<meta name="description" content="Single flute upcut, cutting length 12mm">
<meta property="og:image" content="/img/bit.avif">
<script type="application/ld+json">{"@type":"Product","name":"ignored, og wins"}</script>
</head><body>x</body></html>"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_links_and_page_info():
    text = "I have this in 3.175 - https://www.aliexpress.com/item/1005.html?spm=a&x=1, and (https://a.b/c)."
    assert U.find_links(text) == ["https://www.aliexpress.com/item/1005.html?spm=a&x=1", "https://a.b/c"]
    assert U.short_url(U.find_links(text)[0]) == "https://www.aliexpress.com/item/1005.html"
    shown = U._linked("see https://a.b/c?track=1 <b>")
    assert ">https://a.b/c</a>" in shown and 'href="https://a.b/c?track=1"' in shown and "&lt;b&gt;" in shown
    info = U.page_info(PRODUCT, "https://shop.example/item/1")
    assert info["title"] == "1 Flute Spiral End Mill DLC 3.175mm Shank"
    assert info["description"].startswith("Single flute") and info["image"] == "https://shop.example/img/bit.avif"
    ld = U.page_info('<script type="application/ld+json">[{"@type":"Product","name":"V-bit 60",'
                     '"image":["https://x/v.png"]}]</script>', "https://x/")
    assert ld["title"] == "V-bit 60" and ld["image"] == "https://x/v.png"


def test_unreadable_link_says_so(tmp_path):
    info = U.fetch_link("http://127.0.0.1:9/nothing", tmp_path, timeout=2)
    assert info["error"] and not info["title"]
    note = U.link_note(info)
    assert "could not be read" in note and "127.0.0.1:9/nothing" in note


def test_pictures_are_made_readable(qapp, tmp_path):
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor("orange"))
    png = tmp_path / "in" / "a.png"
    png.parent.mkdir()
    img.save(str(png))
    out = tmp_path / "out"
    assert U.prepare_image(png, out) == out / "a.png"
    assert U.prepare_image(png, out) == out / "a-1.png"          # never overwrites
    bmp = tmp_path / "in" / "b.bmp"
    img.save(str(bmp))
    assert U.prepare_image(bmp, out) == out / "b.png" and QImage(str(out / "b.png")).width() == 40
    PIL = pytest.importorskip("PIL.Image")
    from PIL import features
    if features.check("avif"):                                     # phone and shop pictures
        PIL.new("RGB", (20, 10), "red").save(tmp_path / "in" / "c.avif")
        assert QImage(str(U.prepare_image(tmp_path / "in" / "c.avif", out))).width() == 20
    with pytest.raises(ValueError):
        (tmp_path / "in" / "junk.heic").write_text("no picture")
        U.prepare_image(tmp_path / "in" / "junk.heic", out)


def test_library_add_skips_a_bit_it_has(config):
    s = A.Session()
    n = len(s.library_list()["tools"])
    r = s.library_add(name='1/8" (3.175 mm) 1 flute upcut spiral', diameter=3.175, flutes=1, shape="flat")
    assert r["added"] and r["library"].startswith(str(config))
    again = s.library_add(name="same bit, other name", diameter=3.175, flutes=1, shape="flat")
    assert not again["added"] and again["tool"]["name"].startswith('1/8"')
    tools = s.library_list()["tools"]
    assert len(tools) == n + 1 and tools[-1]["flutes"] == 1
    assert library.add(Tool(0, "60° V", 6.35, 2, "vbit", 60.0)) is None
    assert library.add(Tool(0, "90° V", 6.35, 2, "vbit", 90.0)) is None       # angle tells V-bits apart
    with pytest.raises(A.CommandError):
        s.library_add(name="bad", diameter=0)
    with pytest.raises(A.CommandError, match="Unknown field"):
        s.library_add(name="bad", diameter=3, flute=1)


def test_library_add_to_job(config):
    s = A.Session()
    s.new_design(str(config / "p"), "p")
    job = s.library_add(name="3 mm downcut", diameter=3.0, flutes=2, add_to_job=True)["job"]
    assert job["tools"][-1]["name"] == "3 mm flat endmill, 2 flute"   # the library had a 3 mm 2 flute flat


class _Win:
    project = None


def test_panel_pastes_pictures_and_links(qapp, config, monkeypatch):
    monkeypatch.setattr(A, "STATE", config / "cache")
    panel = U.AssistantPanel(_Win())
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("steelblue"))
    mime = QMimeData()
    mime.setImageData(img)
    panel.input.insertFromMimeData(mime)                         # Ctrl+V of a copied picture
    assert len(panel.attached) == 1 and panel.attached[0].endswith(".png")
    assert panel.attached[0].startswith(str(config / "cache" / "chat"))
    bmp = config / "drop.bmp"
    img.save(str(bmp))
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile(str(bmp))])                  # a file copied or dropped
    assert panel.input.canInsertFromMimeData(files)
    panel.input.insertFromMimeData(files)
    assert len(panel.attached) == 2 and panel.attached[1].endswith("drop.png")
    text = QMimeData()
    text.setText("https://example.com/bit")
    panel.input.insertFromMimeData(text)                           # links stay text
    assert panel.input.toPlainText() == "https://example.com/bit" and len(panel.attached) == 2

    sent = []
    monkeypatch.setattr(panel, "_launch", lambda t, p, l: sent.append((t, p, l)))
    monkeypatch.setattr(U, "fetch_link", lambda u, f: {"url": u, "title": "", "description": "",
                                                       "image": "", "picture": "", "error": "HTTP 403"})
    panel.send()
    for _ in range(50):
        if sent:
            break
        qapp.processEvents()
        import time
        time.sleep(0.02)
    (t, pics, links), = sent
    assert t == "https://example.com/bit" and len(pics) == 2 and links[0]["error"] == "HTTP 403"
    assert panel.attached == [] and panel.thumbs.isHidden()


def test_panel_shows_bits_claude_added(qapp, config):
    panel = U.AssistantPanel(_Win())
    A.Session().library_add(name="1/8 single flute", diameter=3.175, flutes=1)
    panel.library_changed()
    assert "Added to your tool library" in panel.log.toHtml() and "1/8 single flute" in panel.log.toHtml()
    panel.library_changed()
    assert "already has" in panel.log.toHtml()
