"""Syntexophobic: 2.5D CAM for GRBL machines, with git-style job history."""

import argparse
import sys

__version__ = "0.1.0"


def main() -> int:
    ap = argparse.ArgumentParser(prog="syntexophobic", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("gcode", help="generate G-code for a project without opening the window")
    g.add_argument("project", help="project folder (contains job.toml)")
    g.add_argument("-o", "--output", help="output file (default: <name>.nc in the project)")
    n = sub.add_parser("new", help="create a project folder from a STEP file")
    n.add_argument("step")
    n.add_argument("folder", nargs="?")
    sm = sub.add_parser("simulate", help="simulate the toolpaths and check for gouges (exit 1 if any)")
    sm.add_argument("project")
    sm.add_argument("--res", type=float, default=0.25, help="grid size in mm (default 0.25)")
    sm.add_argument("--gcode", help="simulate this G-code file instead of the project's toolpaths")
    sub.add_parser("mcp", help="MCP server on stdio, for Claude Code (drives the open window, or works headless)")
    sub.add_parser("install-mcp", help="register the MCP server with Claude Code (user scope)")
    sub.add_parser("backends", help="list compute backends and the devices they would use")
    ap.add_argument("path", nargs="?", help="project folder or STEP file to open")
    ap.add_argument("--backend", help="compute backend: auto (default), numpy, cupy")
    args, _ = ap.parse_known_args()
    if args.backend:
        import os
        os.environ["SYNTEXOPHOBIC_BACKEND"] = args.backend

    if args.cmd == "mcp":
        from .mcp_server import main as serve
        serve()
        return 0
    if args.cmd == "install-mcp":
        import shutil
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        cmd = ["claude", "mcp", "add", "--scope", "user", "syntexophobic", "--",
               uv, "run", "--project", str(root), "syntexophobic", "mcp"]
        print(" ".join(cmd))
        return subprocess.call(cmd)
    if args.cmd == "backends":
        from . import compute
        for name, device in compute.available().items():
            print(f"{name:8} {device}")
        print(f"active:  {compute.get().name}")
        return 0

    if args.cmd == "gcode":
        from pathlib import Path
        from .project import Project
        p = Project(args.project)
        tps = p.toolpaths()
        out = Path(args.output or p.folder / f"{p.job.name}.nc")
        out.write_text(p.gcode(tps))
        for tp in tps:
            print(f"{tp.op.name}: {len(tp.levels)} levels, {tp.minutes():.1f} min")
        print(f"wrote {out}")
        return 0
    if args.cmd == "simulate":
        from .project import Project
        from . import gcode, simulate
        p = Project(args.project)
        if args.gcode:
            from pathlib import Path
            tps = gcode.read(Path(args.gcode).read_text(), p.job, p.setup().stock)
        else:
            tps = p.toolpaths()
        r = simulate.simulate(p.setup(), tps, res=args.res)
        print(f"grid {r.grid.nx} x {r.grid.ny} at {args.res} mm, {r.removed_cm3:.1f} cm³ removed")
        bad = False
        if r.gouge_cells:
            print(f"GOUGE: up to {r.gouge_depth:.3f} mm into the part in {r.gouge_cells} cells")
            bad = True
        if r.rapid_hits:
            name, (x, y, z), d = max(r.rapid_hits, key=lambda h: h[2])
            print(f"RAPID INTO STOCK: {len(r.rapid_hits)} rapids hit material; worst {d:.2f} mm in {name} "
                  f"at X{x:.2f} Y{y:.2f} Z{z:.2f}")
            bad = True
        if not bad:
            print("no gouges, no rapids into stock")
        return 1 if bad else 0
    if args.cmd == "new":
        from pathlib import Path
        from .project import Project
        step = Path(args.step)
        p = Project.create(args.folder or step.with_name(step.stem + "-cam"), step)
        print(f"created {p.folder}")
        return 0

    from .app import run
    return run(args.path)


if __name__ == "__main__":
    sys.exit(main())
