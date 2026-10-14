# Changelog

Each release has a version and a build number; Help → About shows a one-line summary of
every release, read from this file.

## Unreleased

**History**
- A Subway tab in the History panel: each variant is a coloured line, each saved version a
  station. Hover a station to see its note and what it changed (files and changed lines);
  click it to pick that version.

**Project files**
- Projects are kept in a single `.cccp` file holding the project and its whole git history,
  variants included. New projects and designs ask where to save their `.cccp`; Open reads
  `.cccp` files and old project folders; File > Save project as… turns a folder into one.

## v0.7.0 (build 11) — 2026-10-13

**Design workspace**
- The window follows Fusion's layout. The sketch tools are now a ribbon — Create, Modify,
  Constraints and Inspect — with the drawing tools, constraints and a Finish Sketch button
  grouped under captions.
- The feature list is a browser tree: Origin planes, Sketches and the feature history, each
  with its icon. Double-click a plane for a new sketch on it, a sketch to edit it, a feature
  to open its sketch.
- Creating features (Extrude, Revolve, Loft, Sweep, Hole, Fillet, Chamfer, Shell, Mirror,
  Relief, Insert) moves onto a tool ribbon under the app bar that swaps between the Design
  and Manufacture workspaces. The Claude assistant panel is unchanged.

**3D view**
- A ViewCube in the top-right corner: click a face, edge or corner to turn the model to it.
- Front, Back, Left, Right, Top, Bottom and Home (iso) views, and Fit, on the app bar and
  in the View menu.
- Visual style: shaded, shaded with edges (the default), or wireframe, under View > Visual style.

**Look**
- A dark theme throughout, and a new icon set — every toolbar, sketch, constraint and
  feature icon redrawn in one drafting style, drawn in the window's text colour.

## v0.6.3 (build 10) — 2026-10-13

**Install**
- Syntexophobic runs on macOS and Windows too. Every release now has three downloads:
  the Linux AppImage, `Syntexophobic-<version>-macOS-arm64.zip` (the app for Apple silicon,
  macOS 12 or later) and `Syntexophobic-<version>-windows-x64.zip` (a folder with
  `Syntexophobic.exe`). Each carries its own Python and libraries; the Windows build also
  brings git (MinGit), used when the machine has none. Neither is signed: the Mac's first
  start needs System Settings → Privacy & Security → *Open Anyway*, Windows' SmartScreen
  *More info* → *Run anyway*.
- Releases are built by GitHub Actions: a version tag builds all three, starts each one
  as a check, and publishes them with this changelog's section as the notes.

**Updates**
- On a Mac, *Update* downloads the new app and puts it in place of the running one (the
  old one is kept in `~/Library/Caches/syntexophobic/previous`), then *Restart now*. On
  Windows the update bar says where to download the new version, as Windows will not
  replace the files of a running program.

**Claude**
- `install-mcp` registers the Mac app's or Windows build's own command. On Windows the
  MCP server reaches the open window through a named pipe (Unix sockets elsewhere).

**Fixes**
- Files copied into a project (imports, pictures) are recorded with `/`, so a project
  made on Windows opens on Linux and macOS.

