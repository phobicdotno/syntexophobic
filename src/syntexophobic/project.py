"""Opening, creating and running a project folder (job.toml + model + git history)."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import cam, gcode, geometry
from . import job as jobfile
from .history import History


class Project:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.job = jobfile.load(self.folder / jobfile.JOB_FILE)
        self.history = History(self.folder)
        self._shape = None
        self._shape_up = None
        self._geometry_cache: dict = {}
        self._setup = None

    @classmethod
    def create(cls, folder: str | Path, model: str | Path) -> "Project":
        """New project folder from a STEP file: copies the model, writes a default job, first commit."""
        folder, model = Path(folder), Path(model)
        folder.mkdir(parents=True, exist_ok=True)
        if model.resolve().parent != folder.resolve():
            shutil.copy2(model, folder / model.name)
        jobfile.save(jobfile.Job(name=model.stem, model=model.name), folder / jobfile.JOB_FILE)
        h = History(folder)
        h.init()
        h.save(f"New project from {model.name}")
        return cls(folder)

    @classmethod
    def create_design(cls, folder: str | Path, name: str) -> "Project":
        """New project with an empty designed part (model.toml) instead of a STEP file."""
        from .model import Part
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        Part(name).save(folder)
        (folder / "sketches" / ".keep").touch()
        jobfile.save(jobfile.Job(name=name, model="model.toml"), folder / jobfile.JOB_FILE)
        h = History(folder)
        h.init()
        h.save(f"New design {name}")
        return cls(folder)

    @property
    def shape(self):
        if self._shape is None or self._shape_up != self.job.stock.up:
            raw = self._load_model()
            self._shape = geometry.orient(raw, self.job.stock.up)
            self._shape_up = self.job.stock.up
            self._geometry_cache = {}
            self._setup = None
        return self._shape

    def _load_model(self):
        """The part to machine: a STEP file, or a designed part (model.toml) built now."""
        path = self.folder / self.job.model
        if path.suffix.lower() == ".toml":
            from .model import Part
            return Part.load(path).build()
        return geometry.load_step(path)

    def reload(self) -> None:
        """Re-read job.toml and the model (after restoring a version or switching variant)."""
        self.job = jobfile.load(self.folder / jobfile.JOB_FILE)
        self._shape = None
        self._setup = None

    def save_job(self) -> None:
        jobfile.save(self.job, self.folder / jobfile.JOB_FILE)
        self._setup = None

    def setup(self) -> cam.Setup:
        shape = self.shape          # reloads (and drops the setup) if the up axis changed
        if self._setup is None:
            self._setup = cam.Setup(shape, self.job, self._geometry_cache)
        return self._setup

    def toolpaths(self) -> list[cam.Toolpath]:
        self._setup = None   # job values may have changed
        return cam.generate_all(self.setup())

    def gcode(self, toolpaths=None) -> str:
        tps = toolpaths if toolpaths is not None else self.toolpaths()
        return gcode.grbl(self.job, tps, self.setup().stock)


class Undo:
    """Undo/redo over the project's text files (job, model, sketches).

    Every recorded edit stores the files as they were before it. Edits within
    `merge_seconds` of each other (a spin box held down, typing a value) become one step.
    """

    def __init__(self, folder: str | Path, limit: int = 200, merge_seconds: float = 0.8):
        self.folder = Path(folder)
        self.limit = limit
        self.merge_seconds = merge_seconds
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.current = self.snapshot()
        self._last = 0.0

    def _files(self):
        f = self.folder
        return [p for p in [f / jobfile.JOB_FILE, f / "model.toml", *sorted((f / "sketches").glob("*.toml"))]
                if p.exists()]

    def snapshot(self) -> dict:
        return {str(p.relative_to(self.folder)): p.read_text() for p in self._files()}

    def record(self) -> None:
        """Call after the files were changed on disk."""
        import time
        now = self.snapshot()
        if now == self.current:
            return
        t = time.monotonic()
        if not (self.undo_stack and t - self._last < self.merge_seconds):
            self.undo_stack.append(self.current)
            del self.undo_stack[:-self.limit]
        self._last = t
        self.current = now
        self.redo_stack.clear()

    def _apply(self, state: dict) -> None:
        for rel, text in state.items():
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for p in self._files():                     # sketches that did not exist then
            if str(p.relative_to(self.folder)) not in state:
                p.unlink()
        self.current = state

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.current)
        self._apply(self.undo_stack.pop())
        self._last = 0.0
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.current)
        self._apply(self.redo_stack.pop())
        self._last = 0.0
        return True
