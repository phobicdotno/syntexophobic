# Changelog

All notable changes to Syntexophobic. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `tools/mutants.py`: puts ten known bugs back one at a time (part ignored by roughing,
  missing tool radius, drop-cutter without edges or faces, flipped work offset, tabs not
  lifted, no ramp, adaptive without keep-out, pecks from the hole top, no first `M6`) and
  checks that the tests fail for each. All ten are caught.
- **Sketch** (`sketch.py`): points, lines, circles and arcs with 20 constraint types
  (coincident, horizontal, vertical, parallel, perpendicular, equal, distance, horizontal /
  vertical distance, radius, diameter, angle, point on line / circle, midpoint, tangent,
  concentric, symmetric, fixed, x, y). Levenberg-Marquardt solver that moves geometry as
  little as possible, reports remaining degrees of freedom and conflicting constraints.
  Closed profiles (with holes) are found automatically. Stored as TOML, one entity per table.
- **3D modelling** (`model.py`): parametric parts as a feature list in `model.toml` with
  sketches in `sketches/`. Extrude (one side or symmetric) and revolve, each as new body,
  join, cut or intersect; linear and circular patterns; fillet, chamfer, shell and mirror.
  Edges and faces are chosen with short selectors (`|Z`, `#Z`, `>Z`, `<Z`, `%CIRCLE`,
  `A&B`) that stay valid when the design changes. STEP export.
- **3D finishing** (`parallel` operation): raster passes along X or Y whose height comes
  from an exact drop-cutter on the part's mesh (tool against every nearby triangle's
  corners, edges and face; flat and ball ends), within 8 µm of the true surface. Parallel
  on all cores with height-sorted pruning. Ball-end tools (`shape = "ball"`), also in the
  simulation, the G-code comments (`TOOL/MILL` corner radius) and the reader.
- **Adaptive clearing** (`adaptive` operation): the tool is steered step by step so the
  cut stays near the stepover instead of slotting; it drops in outside the stock or next
  to cleared material, helixes into closed pockets, and ends each level with an exact
  wall pass. On the test pocket the worst bite falls from 1.0 to 0.53 × diameter and 95 %
  of cuts stay under 0.17 × diameter. Compiled with numba. Feeds suggestion for adaptive:
  deep stepdown, small stepover, feed raised by the radial chip-thinning factor.
- **Undo / redo** (Edit menu, Ctrl+Z / Ctrl+Shift+Z) for design and job edits, over
  snapshots of the project's text files. Rapid edits merge into one step; undoing a step
  that created a sketch removes it again.
- **Loft** through two or more sketch profiles (`sketch = "bottom;top"`) and **sweep** of a
  profile along a path sketch (one chain of lines and arcs; the sweep starts at the end
  the profile sits on). Both take new / join / cut / intersect.
- Picking in the 3D view: model edges are drawn; clicking (without dragging) picks the
  edge or face under the cursor. With a fillet or chamfer selected, clicks add or remove
  its edges; with a shell, its open faces. Picks are stored as `@x,y,z` points in
  `model.toml` and re-found after a rebuild (within 0.75 mm), with a clear error when the
  geometry moved too far. The current feature's picks are highlighted.
- **Sketch on face**: click a flat face square to X, Y or Z, then start a sketch on it.
- Sketch **splines**: smooth curves through ordinary (constrainable) sketch points, open
  or closed, built as OpenCascade interpolating B-splines and drawn from the same curve.
  Spline tool in the editor (Enter / right-click ends, clicking the first point closes).
- Sketch tools: corner **fillet** (tangent arc, the corner point is removed), regular
  **polygon** on a construction circle, **slot**, parametric **offset** of a closed chain
  (stays parallel and at its distance when the original changes), **mirror** across a line
  (symmetric constraints), and **trim** between crossings with lines and circles.
- **Design workspace** in the window: Design / Manufacture switch, File > New design…,
  feature list with per-type settings, and a sketch editor (draw, snap, drag with live
  solving, constraint and dimension buttons, construction geometry). Edits save and
  rebuild the part automatically.
- A CAM project can machine a designed part directly (`model = "model.toml"`); it is
  rebuilt whenever the project reloads.
- Hole detection: full vertical cylinders with material around them. Pocket-corner
  fillets and bosses are left out.
- `drill` operation: full-retract pecks written out move by move (GRBL has no G83),
  counted from the stock top so it is safe before roughing, with a tip allowance on
  through holes. Takes holes within -0.05/+0.3 mm of the drill unless
  `hole_min`/`hole_max` say otherwise.
- `bore` operation: helical circles from the centre outwards for holes larger than the
  endmill.
- Rest machining: a rough pass with `rest_from = <tool id>` only cuts what that earlier,
  bigger tool could not reach (inside corners, narrow gaps).
