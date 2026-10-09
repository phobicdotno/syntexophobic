"""Every action in Syntexophobic as a command with JSON-friendly arguments and results.

The same commands serve three callers:

- the MCP server (`syntexophobic mcp`), so Claude Code (or the Assistant panel, which
  runs Claude Code) can drive the program;
- the running window, which listens on a local socket and runs commands sent to it, so
  you see every change happen live;
- headless use, straight on the project files, when no window is open.

Commands change the files on disk (job.toml, model.toml, sketches/*.toml) through the
normal Project / Part / Sketch classes; the window reloads after each one.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from .job import Machine, Operation, Stock, Tool
from .job import load as load_job
from .job import save as save_job
from .model import Feature, ModelError, Part
from .project import Project
from .sketch import CONSTRAINT_TYPES, Sketch

STATE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "syntexophobic"
SOCKET = STATE / "control.sock"


class CommandError(RuntimeError):
    pass


def _clean(obj):
    """dataclasses and tuples to plain JSON values."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _clean(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _update(obj, values: dict):
    known = {f.name for f in fields(obj)}
    bad = sorted(set(values) - known)
    if bad:
        raise CommandError(f"Unknown field(s) {bad}; valid: {sorted(known)}")
    for k, v in values.items():
        setattr(obj, k, v)


