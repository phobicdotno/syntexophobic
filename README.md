# Syntexophobic

2.5D CAM for GRBL machines (Shapeoko, Nomad, Carbide Motion), with a git-style job history
instead of a timeline. Built on OpenCascade, Qt and VTK. Linux desktop.

*σύντηξις (sýntēxis): melting together, fusion.*

## Run

```sh
uv run syntexophobic                       # open the window
uv run syntexophobic part.step             # new project from a STEP file and open it
uv run syntexophobic new part.step [dir]   # create a project folder only
uv run syntexophobic gcode <project>       # write G-code without the window
uv run pytest                              # tests
```

## How it works

A **project** is a folder with the STEP model, `job.toml` and a git repository.

- **Job.** Stock, machine, tools and operations live in `job.toml`: plain text, stable key
  order, one value per line, so diffs read well. Edits in the window are written straight
  to it.
- **History.** *Save version* is a git commit with your note. *Variants* are git branches,
  for trying a different tool or strategy. Pick any version to see what differs from now,
  and *Restore* brings it back as unsaved changes. `git log` in the folder shows the same
  history.
- **Toolpaths.** For each Z level the tool may not enter anywhere the part exists at or
  above that height. That area, grown by the tool radius (plus *leave* when roughing), is
  cut out of the stock outline:
  - **rough** clears what is left with concentric offset rings,
  - **finish** follows the walls only.
  Levels always land exactly on flat floors found in the model.
- **G-code.** GRBL 1.1, mm, absolute. Work zero is the stock's front-left top corner by
  default (`zero = "model"` uses the CAD origin). Tool changes stop with `M0`.

## Not there yet

Ramp/helix entry (plunges are straight), tabs for cut-through parts, adaptive clearing,
drilling, 3D finishing, simulation, and the modelling side.
