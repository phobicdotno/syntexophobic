"""GRBL post-processor (Carbide Motion, Shapeoko, Nomad and other GRBL 1.1 machines)."""

from __future__ import annotations

from .cam import Toolpath
from .job import Job


def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


POSTS = {
    # Carbide Motion (Shapeoko, Nomad): written the way Carbide Create's own shapeoko.post
    # does it. M6 T<n> for every tool, the first one too, so Carbide Motion prompts for
    # the change and a BitSetter re-measures the tool. G53 G0 Z-5 lifts to 5 mm below the
    # machine's top (homed) before tool changes, so nothing hits clamps. Ends with M2.
    "carbide": {"tool_change": "M6", "safe_retract": True, "header": "G21 G90 G17", "end": "M2"},
    # Any other GRBL 1.1 machine: M0 pause for a manual change, then re-zero Z by hand.
    "grbl": {"tool_change": "M0", "safe_retract": False, "header": "G21 G90 G17 G94", "end": "M30"},
}


def grbl(job: Job, toolpaths: list[Toolpath], stock) -> str:
    """G-code for GRBL-based machines in the dialect named by job.machine.post.

    stock is the cam.Setup stock Box; it sets work zero when machine.zero == "stock".
    """
    post = POSTS.get(job.machine.post, POSTS["grbl"])
    if job.machine.zero == "stock":
        ox, oy, oz = stock.xmin, stock.ymin, stock.zmax
        zero_note = "X0 Y0 = stock front-left corner, Z0 = stock top"
    else:
        ox = oy = oz = 0.0
        zero_note = "X0 Y0 Z0 = model origin"
    L, W, H = stock.xmax - stock.xmin, stock.ymax - stock.ymin, stock.zmax - stock.zmin
    out = [
        f"(Syntexophobic - {job.name})",
        f"(Post: {job.machine.post}. Work zero: {zero_note})",
        f"(STOCK/BLOCK,{_num(L)}, {_num(W)}, {_num(H)},{_num(ox - stock.xmin)}, {_num(oy - stock.ymin)}, {_num(oz - stock.zmin)})",
        post["header"],
    ]

    def safe_retract():
        if post["safe_retract"]:
            out.extend(["(Move to safe Z to avoid workholding)", "G53 G0 Z-5"])

    safe_retract()
    current_tool = None
    for tp in toolpaths:
        tool = job.tool(tp.op.tool)
        if current_tool != tool.id:
            if post["tool_change"] == "M6":
                if current_tool is not None:
                    out.append("M5")
                    safe_retract()
                corner = tool.diameter / 2 if tool.shape == "ball" else 0
                out += [f"(TOOL/MILL,{_num(tool.diameter)}, {_num(corner)}, 0, 0)", f"M6 T{tool.id}"]
            elif current_tool is not None:
                # GRBL has no tool changer: stop, let the operator swap and re-zero Z.
                out += ["M5", f"(Change to T{tool.id} {tool.name}, re-zero Z, then resume)", "M0"]
            out += [f"M3 S{job.machine.rpm}", "G4 P3"]
            current_tool = tool.id
        shape = f"vbit {_num(tool.angle)}" if tool.shape == "vbit" else tool.shape
        out.append(f"({tp.op.name}: T{tool.id} {tool.name}, D{_num(tool.diameter)}, {shape})")
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
    out.append("M5")
    safe_retract()
    out += [post["end"], ""]
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
    header = re.compile(r"^\((.+): T(\d+) .*, D([\d.]+)(?:, (flat|ball|vbit)(?: ([\d.]+))?)?\)$")
    paths: list[Toolpath] = []
    motion, feed = "G0", None
    for raw in text.splitlines():
        line = raw.strip()
        m = header.match(line)
        if m:
            tp = Toolpath(Operation(name=m.group(1), tool=int(m.group(2))), float(m.group(3)),
                          tool_shape=m.group(4) or "flat")
            if m.group(5):
                import math
                tp.vbit_k = math.tan(math.radians(float(m.group(5)) / 2))
            paths.append(tp)
            continue
        line = re.sub(r"\(.*?\)", "", line).strip()
        if not line or not paths:
            continue
        parts = [w for w in line.split() if len(w) > 1 and w[0] in "GXYZF"]
        gcodes = [float(w[1:]) for w in parts if w[0] == "G"]
        words = {w[0]: float(w[1:]) for w in parts if w[0] != "G"}
        if 53 in gcodes:
            # machine-coordinate retract (G53 G0 Z-5): somewhere above everything
            paths[-1].moves.append(Move("rapid", None, None, stock.zmax + 100.0)) if paths else None
            continue
        for g in gcodes:
            if g in (0, 1):
                motion = "G0" if g == 0 else "G1"
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
