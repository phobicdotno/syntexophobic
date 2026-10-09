<p align="center"><img src="src/syntexophobic/assets/syntexophobic.svg" width="112" alt=""></p>

# Syntexophobic

Parametric CAD and 2.5D CAM for GRBL machines (Shapeoko, Nomad, Carbide Motion), with a
git-style history instead of a timeline. Built on OpenCascade, Qt and VTK. Linux desktop.

*σύντηξις (sýntēxis): melting together, fusion.*

![Roughing (blue) and wall finishing (orange) on the demo plate](docs/screenshots/toolpaths.png)

## Run

```sh
uv run syntexophobic                         # open the window
uv run syntexophobic part.step               # new project from a STEP file and open it
uv run syntexophobic new part.step [dir]     # create a project folder only
uv run syntexophobic gcode <project>         # write G-code without the window
uv run syntexophobic simulate <project>      # simulate; exits 1 if the tool would gouge the part
uv run syntexophobic backends                # compute backends and devices
./tools/install-desktop.sh                   # add it to the app menu
uv run pytest                                # tests
uv run python tools/mutants.py               # check the safety tests catch known bugs
```

## How it works

A **project** is a folder with the model (a designed part or a STEP file), `job.toml` and a
git repository. The toolbar switches between the **Design** and **Manufacture** workspaces.

### Design

![Design workspace: feature list, feature settings and the part](docs/screenshots/design.png)

A part is a list of features in `model.toml`, built top to bottom:

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
