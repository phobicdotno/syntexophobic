<p align="center"><img src="src/syntexophobic/assets/syntexophobic.svg" width="112" alt=""></p>

# Syntexophobic

Parametric CAD and 2.5D CAM for GRBL machines (Shapeoko, Nomad, Carbide Motion), with a
git-style history instead of a timeline. Built on OpenCascade, Qt and VTK. Linux desktop.

*σύντηξις (sýntēxis): melting together, fusion.*

![Roughing (blue) and wall finishing (orange) on the demo plate](docs/screenshots/toolpaths.png)

## Install

**One file:** download `Syntexophobic-<version>-x86_64.AppImage`, then

```sh
chmod +x Syntexophobic-*-x86_64.AppImage
./Syntexophobic-*-x86_64.AppImage              # the window; add a path or a command as below
./Syntexophobic-*-x86_64.AppImage install-mcp  # optional: let Claude Code drive it
```

It carries its own Python and libraries (x86-64 Linux; the window needs X11 or XWayland).
Build it from a clone with `./tools/build-appimage.sh` (writes `dist/`).

**As a Python tool** (not an npm package), with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/phobicdotno/syntexophobic   # installs the `syntexophobic` command
uv tool upgrade syntexophobic                                      # later: update
```

`pipx install git+https://github.com/phobicdotno/syntexophobic` works too. From a clone, put
`uv run` in front of the commands below.

## Run

```sh
uv run syntexophobic                         # open the window
uv run syntexophobic part.step               # new project from a STEP (or STL) file and open it
uv run syntexophobic new part.step [dir]     # create a project folder only
uv run syntexophobic convert part.stl [out.step]  # STL mesh -> STEP solid
uv run syntexophobic --version               # version and build (Help > About lists every release)
uv run syntexophobic gcode <project>         # write G-code without the window
uv run syntexophobic simulate <project>      # simulate; exits 1 if the tool would gouge the part
uv run syntexophobic backends                # compute backends and devices
./tools/install-desktop.sh                   # add it to the app menu
uv run pytest                                # tests
uv run python tools/mutants.py               # check the safety tests catch known bugs
```

## Claude

Syntexophobic can be driven entirely by Claude, using your Claude subscription through
Claude Code (no API key):

- **In the program**: View > Assistant (Ctrl+K) opens a chat panel. Claude gets only
  Syntexophobic's tools and you watch the window change as it works.
- **From Claude Code**: `uv run syntexophobic install-mcp` registers the MCP server once;
  then any Claude Code session can design, machine, simulate and export. With the window
  open, changes appear in it live; without it, Claude works on the project files.

The MCP server has 36 tools (projects, parameters, sketches, features, stock, tools,
operations, toolpaths, simulation, G-code, history, views, screenshots). Claude is told to
call nothing safe to run until `simulate` says so.

## How it works

A **project** is a folder with the model (a designed part or a STEP file), `job.toml` and a
git repository. The toolbar switches between the **Design** and **Manufacture** workspaces.

### Design

![Design workspace: feature list, feature settings and the part](docs/screenshots/design.png)

A part is a list of features in `model.toml`, built top to bottom. A "new" feature starts a
separate body (a box and its lid in one design); others change the body you name, and a cut
without one goes through all of them.

| Feature | Options |
|---|---|
| **Extrude** | distance, symmetric, new / join / cut / intersect, linear or circular pattern |
| **Revolve** | axis line from the sketch, angle, same operations and patterns |
| **Loft** | through two or more sketch profiles, bottom to top |
| **Sweep** | a profile along a path sketch of lines and arcs |
| **Fillet**, **Chamfer** | radius or distance, edge selector |
| **Shell** | wall thickness, faces to open |
| **Mirror** | YZ, XZ or XY plane |
| **Hole** | simple, counterbore or countersink on every free point of a sketch, blind or through |
| **Relief** | a picture's brightness as height, on a base slab |
| **Import** | a STEP or STL part (copied into `imports/`) as a body: new, join, cut or intersect |

**Parameters** (`width = 80`, `wall = 2.5`, `depth = width / 4 - wall`) can be used in
sketch dimensions and in feature values; change one and the whole part follows.

Edges and faces are picked by clicking them in the 3D view (stored as `@x,y,z` and found
again after a rebuild), or with short selectors: `|Z` (vertical edges), `#Z` (horizontal),
`>Z` / `<Z` (top / bottom face), `%LINE`, `%CIRCLE`, and `A&B` for both. Click a flat face
(slanted ones too) and press *Sketch on face* to draw on it.

### Sketch

![Sketch editor: fully constrained base sketch, the hole selected](docs/screenshots/sketch.png)

