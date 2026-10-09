"""The CAM job: a plain-text TOML file that lives next to the model and is versioned with git."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

JOB_FILE = "job.toml"


@dataclass
class Tool:
    id: int = 1
    name: str = "6 mm flat endmill"
    diameter: float = 6.0
    flutes: int = 2
    shape: str = "flat"        # flat | ball | vbit
    angle: float = 90.0        # vbit: included angle in degrees


@dataclass
class Stock:
    up: str = "+Z"            # which model axis points up at the machine
    body: str = ""            # designed part with several bodies: the one to machine ("" = all)
    material: str = "hardwood"   # feeds.MATERIALS key, for suggested feeds and speeds
    margin: float = 3.0       # extra material around the part in X and Y (when no size is set)
    top_offset: float = 1.0   # extra material above the part top, faced off first
    width: float = 0.0        # exact blank size in X, Y, Z (0 = part + margin); centred on
    depth: float = 0.0        # the part in X and Y, top at part top + top_offset
    height: float = 0.0


@dataclass
class Machine:
    post: str = "carbide"     # carbide (Carbide Motion: M6, BitSetter) | grbl (M0 pauses)
    rpm: int = 18000
    safe_z: float = 5.0       # rapid height above the stock top
    rapid_rate: float = 5000.0   # mm/min, for time estimates
    arcs: bool = True         # write runs of short moves as G2/G3 arcs (within 5 µm)
    zero: str = "stock"       # "stock": X0 Y0 front-left corner, Z0 top. "model": CAD origin


@dataclass
class Operation:
    type: str = "rough"       # rough | adaptive | finish | profile | parallel (3D) | vcarve | drill | bore
    name: str = "Rough"
    tool: int = 1
    stepdown: float = 1.5
    stepover: float = 0.45    # fraction of tool diameter
    leave: float = 0.3        # material left on walls for the finish pass
    feed: float = 1000.0      # mm/min
    plunge: float = 300.0     # mm/min
    bottom: float | None = None   # lowest Z; default is the bottom of the model
    ramp_angle: float = 5.0   # degrees; the tool ramps down along its path. 0 = plunge
    tabs: int = 0             # holding tabs per outline (profile and finish only)
    tab_width: float = 6.0
    tab_height: float = 2.0  # above the operation's bottom
    rest_from: int | None = None    # rough: only cut what this earlier tool (id) could not reach
    raster: str = "X"               # parallel: direction of the passes, X or Y
    source: str = ""                # vcarve: a sketch of the designed part, or a .dxf in the project
    max_depth: float = 3.0          # vcarve: deepest cut; wider areas get a flat floor
    hole_min: float | None = None   # drill/bore: smallest hole diameter to machine
    hole_max: float | None = None   # drill/bore: largest
    side: str = "top"         # top, or bottom: after flipping the part over its X axis
    enabled: bool = True


@dataclass
class Job:
    name: str = "Untitled"
    model: str = ""
    stock: Stock = field(default_factory=Stock)
    machine: Machine = field(default_factory=Machine)
    tools: list[Tool] = field(default_factory=lambda: [Tool()])
    operations: list[Operation] = field(default_factory=lambda: [
        Operation(),
        Operation(type="finish", name="Finish walls", stepdown=3.0, leave=0.0, feed=800.0),
    ])

    def tool(self, tool_id: int) -> Tool:
        for t in self.tools:
            if t.id == tool_id:
                return t
        raise KeyError(f"No tool with id {tool_id}")


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def load(path: str | Path) -> Job:
    data = tomllib.loads(Path(path).read_text())
    j = data.get("job", {})
    return Job(
        name=j.get("name", "Untitled"),
        model=j.get("model", ""),
        stock=_build(Stock, data.get("stock", {})),
        machine=_build(Machine, data.get("machine", {})),
        tools=[_build(Tool, t) for t in data.get("tools", [])] or [Tool()],
        operations=[_build(Operation, o) for o in data.get("operations", [])],
    )


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return repr(v)


def _table(header: str, obj) -> list[str]:
    lines = [header]
    for k, v in asdict(obj).items():
        if v is not None:
            lines.append(f"{k} = {_value(v)}")
    return lines + [""]


def dumps(job: Job) -> str:
    """Stable, diff-friendly TOML: fixed key order, one value per line."""
    out = ["[job]", f"name = {_value(job.name)}", f"model = {_value(job.model)}", ""]
    out += _table("[stock]", job.stock)
    out += _table("[machine]", job.machine)
    for t in job.tools:
        out += _table("[[tools]]", t)
    for op in job.operations:
        out += _table("[[operations]]", op)
    return "\n".join(out)


def save(job: Job, path: str | Path) -> None:
    Path(path).write_text(dumps(job))
