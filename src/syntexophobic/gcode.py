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
