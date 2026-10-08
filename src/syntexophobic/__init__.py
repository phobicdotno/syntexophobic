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
    ap.add_argument("path", nargs="?", help="project folder or STEP file to open")
    args, _ = ap.parse_known_args()

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
