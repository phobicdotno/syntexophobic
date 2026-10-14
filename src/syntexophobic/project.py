"""Opening, creating and running a project folder (job.toml + model + git history),
on its own or kept in a .cccp project file (see cccp.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import cam, cccp, gcode, geometry
from . import job as jobfile
from .history import History


class Project:
    def __init__(self, folder: str | Path, archive: str | Path | None = None):
        self.folder = Path(folder)
        self.job = jobfile.load(self.folder / jobfile.JOB_FILE)
        self.history = History(self.folder)
        self.archive: Path | None = None
        if archive:
            self.keep_in(archive, pack=False)
        self._shape = None
        self._shape_up = None
        self._geometry_cache: dict = {}
        self._setup = None

    @classmethod
    def open(cls, path: str | Path) -> "Project":
        """A .cccp file (unpacked to its work folder), a project folder, or its job.toml."""
        path = Path(path).expanduser()
        if cccp.is_cccp(path):
            return cls(cccp.unpack(path), archive=path)
        if path.name == jobfile.JOB_FILE:
            path = path.parent
        return cls(path)

    def keep_in(self, path: str | Path, pack: bool = True) -> "Project":
        """From now on keep this project in the .cccp file at `path`: it is written now, and
        again on every saved version (and whenever the app calls pack())."""
        self.archive = Path(path).expanduser()
        if self.archive.suffix.lower() != cccp.SUFFIX:
            self.archive = self.archive.with_name(self.archive.name + cccp.SUFFIX)
        self.history.on_save = self.pack
        if pack:
            self.pack()
        return self

    def pack(self) -> None:
        """Write the project, history and all, into its .cccp file (if it has one)."""
        if self.archive is not None:
            cccp.pack(self.folder, self.archive)

    @property
    def location(self) -> Path:
        """What the user opened: the .cccp file, or the project folder."""
        return self.archive or self.folder

    @classmethod
    def create(cls, folder: str | Path, model: str | Path) -> "Project":
        """New project from a STEP or STL file: copies the model, writes a default job, first commit.
        `folder` is a project folder, or a .cccp file to keep the project in."""
        folder, archive = _target(folder)
        model = Path(model)
        folder.mkdir(parents=True, exist_ok=True)
        if model.resolve().parent != folder.resolve():
            shutil.copy2(model, folder / model.name)
        jobfile.save(jobfile.Job(name=model.stem, model=model.name), folder / jobfile.JOB_FILE)
        h = History(folder)
        h.init()
        h.save(f"New project from {model.name}")
        return cls(folder)._kept(archive)

    @classmethod
    def create_design(cls, folder: str | Path, name: str) -> "Project":
        """New project with an empty designed part (model.toml) instead of a STEP file.
        `folder` is a project folder, or a .cccp file to keep the project in."""
        from .model import Part
        folder, archive = _target(folder)
        folder.mkdir(parents=True, exist_ok=True)
        Part(name).save(folder)
        (folder / "sketches" / ".keep").touch()
        jobfile.save(jobfile.Job(name=name, model="model.toml"), folder / jobfile.JOB_FILE)
        h = History(folder)
        h.init()
        h.save(f"New design {name}")
        return cls(folder)._kept(archive)

    def _kept(self, archive: Path | None) -> "Project":
        return self.keep_in(archive) if archive else self

    def to_design(self) -> None:
        """Turn a STEP / STL project into a design: the model becomes the first feature
        (an Import, copied into imports/), so it can be extended with sketches and features.
        The job (stock, tools, operations) and history stay."""
        from .model import Feature, Part
        if self.job.model.endswith(".toml"):
            return
        src = self.folder / self.job.model
        dest = self.folder / "imports" / src.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        Part(self.job.name, features=[Feature("import", name=src.stem, file=f"imports/{src.name}")]).save(self.folder)
        (self.folder / "sketches" / ".keep").touch()
        self.job.model = "model.toml"
        self.save_job()
        self.history.save(f"Design on {src.name}")
        self._shape = None

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
            from .model import ModelError, Part
            part = Part.load(path)
            if self.job.stock.body:
                bodies = part.bodies()
                if self.job.stock.body not in bodies:
                    raise ModelError(f"No body called {self.job.stock.body!r} to machine; bodies: {list(bodies)}")
                return bodies[self.job.stock.body]
            return part.build()
        return geometry.load_model(path)

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
            self._setup.job_folder = self.folder
            if self.job.model.endswith(".toml"):
                from .model import Part
                self._setup.sketches = Part.load(self.folder / self.job.model).sketches
        return self._setup

    def flipped(self) -> cam.Setup:
        """The setup for the second side: the part turned over its X axis through the
        middle of the stock, so the same block of stock is used."""
        top = self.setup()
        if getattr(top, "flipped", None) is None:
            s = top.stock
            cy, cz = (s.ymin + s.ymax) / 2, (s.zmin + s.zmax) / 2
            shape = geometry.flip(self.shape, cy, cz)
            if not hasattr(self, "_flipped_cache") or self._flipped_up != self.job.stock.up:
                self._flipped_cache, self._flipped_up = {}, self.job.stock.up
            bottom = cam.Setup(shape, self.job, self._flipped_cache, stock_box=s)
            bottom.job_folder, bottom.flip_axis = self.folder, (cy, cz)
            bottom.sketches = getattr(top, "sketches", None)
            top.flipped = bottom
        return top.flipped

    def toolpaths(self) -> list[cam.Toolpath]:
        self._setup = None   # job values may have changed
        out = []
        for op in self.job.operations:
            if not op.enabled:
                continue
            if op.side == "bottom":
                setup = self.flipped()
                tp = cam.generate(setup, op)
                tp.side, tp.flip = "bottom", setup.flip_axis
            else:
                tp = cam.generate(self.setup(), op)
            out.append(tp)
        return out

    def sides(self) -> list[str]:
        return sorted({op.side for op in self.job.operations if op.enabled}, reverse=True)

    def gcode(self, toolpaths=None, side: str = "top") -> str:
        """G-code for one side (run the top file, flip the part, run the bottom file)."""
        tps = toolpaths if toolpaths is not None else self.toolpaths()
        tps = [t for t in tps if getattr(t, "side", "top") == side]
        return gcode.grbl(self.job, tps, self.setup().stock, side=side)


def _target(folder: str | Path) -> tuple[Path, Path | None]:
    """(folder to work in, .cccp file or None) for a new project at `folder`."""
    folder = Path(folder).expanduser()
    if not cccp.is_cccp(folder):
        return folder, None
    work = cccp.work_folder(folder)
    cccp.remove(work)
    return work, folder


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
