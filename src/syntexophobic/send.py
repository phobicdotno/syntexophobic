"""Send a project to phobiCCCP running on another computer (or this one), over the local
network or Tailscale, for machining there.

The link, version 1 (phobiCCCP's side is src/projectreceiver.cpp):

- Discovery: a UDP datagram ``PHOBICCCP-DISCOVER 1`` to port 47652 (broadcast); every
  phobiCCCP that is listening answers with one JSON object
  ``{"app": "phobiCCCP", "version": ..., "host": ..., "port": 47652}``. Broadcasts do not
  cross Tailscale, so there the host is typed by name or address instead.
- Transfer: a TCP connection to that port, ``PHOBICCCP-SEND 1\\n``, one line of JSON (the
  manifest, below), then the bytes of every file in the manifest's order. phobiCCCP asks
  its user whether to open it and answers one line: ``OK <what it did>`` or ``ERR <why>``.

The manifest: ``name``, ``from`` (this computer), ``app``, ``stock`` (``width`` X,
``height`` Y, ``thickness`` Z in mm) and ``files``: ``{"name", "size", "role"}`` with role
``model`` (the part as a binary STL in stock coordinates: X0 Y0 the stock's front-left
corner, Z0 its top; ``x``/``y`` say where the mesh's lower-left corner sits), ``vectors``
(a DXF of an XY sketch, same coordinates) or ``project`` (the whole project folder,
history included, as a .cccp zip).
"""

from __future__ import annotations

import io
import json
import re
import socket
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

PORT = 47652
DISCOVER = b"PHOBICCCP-DISCOVER 1"
HELLO = b"PHOBICCCP-SEND 1\n"


class SendError(Exception):
    pass


@dataclass
class Peer:
    host: str          # address the answer came from
    port: int
    name: str          # the computer's name, as phobiCCCP reports it
    version: str = ""

    @property
    def target(self) -> str:
        return self.host if self.port == PORT else f"{self.host}:{self.port}"


