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

    @property
    def shape(self):
        if self._shape is None or self._shape_up != self.job.stock.up:
            raw = geometry.load_step(self.folder / self.job.model)
            self._shape = geometry.orient(raw, self.job.stock.up)
            self._shape_up = self.job.stock.up
            self._setup = None
        return self._shape

    def reload(self) -> None:
        """Re-read job.toml (after restoring a version or switching variant)."""
        old_model = self.job.model
        self.job = jobfile.load(self.folder / jobfile.JOB_FILE)
        if self.job.model != old_model:
            self._shape = None
        self._setup = None

    def save_job(self) -> None:
        jobfile.save(self.job, self.folder / jobfile.JOB_FILE)
        self._setup = None

    def setup(self) -> cam.Setup:
        shape = self.shape          # reloads (and drops the setup) if the up axis changed
        if self._setup is None:
            self._setup = cam.Setup(shape, self.job)
        return self._setup

    def toolpaths(self) -> list[cam.Toolpath]:
        self._setup = None   # job values may have changed
        return cam.generate_all(self.setup())

    def gcode(self, toolpaths=None) -> str:
        tps = toolpaths if toolpaths is not None else self.toolpaths()
        return gcode.grbl(self.job, tps, self.setup().stock)