**Testing**
- The tests run on Linux, macOS and Windows for every pull request; the window tests on
  Linux and Windows (GitHub's Macs have no GPU for VTK). The macOS updater swaps the app and
  keeps the old one; each platform picks its own download from a release.

## v0.6.2 (build 9) — 2026-10-09

**Fixes**
- Full screen and maximize work: with the Design and Job panels both open at start, the
  window asked for at least 1010 px of height, more than a laptop screen, so the window
  manager would not maximize it or make it full screen. Only the current workspace's
  panel shows now, and tall panels scroll instead of stretching the window.
- A failing MCP tool tells Claude why it failed instead of a bare "Error executing tool".

**Interface**
- View → *Full screen* (F11).
- The design side is easy to find: *New design…* (Ctrl+Shift+N) is on the toolbar, and the
  Design workspace shows what to do when no designed part is open (it was all greyed out
  at start, and showed only a note for a STEP project).
- *Design on this part* (Design workspace, STEP or STL project): the model becomes the
  first feature of a design, an Import copied into `imports/`, so sketches, cuts, holes
  and fillets can be added to it. The job and history stay.

**Assistant**
- Pictures go with a message by pasting (Ctrl+V), dropping a file, *Attach image…* (several
  at once, AVIF/HEIC/BMP/TIFF converted to PNG for Claude) or *Take photo…* with a camera;
  they show as thumbnails under the chat (click one to remove it) and in the conversation.
- Links in a message are read by Syntexophobic (Claude still has no web): the page's title,
  description and main picture go with the message. A page that cannot be read (shops that
  block it) is shown as such and Claude works from the address and the pictures.
- A picture or link of a router bit adds it to the tool library (MCP `library_add`,
  `library_list`), in the size you say you own; the chat shows what was added. Saving a
  tool that the library already has (same diameter, flutes and shape) no longer duplicates it.

**Updates**
- An *Update available* bar above the view when GitHub has a newer release: what changed
  (*What's new* shows the release notes), *Update*, *Skip this version*, or ✕ until the next
  start. In the AppImage, *Update* downloads the new AppImage next to the running one and
  points `~/Applications/Syntexophobic.AppImage` at it, then *Restart now*; the old file is
  kept. Only what changed is downloaded: the release carries a block index
  (`<AppImage>.blocks`, written by `tools/build-appimage.sh`), the updater finds every block
  the running AppImage already has, even where it moved, and fetches the rest with range
  requests (0.5.1 to 0.6.0: 1.9 MB instead of 314 MB). The result is checked against the
  release's SHA-256; without an index, or if anything does not match, it downloads the
  whole file. Otherwise it shows the command that updates (`uv tool upgrade`, or `git pull`
  in a clone). Checked at start and every 6 hours (Help → *Check for updates at start*), or
  with Help → *Check for updates…*. The repository is private, so it asks with your GitHub
  login (`GH_TOKEN`/`GITHUB_TOKEN`, `gh auth login`, or the token git stores for
  github.com); without one it stays quiet.

**Fault reports**
- When Claude in the Assistant panel hits a failing tool, gives up on what you asked, or
  calls the new MCP `report_problem`, the program files a GitHub issue (label
  `fault-report`) with the version and build, OS, your request, every tool call with its
  arguments and result, Claude's answer, the window's screenshot and the end of the new log
  (`~/.cache/syntexophobic/syntexophobic.log`). The same fault again is added to its open
  issue instead of a new one, and the chat links the issue. Uses your GitHub login like the
  update check does; without one nothing is sent. `SYNTEXOPHOBIC_NO_FAULT_REPORTS=1`
  turns it off. Failed tool calls now show in the chat in red, and a tool that fails
  without a message tells Claude so instead of returning nothing.

**Install**
- The program icon shows in the dock, the window switcher and the app grid. GNOME takes a
  window's icon only from a menu entry that matches it, so the AppImage now adds its own
  entry and icon to ~/.local/share/applications and ~/.local/share/icons when it starts
  (updated when the AppImage moves; an entry you wrote yourself is left alone; set
  SYNTEXOPHOBIC_NO_DESKTOP_ENTRY=1 to skip it). The window's class is now
  syntexophobic / Syntexophobic, and the .AppImage file carries an icon for file managers.

**Testing**
- Fault reports: the box chat from the first report (new_design failing three times) gives
  one report with the whole story; the same fault in other folders goes to the same issue,
  a closed issue gets a new one; normal answers are not reported.
- Updates: version order, drafts and pre-releases left out, no access, a cut download, the
  link moved to the new AppImage, the bar's skip / hide / installed states; delta updates
  rebuild the new file byte for byte from moved blocks, and a bad block falls back to the
  whole download.
- A STEP project turned into a design: same part and job, then a sketch cut through it.
- Assistant: pasted and dropped pictures, AVIF conversion, page titles and pictures from
  links, unreadable links, library additions without duplicates.

## v0.6.1 (build 8) — 2026-10-09

**Fixes**
- New from STEP / STL and New design did nothing on a computer where git has no name and
  email set (a fresh Ubuntu install): the first history commit failed and the error was
  swallowed. Commits now fall back to a built-in identity when git has none; without git
  installed at all, projects open with history turned off and the History panel says how
  to install it. Any other failure creating a project shows an error instead of nothing.
- The AppImage carries its own git, used when the computer has none, so version history
  works there too.

## v0.6.0 (build 7) — 2026-10-09

**Design**
- Import feature (Design panel *Import…*, MCP `import_model`): a STEP or STL part becomes a
  body where its own coordinates put it: a new body, or joined to, cut from or intersected
  with one. The file is copied into the project's `imports/` folder so history keeps it,
  and read once per change.

**CAM**
- Engrave operation: the tool's centre follows a sketch's or DXF's lines with no offset,
  open lines included (pieces that meet are joined, shortest travel between them), down to
  a depth in stepdown passes; open lines are cut back and forth without lifting. Like
  V-carving, the engraved band is expected in the simulation's gouge check.
- Engraving and V-carving depth counts from the part's surface under the lines (a point
  dropped on its mesh), not from the stock top: with material above the part, roughing
  faced it off first and a 0.3 mm engraving was cut entirely in the removed layer. A line
  on a pocket floor is cut into the floor; a line crossing a step is cut at its highest
  surface (following it down a wall would cut the wall with the side of the V-bit).

**Sketch**
- Single-line text (sketch Text tool, MCP `stroke_text`): Hershey lettering in nine styles
  (futural, script, cursive, times, gothic...) as open strokes that engraving cuts once,
  where outline text gets both sides of every letter cut.

**Simulation**
- The rapid check uses the tool's real shape: lifting a V-bit out of its groove or a ball
  end out of its slot is no longer reported as a rapid into stock (a full-diameter flat
  cylinder "hit" the groove walls); moving sideways at depth still is.

**Interface**
- Playback draws the cutter in its own shape: flat, ball end, or a V-bit's cone.

**Fixes**
- Exporting a design as STEP no longer prints OpenCascade's transfer statistics.

**Testing**
- Import: an STL as a body, a STEP cutting it, the saved project rebuilt and machined.
- Single-line text: cap height, every stroke engraved exactly once.
- Engrave: on the lines, depth, no lifts on open lines, only the band cut; rapids with
  V-bits and ball ends; depth from the part surface with a faced-off top. Six new mutants (24 in all); `tools/mutants.py <words>` runs
  only the matching ones.

## v0.5.1 (build 6) — 2026-10-09

**Install**
- The AppImage keeps only the parts of Qt and VTK the program uses (the Qt and VTK modules
  it imports, the Qt plugins a desktop window needs, and what those link against):
  Qt's web engine, QML, multimedia and 3D and most of VTK are left out.

**Fixes**
- The 3D view refits when the part grows beyond the view: a design started from an empty
  sketch showed one corner of the part, magnified.

## v0.5.0 (build 5) — 2026-10-09

**Install**
- One-file install: `Syntexophobic-<version>-x86_64.AppImage` bundles its own Python and every
  library (about 500 MB). Download, `chmod +x`, run; nothing else to install.
  `tools/build-appimage.sh` builds it.
- From the AppImage, `install-mcp` and the Assistant start the MCP server from the AppImage
  file itself.

**Fixes**
- `syntexophobic part.step` and `syntexophobic <project>` were taken for an unknown
  command; a path now opens as documented.
- Converting STL to STEP no longer prints OpenCascade's transfer statistics.

**Testing**
- Command line paths; the AppImage checked in a clean environment (version, convert, new
  project, simulate, window driven over the control socket, MCP server).

## v0.4.0 (build 4) — 2026-10-09

**About**
- Help → About shows the logo, the version and build, and "What's new": one line per
  release with the areas it touched, read from this changelog, so it cannot go stale.
- `syntexophobic --version`.

**Files**
- STL → STEP converter: File > Convert STL to STEP…, `syntexophobic convert in.stl
  out.step` and the MCP tool `convert_stl`. The mesh becomes a closed faceted solid that
  any CAD program opens. STL files also open directly as CAM projects.

**CAM**
- Flat levels are grouped by height (within 0.05 mm) and need a real area (0.2 % of the
  part's footprint): a relief or other faceted solid no longer gets a cutting level for
  every nearly flat triangle (an image relief went from 171 to 9.5 minutes of adaptive).

**Testing**
- About: the changelog summary parser, the newest entry matching the version, the dialog.
- STL round trip: mesh → STEP → volume.

## v0.3.0 (build 3) — 2026-10-09

**Claude**
- MCP server (`syntexophobic mcp`, 36+ tools: projects, parameters, sketches, features,
  stock, tools, operations, toolpaths, simulation, G-code, history, views, screenshots).
  With the window open every call runs in it live (local socket, this user only);
  otherwise it works on the project files. `syntexophobic install-mcp` registers it with
  Claude Code. It uses Claude Code's own login: no API key.
- Assistant panel (View > Assistant, Ctrl+K): chat with Claude inside the program with
  only Syntexophobic's tools (plus Read for attached pictures); its tool calls drive the
  window as you watch.

**Images**
- Trace image (sketch toolbar, MCP): a picture's dark or light areas become closed
  outlines with their holes, ready to extrude, cut or V-carve.
- Relief feature: brightness becomes height, built as a watertight faceted solid
  (about 2 s for a 120 × 120 grid), for 3D finishing.
- Attach image in the Assistant: Claude looks at the picture and designs a figure from it.

**Design**
- Multiple bodies: "new" makes a separate body; join, intersect, fillet, chamfer, shell and
  mirror change a named body; a cut or hole without one goes through all of them.
- Loft and sweep; Hole feature (simple, counterbore, countersink).
- Named parameters and safe arithmetic expressions in dimensions and feature values.
- Picking edges and faces in the 3D view (stored as `@x,y,z`, found again after a rebuild);
  sketch on any flat face, slanted ones included.
- Undo / redo; Measure; Section view; STEP / STL export.

**Sketch**
- Splines, text in any font, corner fillet, offset, mirror, trim, polygon, slot.
- DXF import and export (polylines with bulges, splines, ellipses, inch drawings).

**CAM**
- Adaptive clearing (steered constant engagement, guaranteed full clearing of each level).
- 3D finishing with an exact drop-cutter on the part's mesh (flat and ball ends), ball-end
  waterline finishing, V-carving with V-bits.
- Two-sided machining: operations for the bottom after flipping the part over X, one G-code
  file per side.
- Tool library shared by all projects; exact stock sizes; G2/G3 arcs (within 5 µm).
- Stay low between passes when the hop is safe; rapid time in estimates.

**Simulation**
- Machine-order simulation that checks every rapid against the stock; gouge depth measured
  along the surface normal; playback slider; both faces of the stock for two-sided jobs.

**Fixes**
- 3D finishing cut up to 0.7 mm into gentle curves (a point simplifier compared only
  neighbours): now Douglas-Peucker. Found on a real enclosure.
- Loop outlines chained from the wrong end of a segment (an "N" carved as a filled box),
  holes attached with a fixed reversal (text came out filled).
- Roughing could not reach stock narrower than the tool keep-out beside the part, so later
  moves plunged into it; found the first time Claude drove the window and simulated.
- Adaptive could leave material that later rapids hit.
- An empty OpenCascade result no longer crashes the program later.

**Testing**
- `tools/mutants.py`: known bugs are put back one at a time; the tests must catch each.
  Two tests were found blind this way and strengthened.

## v0.2.0 (build 2) — 2026-10-08

**CAM**
- Ramp entry along the path; profile cut-outs with holding tabs; drilling (full-retract
  pecks) and helical boring with hole detection; rest machining after a bigger tool.
- Feeds and speeds suggestions from the stock material.
- Fast mesh slicing (about 15×) with exact OpenCascade sections as the check.

**Simulation**
- Stock removal simulation with a gouge check against the part; G-code read back and
  simulated, so the post-processor is checked end to end.

**G-code**
- `carbide` post (default) matching Carbide Create's own shapeoko.post: `M6 T<n>` for
  every tool (BitSetter), `G53 G0 Z-5` safe retracts, `M2`; `grbl` kept for other machines.

**Design**
- Sketch solver with 20 constraint types and degrees-of-freedom reporting; parametric
  features (extrude, revolve, patterns, fillet, chamfer, shell, mirror); a Design
  workspace with a sketch editor; designed parts go straight to CAM.

**Interface**
- Fusion-style mouse, 3Dconnexion SpaceMouse support through spacenavd, black-and-white
  toolbar icons with icon / large icon / text-only modes, app icon and launcher.
- Pluggable compute backends (numpy, CuPy).

## v0.1.0 (build 1) — 2026-10-08

**First version**
- STEP import with any axis up, Z-level roughing and wall finishing, GRBL output with a
  stock-corner work zero, git-style history (versions, variants, diffs, restore), a Qt/VTK
  window with a 3D preview, and a command line.
