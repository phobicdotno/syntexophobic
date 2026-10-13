"""3Dconnexion SpaceMouse / SpaceNavigator input through spacenavd.

spacenavd (the Linux daemon for every 3Dconnexion device) serves a Unix socket. With its
original protocol, which it uses for clients that do not ask for another, every event is
eight 32-bit ints:

    motion:  0, x, y, z, rx, ry, rz, period_ms
    button:  1 (press) or 2 (release), button number, 0, 0, 0, 0, 0, 0

Axes follow the device: x right, y up (lift the cap), z towards you; rx, ry, rz are the
tilts and twist about those axes. No library is needed, so this works wherever the
daemon runs. When the daemon is missing the client stays idle and retries now and then.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

SOCKETS = ("/run/spnav.sock", "/var/run/spnav.sock")
EVENT = struct.Struct("=8i")
MOTION, PRESS, RELEASE = 0, 1, 2


@dataclass
class Motion:
    x: int
    y: int
    z: int
    rx: int
    ry: int
    rz: int
    period: int


def parse(packet: bytes):
    """One 32-byte event -> Motion, ("button", number, pressed) or None."""
    kind, *v = EVENT.unpack(packet)
    if kind == MOTION:
        return Motion(*v[:7])
    if kind in (PRESS, RELEASE):
        return ("button", v[0], kind == PRESS)
    return None


class SpaceMouse(QObject):
    motion = Signal(object)            # Motion
    button = Signal(int, bool)         # number, pressed
    connected = Signal(bool)

    def __init__(self, paths=SOCKETS, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.sock: socket.socket | None = None
        self.notifier: QSocketNotifier | None = None
        self._buf = b""
        self._retry = QTimer(self)
        self._retry.setInterval(5000)
        self._retry.timeout.connect(self.connect_daemon)
        self.connect_daemon()

    @property
    def is_connected(self) -> bool:
        return self.sock is not None

    def connect_daemon(self) -> bool:
        if self.sock:
            return True
        if not hasattr(socket, "AF_UNIX"):     # Windows: no spacenavd, nothing to connect to
            return False
        for path in self.paths:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
            except OSError:
                continue
            s.setblocking(False)
            self.sock = s
            self.notifier = QSocketNotifier(s.fileno(), QSocketNotifier.Read, self)
            self.notifier.activated.connect(self._readable)
            self._retry.stop()
            self.connected.emit(True)
            return True
        self._retry.start()
        return False

    def _readable(self, *_):
        try:
            data = self.sock.recv(EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:                    # daemon went away: wait for it to come back
            self._drop()
            return
        self._buf += data
        latest = None
        while len(self._buf) >= EVENT.size:
            ev = parse(self._buf[:EVENT.size])
            self._buf = self._buf[EVENT.size:]
            if isinstance(ev, Motion):
                latest = ev             # motion is a state: only the newest one matters
            elif ev:
                self.button.emit(ev[1], ev[2])
        if latest:
            self.motion.emit(latest)

    def _drop(self):
        if self.notifier:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
        if self.sock:
            self.sock.close()
        self.sock, self.notifier, self._buf = None, None, b""
        self.connected.emit(False)
        self._retry.start()