Lines, rectangles, circles, arcs, splines, polygons, slots, text and points on the XY, XZ or YZ
plane (with an offset), or on any flat face of the part. Corner fillet, offset, mirror and trim work on existing geometry.
Clicking near a point reuses it. Constraints: coincident, horizontal, vertical, parallel,
perpendicular, equal, tangent, concentric, midpoint, point on line/circle, fixed,
symmetric, plus dimensions for length, distance, diameter and angle. Geometry is blue while
it can still move and white when fully constrained; conflicting constraints turn red.
Sketches live in `sketches/<name>.toml`. DXF drawings import as sketches (File menu) and
sketches export as DXF; designed parts export as STEP or STL.

### Job

Stock, machine, tools and operations live in `job.toml`: plain text, stable key order, one
value per line, so diffs read well. Edits in the window are written straight to it.

### History

![Assistant panel: Claude built a parametric coaster live in the window](docs/screenshots/assistant.png)

![Claude turned an attached picture into a relief plaque](docs/screenshots/image-relief.png)

Pictures can become geometry: *Trace image* turns a logo into sketch outlines, *Relief*
turns brightness into height, and the Assistant's *Attach image* lets Claude design a 3D
figure from a picture.

![History panel with a variant and the diff against the current job](docs/screenshots/history.png)

- **Save version** makes a git commit with your note.
- **Variants** are git branches, for trying another tool or strategy without losing the original.
- Picking a version shows what differs in the job now; **Restore** brings it back as unsaved changes.
- `git log` in the project folder shows the same history.

### Toolpaths

For each Z level the tool may not enter anywhere the part exists at or above that height.
That area, grown by the tool radius (plus *leave*), is cut out of the stock outline.

| Operation | What it does |
|---|---|
| **rough** | clears everything left with concentric offset rings |
| **adaptive** | clears with a steered tool that keeps the cut near the stepover (no slotting), for deep passes |
| **finish** | follows every wall once per level; with a ball end it is waterline finishing for steep 3D walls |
| **profile** | follows only the outermost silhouette, for cut-outs; supports **tabs** |
| **parallel** | 3D finishing: raster passes riding on the surface (exact drop-cutter, flat or ball end) |
| **vcarve** | V-bit carving of a sketch or DXF (text, logos): sharp V walls, flat floors past a max depth |
| **engrave** | the tool's centre on a sketch's or DXF's lines (open lines too), down to a depth in stepdown passes |

![Adaptive clearing on the demo plate: steered passes instead of slotting rings](docs/screenshots/adaptive.png)

- Levels always land exactly on flat floors found in the model.
- Every pass **ramps** down along its own path (default 5°) instead of plunging.
- Tabs step straight up just before and down just after, so no slanted move nicks them.
- The model is sliced as a 0.01 mm mesh, checked against exact OpenCascade sections; any
  outline that does not close falls back to the exact section.

![A sign V-carved with a 60° V-bit from sketch text, simulated](docs/screenshots/vcarve.png)

![A domed cap after adaptive roughing and 3 mm ball-end parallel finishing, simulated](docs/screenshots/finish3d.png)

### Two sides

Give an operation `side = "bottom"` to machine the part after turning it over (about the
X axis, front edge staying at the front). Export writes `name-top.nc` and
`name-bottom.nc`; the simulation follows both faces of the stock, so pockets on each
side and holes drilled through from below are checked like everything else.

### Simulation

![Simulated stock after roughing and finishing](docs/screenshots/simulation.png)

The stock is a height map. Every feed move stamps the tool into it, and the result is
compared with a height map of the part, so a toolpath that would cut into the part is
reported and drawn red. Lateral accuracy is a quarter of the grid size (0.25 mm grid by
default).

### G-code

Two posts, both mm and absolute, with `G0`/`G1` moves and `G2`/`G3` arcs where a run of
short moves fits one within 5 µm (turn arcs off with `arcs = false`):

| Post | For | Tool change | Safe retract | End |
|---|---|---|---|---|
| `carbide` (default) | Shapeoko, Nomad with Carbide Motion | `M6 T<n>` for every tool, so Carbide Motion prompts and a BitSetter re-measures | `G53 G0 Z-5` before tool changes and at both ends | `M2` |
| `grbl` | any GRBL 1.1 machine | `M0` pause, re-zero Z by hand | none | `M30` |

The `carbide` post follows Carbide Create's own `shapeoko.post`, including the CutViewer
`STOCK/BLOCK` and `TOOL/MILL` comments. Work zero is the stock's front-left top corner by
default (`zero = "model"` uses the CAD origin).

### Compute backends

Slicing and simulation run through a pluggable backend with a numpy-compatible API:
`numpy` on the CPU always works, `cupy` is used automatically for NVIDIA CUDA (install the
`cuda` extra) or AMD ROCm builds of CuPy. Choose with `--backend` or
`SYNTEXOPHOBIC_BACKEND`. Other GPU stacks register a factory in `compute.BACKENDS`.

## Not there yet

Scallop finishing. See
[CHANGELOG.md](CHANGELOG.md) for what changed.

Screenshots are made from `examples/demo-plate.step` and a small designed bracket by
`uv run python tools/screenshots.py`.