class Session:
    """The open project and its designed part (if any), and the commands on them."""

    def __init__(self, project: Project | None = None):
        self.project = project
        self.toolpaths = []
        self.last_simulation = None
        self.window = None                 # set by the GUI: for screenshots and live refresh

    # ---- helpers -----------------------------------------------------------------------

    def _need(self) -> Project:
        if self.project is None:
            raise CommandError("No project open: use open_project, new_design or new_from_step")
        return self.project

    def _part(self) -> Part:
        p = self._need()
        if not p.job.model.endswith(".toml"):
            raise CommandError("This project machines an imported STEP file; there is no design to edit")
        return Part.load(p.folder / p.job.model)

    def _save_part(self, part: Part) -> None:
        part.save(self._need().folder)
        self._need().reload()

    def _sketch(self, part: Part, name: str) -> Sketch:
        if name not in part.sketches:
            raise CommandError(f"No sketch {name!r}; sketches: {sorted(part.sketches)}")
        return part.sketches[name]

    def _sketch_result(self, part: Part, sk: Sketch, made=None) -> dict:
        try:
            sk.param_values = part.parameter_values()
        except ModelError:
            pass
        r = sk.solve()
        self._save_part(part)
        out = {"sketch": sk.name, "solved": r.ok, "degrees_of_freedom": r.dof,
               "conflicting_constraints": r.conflicting, "closed_profiles": len(sk.profiles())}
        if made is not None:
            out["created"] = made
        return out

    # ---- projects ---------------------------------------------------------------------

    def open_project(self, path: str) -> dict:
        p = Path(path).expanduser()
        if p.suffix.lower() in (".step", ".stp", ".stl"):
            return self.new_from_step(str(p))
        self.project = Project(p)
        return self.status()

    def new_design(self, folder: str, name: str) -> dict:
        self.project = Project.create_design(Path(folder).expanduser(), name)
        return self.status()

    def new_from_step(self, step: str, folder: str = "") -> dict:
        step = Path(step).expanduser()
        target = Path(folder).expanduser() if folder else step.with_name(step.stem + "-cam")
        self.project = Project.create(target, step)
        return self.status()

    def convert_stl(self, stl: str, step: str = "", merge_flat: bool = True) -> dict:
        """STL mesh -> STEP solid (a closed faceted solid; flat areas merged into faces)."""
        from . import geometry
        src = Path(stl).expanduser()
        dst = Path(step).expanduser() if step else src.with_suffix(".step")
        solid = geometry.stl_to_step(str(src), str(dst), merge_flat)
        b = geometry.bounding_box(solid)
        return {"step": str(dst), "size": [round(b.xmax - b.xmin, 3), round(b.ymax - b.ymin, 3), round(b.zmax - b.zmin, 3)]}

    def status(self) -> dict:
        p = self.project
        if p is None:
            return {"project": None, "hint": "open_project, new_design or new_from_step"}
        out = {"project": str(p.folder), "model": p.job.model, "job": _clean(p.job)}
        if p.job.model.endswith(".toml"):
            part = Part.load(p.folder / p.job.model)
            out["design"] = {"parameters": part.parameters, "sketches": sorted(part.sketches),
                             "features": [_clean(f) for f in part.features]}
            try:
                out["design"]["bodies"] = list(part.bodies())
            except Exception as e:  # does not build yet
                out["design"]["build_error"] = str(e)
        try:
            b = p.setup().part
            out["part_size_mm"] = [round(b.xmax - b.xmin, 3), round(b.ymax - b.ymin, 3), round(b.zmax - b.zmin, 3)]
        except Exception as e:  # model does not build yet
            out["model_error"] = str(e)
        return out

    # ---- design: parameters ------------------------------------------------------------

    def set_parameter(self, name: str, expression: str) -> dict:
        part = self._part()
        part.parameters[name] = str(expression)
        part.parameter_values()                # raises a clear error on loops / unknown names
        self._save_part(part)
        return {"parameters": part.parameters, "values": part.parameter_values()}

    def remove_parameter(self, name: str) -> dict:
        part = self._part()
        part.parameters.pop(name, None)
        self._save_part(part)
        return {"parameters": part.parameters}

    # ---- design: sketches -----------------------------------------------------------------

    def add_sketch(self, name: str, plane: str = "XY", offset: float = 0.0,
                   origin: list | None = None, normal: list | None = None) -> dict:
        """plane XY/XZ/YZ with an offset, or origin + normal for any plane."""
        part = self._part()
        if name in part.sketches:
            raise CommandError(f"A sketch called {name!r} exists")
        if origin is not None and normal is not None:
            part.sketches[name] = Sketch.on_plane(name, origin, normal)
        else:
            part.sketches[name] = Sketch(name=name, plane=plane, offset=float(offset))
        self._save_part(part)
        return {"sketch": name, "plane": part.sketches[name].plane}

    def get_sketch(self, name: str) -> dict:
        part = self._part()
        sk = self._sketch(part, name)
        return {"toml": sk.dumps(), "constraint_types": CONSTRAINT_TYPES}

    def set_sketch(self, name: str, toml: str) -> dict:
        """Replace a sketch with TOML text (same format as get_sketch)."""
        part = self._part()
        sk = Sketch.loads(toml)
        sk.name = name
        part.sketches[name] = sk
        return self._sketch_result(part, sk)

    def sketch_draw(self, sketch: str, shape: str, **a) -> dict:
        """shape: point (x, y) | line (x1, y1, x2, y2) | rectangle (x0, y0, x1, y1) |
        circle (cx, cy, r) | arc (cx, cy, x1, y1, x2, y2; counter-clockwise) | polygon (cx, cy,
        r, sides) | slot (x0, y0, x1, y1, width) | spline (points [[x, y], ...], closed) |
        text (text, x, y, height). Optional construction=true."""
        part = self._part()
        sk = self._sketch(part, sketch)
        c = bool(a.pop("construction", False))
        if shape == "point":
            made = sk.point(a["x"], a["y"], construction=c)
        elif shape == "line":
            made = sk.line((a["x1"], a["y1"]), (a["x2"], a["y2"]), construction=c)
        elif shape == "rectangle":
            made = sk.rectangle(a["x0"], a["y0"], a["x1"], a["y1"])
        elif shape == "circle":
            made = sk.circle((a["cx"], a["cy"]), a["r"], construction=c)
        elif shape == "arc":
            made = sk.arc((a["cx"], a["cy"]), (a["x1"], a["y1"]), (a["x2"], a["y2"]), construction=c)
        elif shape == "polygon":
            made = sk.polygon(a["cx"], a["cy"], a["r"], int(a["sides"]))
        elif shape == "slot":
            made = sk.slot(a["x0"], a["y0"], a["x1"], a["y1"], a["width"])
        elif shape == "spline":
            made = sk.spline([tuple(p) for p in a["points"]], closed=bool(a.get("closed", False)), construction=c)
        elif shape == "text":
            made = sk.text(a["text"], a["x"], a["y"], a.get("height", 10.0), a.get("font", "DejaVu Sans"))
        else:
            raise CommandError(f"Unknown shape {shape!r}")
        return self._sketch_result(part, sk, made)

    def sketch_constrain(self, sketch: str, type: str, refs: list, value: float | None = None,
                         expr: str | None = None) -> dict:
        """Add a constraint between entity ids (see get_sketch for ids and types).
        value is a number; expr an expression with parameters (e.g. "width / 2")."""
        part = self._part()
        sk = self._sketch(part, sketch)
        if expr is not None:
            from .params import evaluate
            value = evaluate(expr, part.parameter_values())
        cid = sk.constrain(type, *refs, value=value)
        if expr is not None:
            next(k for k in sk.constraints if k.id == cid).expr = expr
        return self._sketch_result(part, sk, cid)

    def sketch_edit(self, sketch: str, action: str, **a) -> dict:
        """action: delete (id) | fillet (point, radius) | offset (lines [...], distance) |
        mirror (ids [...], axis) | trim (line, x, y) | construction (id, on)"""
        part = self._part()
        sk = self._sketch(part, sketch)
        if action == "delete":
            sk.delete(a["id"])
            made = None
        elif action == "fillet":
            made = sk.fillet(a["point"], a["radius"])
        elif action == "offset":
            made = sk.offset_lines(a["lines"], a["distance"])
        elif action == "mirror":
            made = sk.mirror(a["ids"], a["axis"])
        elif action == "trim":
            made = sk.trim(a["line"], a["x"], a["y"])
        elif action == "construction":
            ent = sk.lines.get(a["id"]) or sk.circles.get(a["id"]) or sk.arcs.get(a["id"]) or sk.splines.get(a["id"])
            if ent is None:
                raise CommandError(f"No line, circle, arc or spline {a['id']!r}")
            ent.construction = bool(a.get("on", True))
            made = None
        else:
            raise CommandError(f"Unknown sketch action {action!r}")
        return self._sketch_result(part, sk, made)

    def _project_copy(self, path: str) -> str:
        """Copy a file into the project's images/ folder (so history keeps it); returns the
        path relative to the project."""
        import shutil
        src = Path(path).expanduser()
        if not src.exists():
            raise CommandError(f"No file at {src}")
        folder = self._need().folder
        if folder in src.resolve().parents:
            return str(src.resolve().relative_to(folder))
        dest = folder / "images" / src.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        return str(dest.relative_to(folder))

    def trace_image(self, sketch: str, path: str, width: float, x: float = 0.0, y: float = 0.0,
                    threshold: float = 0.5, invert: bool = False) -> dict:
        """Trace the dark areas of a picture (light with invert) into closed outlines in a
        sketch, `width` mm wide, lower-left corner at (x, y)."""
        part = self._part()
        sk = self._sketch(part, sketch)
        rel = self._project_copy(path)
        made = sk.trace_image(self._need().folder / rel, x, y, width, threshold, invert)
        result = self._sketch_result(part, sk)
        result["lines"] = len(made)
        return result

    def add_relief(self, image: str, width: float = 100.0, depth: float = 5.0, base: float = 3.0,
                   invert: bool = False, name: str = "Relief", operation: str = "new", body: str = "") -> dict:
        """A relief whose height follows the picture's brightness (light = high)."""
        rel = self._project_copy(image)
        return self.add_feature("relief", name=name, image=rel, width=width, depth=depth, base=base,
                                invert=invert, operation=operation, body=body)

    def export_sketch_dxf(self, sketch: str, path: str = "") -> dict:
        from . import dxf
        part = self._part()
        sk = self._sketch(part, sketch)
        out = Path(path).expanduser() if path else self._need().folder / f"{sketch}.dxf"
        dxf.export_dxf(sk, out)
        return {"saved": str(out)}

    def import_dxf(self, path: str, name: str = "", plane: str = "XY", offset: float = 0.0) -> dict:
        from . import dxf
        part = self._part()
        name = name or Path(path).stem
        sk = dxf.import_dxf(Path(path).expanduser(), name=name, plane=plane, offset=offset)
        part.sketches[name] = sk
        return self._sketch_result(part, sk)

    # ---- design: features ---------------------------------------------------------------

    def add_feature(self, type: str, **values) -> dict:
        """type: extrude | revolve | loft | sweep | hole | fillet | chamfer | shell | mirror.
        Fields as in model.Feature (sketch, distance, operation, edges, radius, ...)."""
        part = self._part()
        f = Feature(type)
        _update(f, values)
        part.features.append(f)
        return self._built(part)

    def update_feature(self, index: int, **values) -> dict:
        part = self._part()
        if not 0 <= index < len(part.features):
            raise CommandError(f"No feature {index}; there are {len(part.features)}")
        _update(part.features[index], values)
        return self._built(part)

    def remove_feature(self, index: int) -> dict:
        part = self._part()
        part.features.pop(index)
        return self._built(part)

    def _built(self, part: Part) -> dict:
        self._save_part(part)
        try:
            shape = part.build()
        except ModelError as e:
            return {"built": False, "error": str(e), "features": [_clean(f) for f in part.features]}
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from . import geometry
        b = geometry.bounding_box(shape)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        return {"built": True, "size_mm": [round(b.xmax - b.xmin, 3), round(b.ymax - b.ymin, 3), round(b.zmax - b.zmin, 3)],
                "volume_mm3": round(props.Mass(), 3), "holes": [_clean(h) for h in geometry.find_holes(shape)],
                "features": [_clean(f) for f in part.features]}

    def build(self) -> dict:
        return self._built(self._part())

    def export_model(self, path: str) -> dict:
        part = self._part()
        path = str(Path(path).expanduser())
        (part.export_stl if path.lower().endswith(".stl") else part.export_step)(path)
        return {"saved": path}

    # ---- CAM: job ---------------------------------------------------------------------------

    def _job_change(self, change) -> dict:
        p = self._need()
        job = load_job(p.folder / "job.toml")
        change(job)
        save_job(job, p.folder / "job.toml")
        p.reload()
        self.toolpaths = []
        return {"job": _clean(p.job)}

    def set_stock(self, **values) -> dict:
        return self._job_change(lambda j: _update(j.stock, values))

    def set_machine(self, **values) -> dict:
        return self._job_change(lambda j: _update(j.machine, values))

    def add_tool(self, **values) -> dict:
        def change(j):
            t = Tool(id=max([x.id for x in j.tools] or [0]) + 1)
            _update(t, values)
            j.tools.append(t)
        return self._job_change(change)

    def update_tool(self, id: int, **values) -> dict:
        def change(j):
            _update(j.tool(id), values)
        return self._job_change(change)

    def add_operation(self, **values) -> dict:
        """Fields as in job.Operation: type (rough, adaptive, finish, profile, parallel,
        vcarve, drill, bore), tool, stepdown, stepover, leave, feed, plunge, bottom, side..."""
        def change(j):
            op = Operation()
            _update(op, values)
            j.operations.append(op)
        return self._job_change(change)

    def update_operation(self, index: int, **values) -> dict:
        def change(j):
            if not 0 <= index < len(j.operations):
                raise CommandError(f"No operation {index}; there are {len(j.operations)}")
            _update(j.operations[index], values)
        return self._job_change(change)

    def remove_operation(self, index: int) -> dict:
        return self._job_change(lambda j: j.operations.pop(index))

    def suggest_feeds(self, index: int) -> dict:
        from . import feeds
        def change(j):
            feeds.apply(j, j.operations[index])
        return self._job_change(change)

    # ---- CAM: run ---------------------------------------------------------------------------

    def generate(self) -> dict:
        p = self._need()
        self.toolpaths = p.toolpaths()
        rate = p.job.machine.rapid_rate
        return {"toolpaths": [{"operation": t.op.name, "type": t.op.type, "side": t.side, "moves": len(t.moves),
                               "cutting_min": round(t.minutes(), 2), "rapid_min": round(t.rapid_minutes(rate), 2)}
                              for t in self.toolpaths]}

    def simulate(self, resolution: float = 0.25) -> dict:
        from . import simulate as sim
        p = self._need()
        if not self.toolpaths:
            self.generate()
        r = sim.simulate(p.setup(), self.toolpaths, res=resolution, snapshots=40)
        self.last_simulation = r
        worst = max(r.rapid_hits, key=lambda h: h[2]) if r.rapid_hits else None
        return {"gouge_cells": r.gouge_cells, "gouge_depth_mm": round(r.gouge_depth, 4),
                "rapids_into_stock": len(r.rapid_hits),
                "worst_rapid": {"operation": worst[0], "at": _clean(worst[1]), "depth_mm": round(worst[2], 3)} if worst else None,
                "removed_cm3": round(r.removed_cm3, 2), "safe": r.gouge_cells == 0 and not r.rapid_hits}

    def export_gcode(self, path: str = "") -> dict:
        p = self._need()
        if not self.toolpaths:
            self.generate()
        base = Path(path).expanduser() if path else p.folder / f"{p.job.name}.nc"
        sides = p.sides()
        saved = []
        for side in sides:
            out = base if sides == ["top"] else base.with_name(f"{base.stem}-{side}{base.suffix or '.nc'}")
            out.write_text(p.gcode(self.toolpaths, side=side))
            saved.append(str(out))
        return {"saved": saved}

    # ---- history ----------------------------------------------------------------------------

    def save_version(self, message: str) -> dict:
        h = self._need().history.save(message)
        return {"saved": h, "note": None if h else "nothing changed since the last version"}

    def history(self, limit: int = 30) -> dict:
        return {"versions": [_clean(v) for v in self._need().history.log(limit)],
                "variant": self._need().history.branches()[0]}

    def diff(self, ref: str) -> dict:
        return {"diff": self._need().history.diff(ref)}

    def restore(self, ref: str) -> dict:
        p = self._need()
        p.history.restore(ref)
        p.reload()
        return self.status()

    def new_variant(self, name: str) -> dict:
        self._need().history.new_branch(name)
        return {"variant": name}

    # ---- window only ------------------------------------------------------------------------

    def screenshot(self, path: str = "") -> dict:
        if self.window is None:
            raise CommandError("Screenshots need the Syntexophobic window open (run: syntexophobic <project>)")
        return {"image": self.window.screenshot(path or str(STATE / "screenshot.png"))}

    def view(self, preset: str = "iso", workspace: str = "") -> dict:
        """preset: iso | top; workspace: Design | Manufacture (window only)."""
        if self.window is None:
            raise CommandError("The view needs the Syntexophobic window open")
        return self.window.automation_view(preset, workspace)


def server_command() -> list[str]:
    """How to start the MCP server: through uv from a clone, or this Python when installed."""
    import shutil
    import sys
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists():
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        return [uv, "run", "--project", str(root), "syntexophobic", "mcp"]
    return [sys.executable, "-m", "syntexophobic", "mcp"]


COMMANDS = [n for n in dir(Session) if not n.startswith("_") and callable(getattr(Session, n))]


def run(session: Session, cmd: str, args: dict):
    if cmd not in COMMANDS:
        raise CommandError(f"Unknown command {cmd!r}")
    return getattr(session, cmd)(**(args or {}))


# ---- talking to a running window ---------------------------------------------------------

def send(cmd: str, args: dict | None = None, timeout: float = 600.0):
    """Run a command in the open window; raises ConnectionError if none is listening."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(SOCKET))
    except OSError as e:
        raise ConnectionError(str(e)) from e
    with s:
        s.sendall((json.dumps({"cmd": cmd, "args": args or {}}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf.decode())
    if not reply.get("ok"):
        raise CommandError(reply.get("error", "failed"))
    return reply["result"]
