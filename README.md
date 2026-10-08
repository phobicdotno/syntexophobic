<p align="center"><img src="src/syntexophobic/assets/syntexophobic.svg" width="112" alt=""></p>

# Syntexophobic

2.5D CAM for GRBL machines (Shapeoko, Nomad, Carbide Motion), with a git-style job history
instead of a timeline. Built on OpenCascade, Qt and VTK. Linux desktop.

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
```

## How it works

A **project** is a folder with the STEP model, `job.toml` and a git repository.

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
| **finish** | follows every wall once per level |
| **profile** | follows only the outermost silhouette, for cut-outs; supports **tabs** |

- Levels always land exactly on flat floors found in the model.
- Every pass **ramps** down along its own path (default 5°) instead of plunging.
- Tabs step straight up just before and down just after, so no slanted move nicks them.
- The model is sliced as a 0.01 mm mesh, checked against exact OpenCascade sections; any
  outline that does not close falls back to the exact section.

### Simulation

![Simulated stock after roughing and finishing](docs/screenshots/simulation.png)

The stock is a height map. Every feed move stamps the tool into it, and the result is
compared with a height map of the part, so a toolpath that would cut into the part is
reported and drawn red. Lateral accuracy is a quarter of the grid size (0.25 mm grid by
default).

### G-code

Two posts, both mm and absolute, with only `G0`/`G1` moves:

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

Drilling, adaptive clearing, 3D finishing, and the modelling side. See
[CHANGELOG.md](CHANGELOG.md) for what changed.

Screenshots are made from `examples/demo-plate.step` by `uv run python tools/screenshots.py`.