- Feeds and speeds starting points (`feeds.py`): stock material in the job, and
  *Suggest feeds & speeds* fills feed, plunge, stepdown and stepover per operation from
  chipload × flutes × rpm, scaled for cutter diameter and capped for belt machines.
- Menu bar (File, Toolpaths, View) and black-and-white line icons drawn in the theme's
  text colour. View > Toolbar switches between icons, large icons and text only, and is
  remembered. Toolpaths / Rapids / Model are toggle buttons with shortcuts T, R, M.
- Fusion-style mouse in the 3D view: middle-drag pans, Shift+middle orbits as a
  turntable (Z stays up, stops short of vertical), wheel zooms towards the cursor,
  double middle-click fits.
- 3Dconnexion SpaceMouse / SpaceNavigator support through spacenavd, with a built-in
  client for its socket protocol (no library needed). Twist spins, tilt tilts, slide
  pans, push zooms; left button fits, right button iso view. Reconnects if the daemon
  restarts.
- G-code reader: exported files are parsed back and simulated, so the post-processor is
  checked end to end. `syntexophobic simulate <project> --gcode file.nc` checks any file
  written for the project, including hand-edited ones.
- The operation form shows only the fields that apply to the chosen type.
- Stock removal simulation on a height map, with a gouge check against the part.
  *Simulate* in the window (gouges drawn red) and `syntexophobic simulate`, which exits
  with status 1 if the tool would cut into the part.
- Pluggable compute backends (`compute.py`): numpy on the CPU, CuPy for CUDA or ROCm
  when available. `--backend`, `SYNTEXOPHOBIC_BACKEND` and `syntexophobic backends`.
  Optional `cuda` extra.
- Ramp entry: every pass ramps down along its own path (default 5°, helix-like on short
  loops). `ramp_angle = 0` restores straight plunges.
- `profile` operation that follows only the outermost silhouette, for cut-outs.
- Holding tabs for profile and finish passes, with vertical steps just outside each tab.
- *Show toolpaths* and *Show model* toggles; the simulation view hides both.
- App icon and `tools/install-desktop.sh` for the desktop app menu.
- Demo part generator (`examples/make_demo_part.py`), README screenshots and the script
  that regenerates them (`tools/screenshots.py`).

### Changed
- The gouge check measures depth perpendicular to the part's surface, so a hair-thin
  difference on a near-vertical wall is not reported as a deep cut.
- Ball-end simulation runs in a compiled kernel on the CPU.
- New default post `carbide`, matching Carbide Create's own `shapeoko.post` for Carbide
  Motion: `M6 T<n>` for every tool including the first (Carbide Motion prompts and a
  BitSetter re-measures), `G53 G0 Z-5` safe retracts before tool changes and at both
  ends, CutViewer `STOCK/BLOCK` and `TOOL/MILL` comments, header `G21 G90 G17` (no
  `G94`, which Carbide Motion does not list), ends with `M2`. The previous output stays
  available as `grbl` (manual `M0` tool changes).
- Toolpaths slice a 0.01 mm mesh instead of exact B-rep sections, vectorised through the
  compute backend: a full regenerate on a 66k-triangle assembly went from about 33 s to
  1.7 s. Slices that do not close fall back to the exact section.
- Bounding box, flat levels and slices are cached per model, so regenerating after a job
  edit only redoes the toolpath maths.
- Assemblies are sliced per solid, so touching or overlapping parts stay correct.

### Fixed
- Profile outlines used for hole detection follow each segment in its walking direction
  (a line or spline used backwards no longer scrambles the outline).
- 3D finishing could cut up to 0.7 mm into gently curved surfaces: the point simplifier
  compared each point only with its neighbour's chord, so on long gentle curves the
  errors added up and whole passes became single straight moves. It is now
  Douglas-Peucker (every dropped point within 3 µm of its replacement). Found by
  simulating the full job on a real enclosure; a test with a long shallow curve guards it.
- Hole detection no longer depends on how a face's orientation flag was set; it checks
  that the surface normal points at the hole's axis.
- Roughing ignored the part when the first union of the forbidden area started empty, so
  it would have cut straight through the part. Caught by a test that now samples every
  cut every 0.5 mm.
- The profile pass traced islands inside pockets (it now keeps only top-level outlines).
- Mesh outlines were thinned by a point simplifier whose error added up along curves.

## [0.1.0] - 2026-10-08

### Added
- First version: STEP import with any axis facing up, Z-level roughing and wall
  finishing on OpenCascade slices with Clipper offsets, GRBL post-processor with
  stock-corner work zero and `M0` tool changes.
- Git-style history: `job.toml` versions as commits, variants as branches, diffs and
  restore in the window.
- Qt/VTK desktop window with 3D preview, and a command line (`new`, `gcode`).