def discover(timeout: float = 0.8, port: int = PORT) -> list[Peer]:
    """phobiCCCPs listening on this computer and the local network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    found: dict[tuple[str, int], Peer] = {}
    try:
        for dest in ("255.255.255.255", "127.0.0.1"):
            try:
                s.sendto(DISCOVER, (dest, port))
            except OSError:
                pass                       # no broadcast route (offline): try the next
        while True:
            try:
                data, (addr, _) = s.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            try:
                d = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(d, dict) or d.get("app") != "phobiCCCP":
                continue
            p = Peer(addr, int(d.get("port", port)), str(d.get("host") or addr), str(d.get("version", "")))
            found[(p.host, p.port)] = p
    finally:
        s.close()
    return sorted(found.values(), key=lambda p: (p.name, p.host))


def parse_target(text: str) -> tuple[str, int]:
    """``host``, ``host:port`` or ``[v6 address]:port`` → (host, port)."""
    t = text.strip()
    if not t:
        raise SendError("No computer given")
    m = re.fullmatch(r"\[([^\]]+)\](?::(\d+))?", t)
    if m:
        return m.group(1), int(m.group(2) or PORT)
    if t.count(":") == 1:
        host, port = t.split(":")
        if not port.isdigit():
            raise SendError(f"Not a port number: {port!r}")
        return host, int(port)
    return t, PORT                          # a name, an IPv4 or a bare IPv6 address


def _safe(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return s[:80] or "project"


def pack_project(folder: str | Path) -> bytes:
    """The project folder, .git included, as a .cccp zip (paths relative to the folder)."""
    folder = Path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix not in (".nc", ".gcode", ".tap"):
                z.write(f, f.relative_to(folder).as_posix())
    return buf.getvalue()


def _stl_bytes(shape, dx: float, dy: float, dz: float, deflection: float = 0.02) -> bytes:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.StlAPI import StlAPI_Writer
    from OCP.gp import gp_Trsf, gp_Vec
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    moved = BRepBuilderAPI_Transform(shape, t, True).Shape()
    BRepMesh_IncrementalMesh(moved, deflection, False, 0.2, True)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "part.stl"
        w = StlAPI_Writer()
        w.ASCIIMode = False
        if not w.Write(moved, str(path)):
            raise SendError("Could not write the part as STL")
        return path.read_bytes()


def _sketch_dxfs(project, dx: float, dy: float) -> list[tuple[str, bytes]]:
    """Every sketch on the XY plane, moved into stock coordinates, as DXF."""
    if not project.job.model.endswith(".toml") or project.job.stock.up != "+Z":
        return []
    import ezdxf
    from .dxf import export_dxf
    from .model import Part
    out = []
    with tempfile.TemporaryDirectory() as d:
        for name, sk in Part.load(project.folder / project.job.model).sketches.items():
            if sk.plane != "XY":
                continue
            path = Path(d) / "s.dxf"
            export_dxf(sk, path)
            doc = ezdxf.readfile(str(path))
            entities = list(doc.modelspace())
            if not entities:
                continue
            for e in entities:
                e.translate(dx, dy, 0)
            doc.saveas(str(path))
            out.append((f"{_safe(name)}.dxf", path.read_bytes()))
    return out


def package(project) -> tuple[dict, list[tuple[str, bytes]]]:
    """The manifest and the files to send for a project."""
    from . import geometry
    from .about import version
    s = project.setup().stock
    b = geometry.bounding_box(project.shape)
    name = _safe(project.job.name or project.folder.name)
    model = _stl_bytes(project.shape, -s.xmin, -s.ymin, -s.zmax)
    files = [("part.stl", model, {"role": "model", "x": round(b.xmin - s.xmin, 4), "y": round(b.ymin - s.ymin, 4),
                                  "top": round(b.zmax - s.zmax, 4)})]
    files += [(n, data, {"role": "vectors"}) for n, data in _sketch_dxfs(project, -s.xmin, -s.ymin)]
    files.append((f"{name}.cccp", pack_project(project.folder), {"role": "project"}))
    manifest = {
        "name": name,
        "from": socket.gethostname(),
        "app": f"Syntexophobic {version()}",
        "stock": {"width": round(s.xmax - s.xmin, 4), "height": round(s.ymax - s.ymin, 4),
                  "thickness": round(s.zmax - s.zmin, 4)},
        "files": [{"name": n, "size": len(data), **extra} for n, data, extra in files],
    }
    return manifest, [(n, data) for n, data, _ in files]


def send(host: str, port: int, manifest: dict, files: list[tuple[str, bytes]],
         connect_timeout: float = 5.0, answer_timeout: float = 300.0) -> str:
    """Send, then wait for phobiCCCP's answer (its user is asked first). Returns what
    phobiCCCP says it did; raises SendError when it could not be reached or said no."""
    try:
        conn = socket.create_connection((host, port), timeout=connect_timeout)
    except OSError as e:
        raise SendError(f"Could not reach phobiCCCP at {host}:{port}: {e.strerror or e}. "
                        "Is it running, with receiving turned on?") from None
    with conn:
        try:
            conn.settimeout(60)
            conn.sendall(HELLO + json.dumps(manifest, separators=(",", ":")).encode() + b"\n")
            for _, data in files:
                conn.sendall(data)
            conn.settimeout(answer_timeout)
            answer = b""
            while not answer.endswith(b"\n") and len(answer) < 4096:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                answer += chunk
        except socket.timeout:
            raise SendError("phobiCCCP did not answer (nobody accepted the project in time)") from None
        except OSError as e:
            raise SendError(f"The connection to {host}:{port} broke: {e.strerror or e}") from None
    line = answer.decode("utf-8", "replace").strip()
    if line.startswith("OK"):
        return line[2:].strip() or "received"
    if line.startswith("ERR"):
        raise SendError(f"phobiCCCP: {line[3:].strip()}")
    raise SendError("phobiCCCP closed the connection without an answer")


def send_project(project, target: str) -> str:
    host, port = parse_target(target)
    manifest, files = package(project)
    return send(host, port, manifest, files)


def dialog(window):
    """File > Send to phobiCCCP…: pick a phobiCCCP found on the network, or type a host."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                   QListWidgetItem, QPushButton, QVBoxLayout)
    from .app import Task

    dlg = QDialog(window)
    dlg.setWindowTitle("Send to phobiCCCP")
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("Sends the part, its XY sketches and the whole project (with history) to "
                         "phobiCCCP, which opens it as a new design. On the local network it is found "
                         "below; over Tailscale, type the computer's Tailscale name or address."))
    found = QListWidget()
    lay.addWidget(found)
    row = QHBoxLayout()
    target = QLineEdit(QSettings().value("send/target", "", type=str))
    target.setPlaceholderText(f"computer name or address[:port]  (port {PORT})")
    refresh = QPushButton("Search again")
    row.addWidget(target, 1)
    row.addWidget(refresh)
    lay.addLayout(row)
    status = QLabel("")
    status.setWordWrap(True)
    lay.addWidget(status)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    b_send = buttons.addButton("Send", QDialogButtonBox.AcceptRole)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    found.currentItemChanged.connect(lambda item, _: item and target.setText(item.data(256)))
    tasks = []                              # keep the runnables' signal objects alive

    def run(fn, done, failed):
        t = Task(fn)
        t.setAutoDelete(False)
        tasks.append(t)
        t.signals.done.connect(done)
        t.signals.failed.connect(failed)
        window.pool.start(t)

    def search():
        refresh.setEnabled(False)
        status.setText("Looking for phobiCCCP on the network…")

        def done(peers):
            refresh.setEnabled(True)
            found.clear()
            for p in peers:
                item = QListWidgetItem(f"{p.name}  ({p.host}{'' if p.port == PORT else f':{p.port}'})"
                                       + (f"  phobiCCCP {p.version}" if p.version else ""))
                item.setData(256, p.target)
                found.addItem(item)
            status.setText(f"Found {len(peers)} phobiCCCP." if peers else
                           "None found here. Type the computer's name or Tailscale address.")

        run(discover, done, lambda tb: (refresh.setEnabled(True), status.setText("Search failed.")))

    def send_now():
        if window.project is None:
            status.setText("Open a project first.")
            return
        text = target.text().strip()
        if not text:
            status.setText("Pick a phobiCCCP or type where it runs.")
            return
        QSettings().setValue("send/target", text)
        b_send.setEnabled(False)
        status.setText(f"Sending to {text}… phobiCCCP asks there whether to open it.")
        project = window.project

        def done(what):
            b_send.setEnabled(True)
            status.setText(f"Sent: phobiCCCP {what}.")

        def failed(tb):
            b_send.setEnabled(True)
            last = tb.strip().splitlines()[-1]
            status.setText(last.split(": ", 1)[1] if ": " in last else last)

        run(lambda: send_project(project, text), done, failed)

    refresh.clicked.connect(search)
    b_send.clicked.connect(send_now)
    search()
    return dlg
