# Changelog

All notable changes to Syntexophobic. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Drive Syntexophobic from Claude** over MCP: `syntexophobic mcp` is an MCP server
  (stdio) with 36 tools covering projects, parameters, sketches (draw, constrain, edit,
  DXF), features, stock, machine, tools, operations, toolpaths, simulation, G-code,
  history, views and screenshots. With the window open, every call runs in the window
  (a local socket, this user only) so changes appear live; otherwise it works headless
  on the project files. `syntexophobic install-mcp` registers it with Claude Code. It
  uses Claude Code's own login, not an API key.
- `tools/mutants.py`: puts eighteen known bugs back one at a time (part ignored by roughing,
  missing tool radius, drop-cutter without edges or faces, flipped work offset, tabs not
  lifted, no ramp, adaptive without keep-out, pecks from the hole top, a too-narrow ball
  waterline, no first `M6`, a lazy point simplifier, loop outlines chained from the wrong
  end, holes attached with a fixed reversal, adaptive leftovers not cleaned up, G2/G3 swapped, bottom side
  not mirrored, rough tool kept inside the stock outline) and checks that the tests fail
  for each. All eighteen are caught;
  the waterline one first survived, which led to a test on vertical walls.
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
- **Waterline finishing with ball ends**: `finish` and `profile` with a ball-end tool
  contour each level using the ball's real shape (material from the tip up to the equator,
  widened by the ball's width at each height, sampled at equal width steps so it is never
  too narrow and at most r/40 too wide). Pairs with `parallel` for steep walls.
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
- **Sketch on face**: click any flat face, including slanted ones, and start a sketch on
  it. Sketch planes can be custom (origin, X direction, normal, stored in the sketch
  file); faces square to an axis still become XY / XZ / YZ with an offset. The normal
  points out of the material, so a positive extrude adds and a negative one cuts.
- **V-carving** (`vcarve` operation, `vbit` tools with an angle): carves the closed
  shapes of a sketch or a DXF into the stock top; each ring sits at depth = distance from
  the outline ÷ tan(half angle), so walls are true V-grooves with sharp corners, and a
  max depth gives V-pockets with flat floors. Cone-shaped tools in the simulation, the
  G-code comment and the reader.
- **Text** in sketches (Text tool): any installed font, as closed outlines with the
  letters' holes, ready to extrude, cut or V-carve.
- Adaptive clearing always finishes a level: whatever the steered walk leaves (it gives up
  on awkward spots or at its path limit) is cleared with offset rings before the wall
  pass. Found when a reordering experiment left material that later rapids hit; the
  in-order rapid check caught it.
- **Two-sided machining**: operations have a side (`top` or `bottom`). Bottom
  operations see the part turned over its X axis through the middle of the same stock.
  The simulation keeps both faces of the stock (gouge and rapid checks on both, cuts from
  both sides that meet become holes). Bottom paths are drawn where they cut, and G-code
  export writes one file per side with a note on how to flip.
- **Arc output** (G2/G3, on by default, `arcs = false` turns it off): runs of short flat
  moves become arcs that stay within 5 µm of the path (checked at every point and chord
  midpoint). The reader expands arcs back, and the round-trip test simulates the file.
  On the test enclosure: 44 073 → 26 824 lines (942 → 675 KB).
- Roughing and adaptive stay low between passes when the hop stays inside this level's
  allowed area (lift to just above the previous level instead of over the top). Time
  estimates can include rapids (`rapid_rate`). The gain on the test enclosure is small
  (adaptive rapids 4.7 → 4.6 min): most rapid time is horizontal travel between paths.
- **Rapid moves are checked**: the simulation runs in machine order and tests every rapid
  against the stock as it is at that moment; a rapid into material is reported (window
  and `simulate`, which exits 1) with where and how deep.
- **Simulation playback**: the simulation keeps about 40 snapshots in cutting order; a
  slider in the toolbar shows the stock at any moment with the tool drawn where it is.
- **Section view** (View > Section > X / Y / Z): cuts the model, its edges and the
  simulated stock with a plane, moved with a slider in the toolbar; it stays while the
  part is rebuilt or re-simulated.
- **Tool library** shared by all projects (`~/.config/syntexophobic/tools.toml`), started
  with common inch and metric bits: *From library…* copies a tool into the job with the
  next free number, *Save to library* keeps a job's tool for later.
- **Exact stock size** (X, Y, Z), centred on the part, with a warning when the blank is
  smaller than the part; the margin is still used when no size is set.
- **Measure** (View menu, D): click an edge or face for its length, diameter or area;
  click a second one for the minimum distance with ΔX/ΔY/ΔZ, drawn as a line.
- **Hole** feature: simple, counterbore or 90° countersink holes on every free point of
  a sketch, drilled along the sketch normal into the material; blind (depth) or through.
  The CAM side finds them as holes for drilling.
- **Parameters**: named values and expressions (`width = 80`, `depth = width / 4 - wall`)
  in `model.toml`, usable in sketch dimensions and feature values (distance, angle,
  radius, thickness, pattern count and spacing). Change one and the part follows.
  Expressions are arithmetic only (checked with `ast`), so a shared model file cannot run
  code. Parameters dialog in the Design panel; dimensions and feature fields accept
  expressions and mark invalid ones red.
- **DXF import and export** (File menu, `dxf.py`): lines, arcs, circles, polylines with
  bulges, splines and ellipses; touching end points are merged so outlines become closed
  profiles; inch drawings are scaled to mm. Sketches export as mm DXF.
- **Export model** as STEP or STL.
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
- Roughing kept the tool centre inside the stock outline, so a strip of stock narrower
  than the tool keep-out (3 mm margin vs 3.3 mm) was never cut, and later moves plunged
  into it. Found the first time Claude drove the window over MCP and simulated the result.
- The outline used for holes and V-carving compared each segment with the previous
  segment's start instead of its end, which scrambled shapes with long straight strokes
  (an "N" came out as a filled box). A test checks the area of N, Z, M and W.
- V-carving is not reported as a gouge inside the carved shapes (it cuts the part on
  purpose); anywhere else it still is.
- Qt font use no longer crashes when a console-only Qt application already exists.
- Holes in sketch profiles were attached with a fixed reversal, which only worked when the
  hole was drawn the same way round as its outline; font outlines (drawn the other way)
  came out filled. Loops are now oriented by their signed area.
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
