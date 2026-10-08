# Changelog

All notable changes to Syntexophobic. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Hole detection: full vertical cylinders with material around them. Pocket-corner
  fillets and bosses are left out.
- `drill` operation: full-retract pecks written out move by move (GRBL has no G83),
  counted from the stock top so it is safe before roughing, with a tip allowance on
  through holes. Takes holes within -0.05/+0.3 mm of the drill unless
  `hole_min`/`hole_max` say otherwise.
- `bore` operation: helical circles from the centre outwards for holes larger than the
  endmill.
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
- Toolpaths slice a 0.01 mm mesh instead of exact B-rep sections, vectorised through the
  compute backend: a full regenerate on a 66k-triangle assembly went from about 33 s to
  1.7 s. Slices that do not close fall back to the exact section.
- Bounding box, flat levels and slices are cached per model, so regenerating after a job
  edit only redoes the toolpath maths.
- Assemblies are sliced per solid, so touching or overlapping parts stay correct.

### Fixed
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
