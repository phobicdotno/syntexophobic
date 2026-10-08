"""GRBL post-processor (Carbide Motion, Shapeoko, Nomad and other GRBL 1.1 machines)."""

from __future__ import annotations

from .cam import Toolpath
from .job import Job


def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def grbl(job: Job, toolpaths: list[Toolpath], stock) -> str:
    """stock is the cam.Setup stock Box; it sets work zero when machine.zero == "stock"."""
    if job.machine.zero == "stock":
        ox, oy, oz = stock.xmin, stock.ymin, stock.zmax
        zero_note = "X0 Y0 = stock front-left corner, Z0 = stock top"
    else:
        ox = oy = oz = 0.0
        zero_note = "X0 Y0 Z0 = model origin"
    out = [
        f"(Syntexophobic - {job.name})",
        f"(GRBL, mm, absolute. Work zero: {zero_note})",
        f"(Stock {_num(stock.xmax - stock.xmin)} x {_num(stock.ymax - stock.ymin)} x {_num(stock.zmax - stock.zmin)} mm)",
        "G21 G90 G17 G94",
    ]
    current_tool = None
    for tp in toolpaths:
        tool = job.tool(tp.op.tool)
        out.append(f"({tp.op.name}: T{tool.id} {tool.name}, D{_num(tool.diameter)})")
        if current_tool is not None and current_tool != tool.id:
            # GRBL has no tool changer: stop, let the operator swap and re-zero Z.
            out += ["M5", f"(Change to T{tool.id} {tool.name}, re-zero Z, then resume)", "M0"]
        if current_tool != tool.id:
            out += [f"M3 S{job.machine.rpm}", "G4 P3"]
            current_tool = tool.id
        last = {"X": None, "Y": None, "Z": None, "F": None}
        for m in tp.moves:
            words = []
            for axis, v, o in (("X", m.x, ox), ("Y", m.y, oy), ("Z", m.z, oz)):
                if v is not None:
                    v -= o
                if v is not None and _num(v) != last[axis]:
                    last[axis] = _num(v)
                    words.append(axis + last[axis])
            if not words:
                continue
            if m.kind == "rapid":
                out.append("G0 " + " ".join(words))
            else:
                if m.f is not None and _num(m.f) != last["F"]:
                    last["F"] = _num(m.f)
                    words.append("F" + last["F"])
                out.append("G1 " + " ".join(words))
    out += ["M5", "M30", ""]
    return "\n".join(out)


def read(text: str, job: Job, stock) -> list[Toolpath]:
    """Parse G-code written by grbl() back into toolpaths in model coordinates.

    Used to check the post-processor end to end: what the machine would actually do is
    simulated, not the moves the CAM code meant to write. Operation blocks are found by
    the "(name: Tn ..., D<diameter>)" comment the post writes before each one.
    """
    import re

    from .cam import Move, Toolpath
    from .job import Operation

    if job.machine.zero == "stock":
        ox, oy, oz = stock.xmin, stock.ymin, stock.zmax
    else:
        ox = oy = oz = 0.0
    header = re.compile(r"^\((.+): T(\d+) .*, D([\d.]+)\)$")
    paths: list[Toolpath] = []
    motion, feed = "G0", None
    for raw in text.splitlines():
        line = raw.strip()
        m = header.match(line)
        if m:
            paths.append(Toolpath(Operation(name=m.group(1), tool=int(m.group(2))), float(m.group(3))))
            continue
        line = re.sub(r"\(.*?\)", "", line).strip()
        if not line or not paths:
            continue
        words = dict((w[0], float(w[1:])) for w in line.split() if w[0] in "GXYZF" and len(w) > 1)
        if "G" in words and words["G"] in (0, 1):
            motion = "G0" if words["G"] == 0 else "G1"
        if "F" in words:
            feed = words["F"]
        if not any(a in words for a in "XYZ"):
            continue
        paths[-1].moves.append(Move(
            "rapid" if motion == "G0" else "feed",
            words["X"] + ox if "X" in words else None,
            words["Y"] + oy if "Y" in words else None,
            words["Z"] + oz if "Z" in words else None,
            feed if motion == "G1" else None))
    return paths
