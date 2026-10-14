"""Git-style history for a project folder: every save is a commit, variants are branches.

A project is a folder holding job.toml and the model file. The folder is a real git
repository, so `git log` and any git tool see the same history as the app.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .job import JOB_FILE


@dataclass
class Version:
    hash: str
    short: str
    date: str
    message: str
    refs: str


@dataclass
class Station:
    """One version on the subway map: the row it sits on, its lane, and the lines around it."""
    version: Version
    parents: list[str]
    lane: int
    colour: int                                   # index into the map's line colours
    # line pieces from the row above into this row, and from this row down to the next:
    # (from lane, to lane, colour)
    top: list[tuple[int, int, int]]
    bottom: list[tuple[int, int, int]]


# Used for commits when the user never told git who they are (a fresh desktop install):
# without it every commit, and so every new project, fails.
FALLBACK_NAME = "Syntexophobic"
FALLBACK_EMAIL = "syntexophobic@localhost"


def available() -> bool:
    """Whether git is installed. Without it projects still open and save, just with no history."""
    return shutil.which("git") is not None


class History:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._identity: list[str] | None = None
        self.on_save = None                       # called after each new version (the .cccp repacks)

    def _git(self, *args: str, check: bool = True) -> str:
        if not available():
            raise RuntimeError("Version history needs git. Install it with: sudo apt install git")
        r = subprocess.run(["git", *self._identity_args(), *args], cwd=self.folder, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
        return r.stdout

    def _identity_args(self) -> list[str]:
        """`-c user.name=… -c user.email=…` for whatever git has no value for, else nothing."""
        if self._identity is None:
            self._identity = []
            for key, value in (("user.name", FALLBACK_NAME), ("user.email", FALLBACK_EMAIL)):
                r = subprocess.run(["git", "config", key], cwd=self.folder if self.folder.exists() else None,
                                   capture_output=True, text=True)
                if not r.stdout.strip():
                    self._identity += ["-c", f"{key}={value}"]
        return self._identity

    def init(self) -> None:
        if not available():
            return
        if not (self.folder / ".git").exists():
            self._git("init", "-q", "-b", "main")
            (self.folder / ".gitignore").write_text("*.nc\n*.gcode\n")

    def save(self, message: str) -> str | None:
        """Commit the job and model. Returns the new short hash, or None if nothing changed."""
        if not available():
            return None
        self._git("add", "-A")
        if not self._git("status", "--porcelain"):
            return None
        self._git("commit", "-q", "-m", message or "Save")
        if self.on_save:
            self.on_save()
        return self._git("rev-parse", "--short", "HEAD").strip()

    def log(self, limit: int = 200) -> list[Version]:
        if not available():
            return []
        if not self._git("rev-parse", "--verify", "-q", "HEAD", check=False):
            return []
        out = self._git("log", "--all", f"-n{limit}", "--date=format:%Y-%m-%d %H:%M",
                        "--pretty=format:%H%x1f%h%x1f%ad%x1f%s%x1f%D")
        return [Version(*line.split("\x1f")) for line in out.splitlines() if line]

    def subway(self, limit: int = 200) -> list[Station]:
        """Every version of every variant, newest first, laid out as a subway map: each
        variant is a line in its own lane, and branching or merging versions are where lines meet."""
        if not available() or not self._git("rev-parse", "--verify", "-q", "HEAD", check=False):
            return []
        out = self._git("log", "--all", "--topo-order", f"-n{limit}", "--date=format:%Y-%m-%d %H:%M",
                        "--pretty=format:%H%x1f%h%x1f%ad%x1f%s%x1f%D%x1f%P")
        rows = []
        for line in out.splitlines():
            if line:
                *fields, parents = line.split("\x1f")
                rows.append((Version(*fields), parents.split()))
        current, names = self.branches()
        head = self._git("rev-parse", "HEAD").strip()
        return layout(rows, sorted(names, key=lambda n: n not in ("main", "master")), head)

    def changes(self, ref: str, max_lines: int = 30) -> str:
        """What a version changed, as text: the files touched, then the changed lines of the
        project's text files (binary models only show up as a file name)."""
        files = self._git("show", "--format=", "--stat=60", ref).rstrip()
        lines = [l for l in self._git("show", "--format=", "--no-color", "--unified=0", ref, "--",
                                      "*.toml").splitlines()
                 if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        more = len(lines) - max_lines
        lines = lines[:max_lines] + ([f"… {more} more changed lines"] if more > 0 else [])
        return files + ("\n\n" + "\n".join(lines) if lines else "")

    def diff(self, a: str, b: str | None = None) -> str:
        """Job changes between two versions, or between a version and the working copy."""
        args = ["diff", "--no-color", a] + ([b] if b else []) + ["--", JOB_FILE]
        return self._git(*args)

    def has_changes(self) -> bool:
        if not available():
            return False
        return bool(self._git("status", "--porcelain", check=False))

    def restore(self, ref: str) -> None:
        """Bring the job and model back as they were in `ref`, as uncommitted changes."""
        self._git("checkout", ref, "--", ".")

    def branches(self) -> tuple[str, list[str]]:
        if not available():
            return "", []
        current = self._git("branch", "--show-current").strip()
        names = [b.strip() for b in self._git("branch", "--format=%(refname:short)").splitlines()]
        return current, names

    def new_branch(self, name: str) -> None:
        self._git("switch", "-q", "-c", name)

    def switch(self, name: str) -> None:
        if self.has_changes():
            raise RuntimeError("Save or discard your changes before switching variant.")
        self._git("switch", "-q", name)


def layout(rows: list[tuple[Version, list[str]]], branches: list[str] = (), first: str = "") -> list[Station]:
    """Lanes for commits given newest first (topological order) with their parents.

    Each lane holds the commit it is waiting for. A commit takes the lane waiting for it
    (or a free one), its first parent carries on in that lane, and other parents get lanes
    of their own. A line takes the colour of its variant (`branches` order), and the line
    through `first` (the version you are on) runs straight down the left-most lane."""
    order = {b: i for i, b in enumerate(branches)}
    fresh = len(order)
    lanes: list[str | None] = [first] if first else []    # held for `first` until it shows up
    colours: list[int] = [0] if first else []
    unseen = {first}                                      # held, but no line drawn to it yet

    def free_lane() -> int:
        if None in lanes:
            return lanes.index(None)
        lanes.append(None)
        colours.append(0)
        return len(lanes) - 1

    stations = []
    for version, parents in rows:
        h = version.hash
        before = [None if w in unseen else w for w in lanes]
        unseen.discard(h)
        names = [r.strip().removeprefix("HEAD -> ") for r in version.refs.split(",") if r.strip()]
        named = [order[n] for n in names if n in order]
        tip = h not in lanes
        lane = free_lane() if tip else lanes.index(h)
        if named and (tip or h == first):
            colours[lane] = min(named)
        elif tip:
            colours[lane], fresh = fresh, fresh + 1
        colour = colours[lane]
        top = [(j, lane if w == h else j, colours[j]) for j, w in enumerate(before) if w]
        lanes[:] = [None if w == h else w for w in lanes]     # lines merging into this commit end here
        from_here, joins, moved = set(), set(), []
        for k, p in enumerate(parents):
            if k == 0 and p in lanes and lanes.index(p) > lane:  # the line further right bends in to this one
                j = lanes.index(p)
                moved.append((j, lane, colours[j]))
                lanes[j] = None
                unseen.discard(p)
            if p in lanes:                                    # already on another line: join it
                joins.add(lanes.index(p))
                unseen.discard(p)
                continue
            j = lane if k == 0 else free_lane()
            if k:
                colours[j], fresh = fresh, fresh + 1
            else:
                colours[j] = colour
            lanes[j] = p
            from_here.add(j)
        while lanes and lanes[-1] is None:
            lanes.pop()
            colours.pop()
        bottom = moved + [(lane if j in from_here | joins else j, j, colour if j in joins else colours[j])
                          for j, w in enumerate(lanes) if w and w not in unseen]
        stations.append(Station(version, parents, lane, colour, top, bottom))
    return stations
