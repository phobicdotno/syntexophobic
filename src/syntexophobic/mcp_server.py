"""MCP server: drive Syntexophobic from Claude Code (or the Assistant panel).

Run by Claude Code as `syntexophobic mcp` (stdio). Every tool is sent to the open
Syntexophobic window when one is running, so changes appear live; otherwise it runs on
the project files directly. Uses Claude Code's own login: no API key is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from . import automation as A

INSTRUCTIONS = """Syntexophobic is a parametric CAD + CAM program for CNC routers (GRBL / Carbide Motion).
A project is a folder with job.toml (stock, machine, tools, operations), model.toml (designed
part: parameters + features) and sketches/*.toml. Units are mm.

Typical flow: new_design -> add_sketch -> sketch_draw / sketch_constrain (until 0 degrees of
freedom) -> add_feature (extrude, revolve, loft, sweep, hole, fillet, chamfer, shell, mirror) ->
add_tool / add_operation -> generate -> simulate (must be safe: no gouges, no rapids into stock)
-> export_gcode -> save_version. Call status first to see what is open. Use screenshot to see
the result when the window is open. Never tell the user a file is safe to run unless simulate
returned safe=true."""

server = MCPServer("syntexophobic", instructions=INSTRUCTIONS)
_headless = A.Session()
_STATE = A.STATE / "headless.json"


def _call(cmd: str, **args):
    """Run in the open window if there is one, else headless (remembering the project)."""
    try:
        return A.send(cmd, args)
    except ConnectionError:
        pass
    if _headless.project is None and cmd not in ("open_project", "new_design", "new_from_step") and _STATE.exists():
        last = json.loads(_STATE.read_text()).get("project")
        if last and Path(last).exists():
            _headless.open_project(last)
    result = A.run(_headless, cmd, args)
    if _headless.project is not None:
        A.STATE.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps({"project": str(_headless.project.folder)}))
    return result


def _out(result) -> str:
    return json.dumps(result, indent=1)


# ---- projects -----------------------------------------------------------------------------

@server.tool()
def status() -> str:
    """What is open: project folder, job (stock, machine, tools, operations), design
    (parameters, sketches, features) and part size. Call this first."""
    return _out(_call("status"))


@server.tool()
def open_project(path: str) -> str:
    """Open a project folder (with job.toml), or a STEP or STL file (creates a <name>-cam project)."""
    return _out(_call("open_project", path=path))


@server.tool()
def convert_stl(stl: str, step: str = "", merge_flat: bool = True) -> str:
    """Convert an STL mesh to a STEP solid (default: next to it with .step). Flat areas become
    single faces unless merge_flat is false."""
    return _out(_call("convert_stl", stl=stl, step=step, merge_flat=merge_flat))


@server.tool()
def new_design(folder: str, name: str) -> str:
    """Start a new project with an empty designed part in `folder`."""
    return _out(_call("new_design", folder=folder, name=name))


@server.tool()
def new_from_step(step: str, folder: str = "") -> str:
    """Start a CAM project from a STEP file (the model is not editable, only machined)."""
    return _out(_call("new_from_step", step=step, folder=folder))


# ---- design -----------------------------------------------------------------------------------

@server.tool()
def set_parameter(name: str, expression: str) -> str:
    """Define or change a named parameter, e.g. width = "80", depth = "width / 4". Use
    parameter names in dimensions (expr) and feature values."""
    return _out(_call("set_parameter", name=name, expression=expression))


@server.tool()
def add_sketch(name: str, plane: str = "XY", offset: float = 0.0, origin: list[float] | None = None,
               normal: list[float] | None = None) -> str:
    """New empty sketch on XY / XZ / YZ shifted by `offset` along the plane's normal, or on any
    plane through `origin` with `normal`. XY normal is +Z, XZ normal is -Y, YZ normal is +X."""
    return _out(_call("add_sketch", name=name, plane=plane, offset=offset, origin=origin, normal=normal))


@server.tool()
def get_sketch(name: str) -> str:
    """A sketch as TOML (points, lines, circles, arcs, splines, constraints with their ids)
    plus the list of constraint types and what they take."""
    return _out(_call("get_sketch", name=name))


@server.tool()
def set_sketch(name: str, toml: str) -> str:
    """Replace a whole sketch with TOML in the get_sketch format, then solve it."""
    return _out(_call("set_sketch", name=name, toml=toml))


@server.tool()
def sketch_draw(sketch: str, shape: str, params: dict) -> str:
    """Draw in a sketch (sketch coordinates, mm). shape and params:
    point {x, y} | line {x1, y1, x2, y2} | rectangle {x0, y0, x1, y1} | circle {cx, cy, r} |
    arc {cx, cy, x1, y1, x2, y2} (counter-clockwise from 1 to 2) | polygon {cx, cy, r, sides} |
    slot {x0, y0, x1, y1, width} | spline {points: [[x, y], ...], closed} | text {text, x, y, height}.
    Add "construction": true for reference geometry. Returns the new ids and the solver state."""
    return _out(_call("sketch_draw", sketch=sketch, shape=shape, **params))


@server.tool()
def sketch_constrain(sketch: str, type: str, refs: list[str], value: float | None = None,
                     expr: str | None = None) -> str:
    """Constrain entities by id. Types: fixed, coincident, horizontal, vertical, parallel,
    perpendicular, equal, distance (line length / two points / point-line), horizontal_distance,
    vertical_distance, radius, diameter, angle (degrees), point_on_line, point_on_circle,
    midpoint, tangent, concentric, symmetric (p1, p2, line), x, y. Give a number in `value` or
    an expression with parameters in `expr`. Returns degrees of freedom left (0 = fully
    constrained) and any conflicting constraints."""
    return _out(_call("sketch_constrain", sketch=sketch, type=type, refs=refs, value=value, expr=expr))


@server.tool()
def sketch_edit(sketch: str, action: str, params: dict) -> str:
    """Edit a sketch. action and params: delete {id} | fillet {point, radius} (corner of two
    lines) | offset {lines: [...], distance} (closed chain; negative = inwards) | mirror {ids,
    axis} | trim {line, x, y} (remove the piece of the line at x, y) | construction {id, on}."""
    return _out(_call("sketch_edit", sketch=sketch, action=action, **params))


@server.tool()
def import_dxf(path: str, name: str = "", plane: str = "XY", offset: float = 0.0) -> str:
    """Import a DXF drawing as a new sketch (lines, arcs, circles, polylines, splines)."""
    return _out(_call("import_dxf", path=path, name=name, plane=plane, offset=offset))


@server.tool()
def trace_image(sketch: str, path: str, width: float, x: float = 0.0, y: float = 0.0,
                threshold: float = 0.5, invert: bool = False) -> str:
    """Trace a picture (PNG/JPG...) into a sketch: its dark areas (light ones with invert)
    become closed outlines `width` mm wide, holes included, lower-left at (x, y). The image is
    copied into the project. Extrude, cut or V-carve the result."""
    return _out(_call("trace_image", sketch=sketch, path=path, width=width, x=x, y=y,
                      threshold=threshold, invert=invert))


@server.tool()
def add_relief(image: str, width: float = 100.0, depth: float = 5.0, base: float = 3.0,
               invert: bool = False, name: str = "Relief", operation: str = "new", body: str = "") -> str:
    """A 3D relief from a picture: brightness becomes height (light = high, or dark with
    invert), `width` mm wide, `depth` mm from darkest to lightest, on a `base` mm slab.
    Machine it with a ball end and a 'parallel' operation."""
    return _out(_call("add_relief", image=image, width=width, depth=depth, base=base, invert=invert,
                      name=name, operation=operation, body=body))


@server.tool()
def export_sketch_dxf(sketch: str, path: str = "") -> str:
    """Save a sketch as a DXF drawing (mm). Default: <project>/<sketch>.dxf."""
    return _out(_call("export_sketch_dxf", sketch=sketch, path=path))


@server.tool()
def add_feature(type: str, fields: dict) -> str:
    """Add a feature at the end of the part. type: extrude | revolve | loft | sweep | hole |
    fillet | chamfer | shell | mirror. fields (as needed): name, sketch, distance (negative =
    other way), symmetric, operation (new/join/cut/intersect), axis (sketch line id, revolve),
    angle, path (sweep), hole (simple/counterbore/countersink), diameter, depth (0 = through),
    head_diameter, head_depth, radius (fillet/chamfer), edges (selector: |Z, #Z, >Z, <Z, %CIRCLE,
    A&B, or @x,y,z picks), faces (shell), thickness, plane (mirror), count/spacing/direction
    (pattern). Numbers may be parameter expressions. Returns size, volume and holes, or the error."""
    return _out(_call("add_feature", type=type, **fields))


@server.tool()
def update_feature(index: int, fields: dict) -> str:
    """Change fields of feature `index` (0-based) and rebuild."""
    return _out(_call("update_feature", index=index, **fields))


@server.tool()
def remove_feature(index: int) -> str:
    """Delete feature `index` (0-based) and rebuild."""
    return _out(_call("remove_feature", index=index))


@server.tool()
def build() -> str:
    """Rebuild the part: size, volume, holes found, or the error."""
    return _out(_call("build"))


@server.tool()
def export_model(path: str) -> str:
    """Save the designed part as STEP (.step/.stp) or STL (.stl)."""
    return _out(_call("export_model", path=path))


# ---- CAM -------------------------------------------------------------------------------------

@server.tool()
def set_stock(fields: dict) -> str:
    """Stock: up (+Z, -Z, +X...), material (softwood, hardwood, mdf, plywood, acrylic, hdpe,
    aluminium, brass), margin, top_offset, width/depth/height (exact blank size, 0 = margin)."""
    return _out(_call("set_stock", **fields))


@server.tool()
def set_machine(fields: dict) -> str:
    """Machine: post (carbide = Carbide Motion with M6, grbl), rpm, safe_z, zero (stock/model),
    arcs (G2/G3 on/off), rapid_rate."""
    return _out(_call("set_machine", **fields))


@server.tool()
def add_tool(fields: dict) -> str:
    """Add a tool: name, diameter, flutes, shape (flat/ball/vbit), angle (vbit). Returns the job
    with the new tool's id."""
    return _out(_call("add_tool", **fields))


@server.tool()
def update_tool(id: int, fields: dict) -> str:
    """Change tool `id`."""
    return _out(_call("update_tool", id=id, **fields))


@server.tool()
def add_operation(fields: dict) -> str:
    """Add a machining operation: type (rough, adaptive, finish, profile, parallel, vcarve,
    drill, bore), name, tool (id), stepdown, stepover (x diameter), leave, feed, plunge
    (mm/min), bottom (Z, omit for the model bottom), ramp_angle, tabs/tab_width/tab_height
    (profile), rest_from (tool id), raster (X/Y), source + max_depth (vcarve), side (top/bottom)."""
    return _out(_call("add_operation", **fields))


@server.tool()
def update_operation(index: int, fields: dict) -> str:
    """Change operation `index` (0-based)."""
    return _out(_call("update_operation", index=index, **fields))


@server.tool()
def remove_operation(index: int) -> str:
    """Delete operation `index` (0-based)."""
    return _out(_call("remove_operation", index=index))


@server.tool()
def suggest_feeds(index: int) -> str:
    """Fill feed, plunge, stepdown and stepover of operation `index` from the stock material."""
    return _out(_call("suggest_feeds", index=index))


@server.tool()
def generate() -> str:
    """Compute toolpaths for all operations: moves and cutting / rapid minutes per operation."""
    return _out(_call("generate"))


@server.tool()
def simulate(resolution: float = 0.25) -> str:
    """Simulate the toolpaths on the stock. safe=true only when nothing cuts into the part and
    no rapid move hits material. Always run this before export_gcode."""
    return _out(_call("simulate", resolution=resolution))


@server.tool()
def export_gcode(path: str = "") -> str:
    """Write G-code (one file per side for two-sided jobs). Default: <project>/<name>.nc."""
    return _out(_call("export_gcode", path=path))


# ---- history -----------------------------------------------------------------------------------

@server.tool()
def save_version(message: str) -> str:
    """Commit the current job and design to the project's git history with a note."""
    return _out(_call("save_version", message=message))


@server.tool()
def history(limit: int = 30) -> str:
    """Saved versions (newest first) and the current variant."""
    return _out(_call("history", limit=limit))


@server.tool()
def diff(ref: str) -> str:
    """What changed in job.toml since version `ref` (hash from history)."""
    return _out(_call("diff", ref=ref))


@server.tool()
def restore(ref: str) -> str:
    """Bring back version `ref` as unsaved changes."""
    return _out(_call("restore", ref=ref))


@server.tool()
def new_variant(name: str) -> str:
    """Start a variant (git branch) to try something without losing the current version."""
    return _out(_call("new_variant", name=name))


# ---- window -----------------------------------------------------------------------------------

@server.tool()
def view(preset: str = "iso", workspace: str = "") -> str:
    """Window only: camera preset iso / top, and workspace Design / Manufacture."""
    return _out(_call("view", preset=preset, workspace=workspace))


@server.tool()
def screenshot() -> Image:
    """Window only: a picture of the Syntexophobic window as it is now."""
    result = _call("screenshot")
    return Image(path=result["image"])


def main() -> None:
    server.run("stdio")
