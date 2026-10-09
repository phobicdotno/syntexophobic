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
        return self._git("rev-parse", "--short", "HEAD").strip()

    def log(self, limit: int = 200) -> list[Version]:
        if not available():
            return []
        if not self._git("rev-parse", "--verify", "-q", "HEAD", check=False):
            return []
        out = self._git("log", "--all", f"-n{limit}", "--date=format:%Y-%m-%d %H:%M",
                        "--pretty=format:%H%x1f%h%x1f%ad%x1f%s%x1f%D")
        return [Version(*line.split("\x1f")) for line in out.splitlines() if line]

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
