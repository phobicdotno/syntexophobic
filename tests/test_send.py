"""Sending a project to phobiCCCP: discovery, the transfer and what arrives (against a stand-in
for phobiCCCP's receiver)."""

import io
import json
import socket
import struct
import threading
import zipfile

import ezdxf
import pytest

from syntexophobic import send
from syntexophobic.model import Feature, Part
from syntexophobic.project import Project
from syntexophobic.sketch import Sketch


class FakeReceiver:
    """phobiCCCP's side of the link on a free port: records what it is sent and answers."""

    def __init__(self, answer=b"OK opened bracket.c2d\n"):
        self.answer = answer
        self.tcp = socket.create_server(("127.0.0.1", 0))
        self.port = self.tcp.getsockname()[1]
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", self.port))
        self.manifest, self.files = None, {}
        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._discovery, daemon=True).start()

    def _discovery(self):
        while True:
            data, addr = self.udp.recvfrom(4096)
            if data == send.DISCOVER:
                self.udp.sendto(json.dumps({"app": "phobiCCCP", "version": "1.2.0", "host": "shop-pc",
                                            "port": self.port}).encode(), addr)

    def _serve(self):
        conn, _ = self.tcp.accept()
        f = conn.makefile("rb")
        assert f.readline() == send.HELLO
        self.manifest = json.loads(f.readline())
        for entry in self.manifest["files"]:
            self.files[entry["name"]] = f.read(entry["size"])
        conn.sendall(self.answer)
        conn.close()


def _stl_bounds(data: bytes):
    n = struct.unpack_from("<I", data, 80)[0]
    pts = [struct.unpack_from("<3f", data, 84 + i * 50 + 12 + v * 12) for i in range(n) for v in range(3)]
    return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]


@pytest.fixture
def design(tmp_path):
    p = Project.create_design(tmp_path / "bracket", "bracket")
    base, text = Sketch(), Sketch()
    base.rectangle(0, 0, 50, 30)
    text.rectangle(10, 10, 20, 20)
    Part("bracket", [Feature("extrude", "Base", sketch="base", distance=10)],
         sketches={"base": base, "logo": text}).save(p.folder)
    p.history.save("Base")
    return Project(p.folder)


def test_discovery_finds_a_listening_phobicccp():
    r = FakeReceiver()
    peers = send.discover(timeout=0.5, port=r.port)
    assert [(p.name, p.host, p.port, p.version) for p in peers] == [("shop-pc", "127.0.0.1", r.port, "1.2.0")]


def test_parse_target():
    assert send.parse_target("shop-pc") == ("shop-pc", send.PORT)
    assert send.parse_target("shop-pc.tail1234.ts.net:5000") == ("shop-pc.tail1234.ts.net", 5000)
    assert send.parse_target("100.101.102.103") == ("100.101.102.103", send.PORT)
    assert send.parse_target("[fd7a:115c::1]:47000") == ("fd7a:115c::1", 47000)
    for bad in (" ", "shop-pc:70000", "shop-pc:0", "[::1]:99999"):
        with pytest.raises(send.SendError):
            send.parse_target(bad)


def test_send_a_design(design, tmp_path):
    r = FakeReceiver()
    assert send.send_project(design, f"127.0.0.1:{r.port}") == "opened bracket.c2d"
    m = r.manifest
    st = design.setup().stock
    assert m["name"] == "bracket"
    assert m["stock"] == pytest.approx({"width": st.xmax - st.xmin, "height": st.ymax - st.ymin,
                                        "thickness": st.zmax - st.zmin})
    roles = {e["name"]: e["role"] for e in m["files"]}
    assert roles == {"part.stl": "model", "base.dxf": "vectors", "logo.dxf": "vectors", "bracket.cccp": "project"}

    # the part in stock coordinates: front-left corner X0 Y0, stock top Z0
    lo, hi = _stl_bounds(r.files["part.stl"])
    model = next(e for e in m["files"] if e["role"] == "model")
    assert lo[:2] == pytest.approx([model["x"], model["y"]], abs=1e-3)
    assert hi[0] - lo[0] == pytest.approx(50, abs=1e-3) and hi[1] - lo[1] == pytest.approx(30, abs=1e-3)
    assert hi[2] == pytest.approx(model["top"], abs=1e-3) and model["top"] < 0   # stock has top_offset

    # sketches moved the same way
    (tmp_path / "logo.dxf").write_bytes(r.files["logo.dxf"])     # CRLF on Windows: read it as a file
    doc = ezdxf.readfile(str(tmp_path / "logo.dxf"))
    xs = [e.dxf.start.x for e in doc.modelspace().query("LINE")]
    assert min(xs) == pytest.approx(10 - st.xmin)

    # the project with its history
    names = zipfile.ZipFile(io.BytesIO(r.files["bracket.cccp"])).namelist()
    assert "job.toml" in names and "model.toml" in names and "sketches/logo.toml" in names
    assert any(n.startswith(".git/") for n in names)


def test_refused(design):
    r = FakeReceiver(b"ERR declined on shop-pc\n")
    with pytest.raises(send.SendError, match="declined"):
        send.send_project(design, f"127.0.0.1:{r.port}")


def test_nobody_listening(design):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(send.SendError, match="Could not reach"):
        send.send_project(design, f"127.0.0.1:{port}")


def test_dialog_lists_what_it_finds(monkeypatch):
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QWidget

    monkeypatch.setattr(send, "discover", lambda: [send.Peer("10.0.0.7", send.PORT, "shop-pc", "1.2.0")])
    window = QWidget()
    window.pool, window.project = QThreadPool.globalInstance(), None
    dlg = send.dialog(window)
    window.pool.waitForDone(5000)
    QApplication.processEvents()
    found = dlg.findChild(QListWidget)
    assert found.count() == 1 and "shop-pc" in found.item(0).text()
    found.setCurrentRow(0)
    assert dlg.findChild(QLineEdit).text() == "10.0.0.7"


def test_discovery_goes_on_after_windows_reports_an_unread_packet(monkeypatch):
    answer = json.dumps({"app": "phobiCCCP", "host": "shop-pc", "port": send.PORT}).encode()

    class Sock:
        def __init__(self, *a):
            self.replies = [ConnectionResetError(10054, "reset"), (answer, ("10.0.0.5", send.PORT))]

        def setsockopt(self, *a): pass
        def ioctl(self, *a): pass
        def settimeout(self, t): pass
        def sendto(self, *a): pass
        def close(self): pass

        def recvfrom(self, n):
            if not self.replies:
                raise TimeoutError
            r = self.replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    monkeypatch.setattr(send.socket, "socket", Sock)
    assert [p.name for p in send.discover()] == ["shop-pc"]
