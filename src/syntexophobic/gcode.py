"""GRBL post-processor (Carbide Motion, Shapeoko, Nomad and other GRBL 1.1 machines)."""

from __future__ import annotations

from .cam import Move, Toolpath
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


def _with_arcs(moves):
    """Moves with runs of flat feed moves (same Z and feed) replaced by fitted arcs."""
    from .arcs import fit
    out, run = [], []
    x = y = z = None

    def flush():
        nonlocal run
        if len(run) >= 4:
            pts = [p for p, _ in run]
            f = run[-1][1]
            start = pts[0]
            for piece in fit(pts):
                if piece[0] == "line":
                    out.append(Move("feed", piece[1][0], piece[1][1], None, f))
                    start = piece[1]
                else:
                    out.append(("arc", start, piece[1], piece[2], piece[3], f))
                    start = piece[1]
        else:
            out.extend(Move("feed", p[0], p[1], None, f) for p, f in run[1:])
        run = []

    for m in moves:
        nx = x if m.x is None else m.x
        ny = y if m.y is None else m.y
        nz = z if m.z is None else m.z
        flat_feed = m.kind == "feed" and m.z is None or (m.kind == "feed" and m.z == z)
        if flat_feed and x is not None and (not run or run[-1][1] == m.f):
            if not run:
                run = [((x, y), m.f)]
            run.append(((nx, ny), m.f))
        else:
            flush()
            out.append(m)
        x, y, z = nx, ny, nz
    flush()
    return out


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
        moves = _with_arcs(tp.moves) if job.machine.arcs else tp.moves
        for m in moves:
            if isinstance(m, tuple):                      # ("arc", start, end, centre, ccw, f)
                _, (sx, sy), (ex, ey), (cx, cy), ccw, f = m
                words = [f"X{_num(ex - ox)}", f"Y{_num(ey - oy)}", f"I{_num(cx - sx)}", f"J{_num(cy - sy)}"]
                if f is not None and _num(f) != last["F"]:
                    last["F"] = _num(f)
                    words.append("F" + last["F"])
                out.append(("G3 " if ccw else "G2 ") + " ".join(words))
                last["X"], last["Y"] = _num(ex - ox), _num(ey - oy)
                continue
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
    here = [0.0, 0.0, 0.0]                       # tool position in model coordinates
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
        parts = [w for w in line.split() if len(w) > 1 and w[0] in "GXYZFIJ"]
        gcodes = [float(w[1:]) for w in parts if w[0] == "G"]
        words = {w[0]: float(w[1:]) for w in parts if w[0] != "G"}
        if 53 in gcodes:
            # machine-coordinate retract (G53 G0 Z-5): somewhere above everything
            if paths:
                paths[-1].moves.append(Move("rapid", None, None, stock.zmax + 100.0))
            here[2] = stock.zmax + 100.0
            continue
        for g in gcodes:
            if g in (0, 1, 2, 3):
                motion = {0: "G0", 1: "G1", 2: "G2", 3: "G3"}[g]
        if "F" in words:
            feed = words["F"]
        if not any(a in words for a in "XYZ"):
            continue
        if motion in ("G2", "G3"):
            # expand the arc into short moves (about 0.1 mm or 3° apart)
            import math
            sx, sy = here[0], here[1]
            ex = words["X"] + ox if "X" in words else sx
            ey = words["Y"] + oy if "Y" in words else sy
            cx, cy = sx + words.get("I", 0.0), sy + words.get("J", 0.0)
            r = math.dist((sx, sy), (cx, cy))
            a0 = math.atan2(sy - cy, sx - cx)
            a1 = math.atan2(ey - cy, ex - cx)
            sweep = (a1 - a0) % (2 * math.pi) if motion == "G3" else -((a0 - a1) % (2 * math.pi))
            if abs(sweep) < 1e-12:
                sweep = 2 * math.pi if motion == "G3" else -2 * math.pi
            n = max(2, int(abs(sweep) * r / 0.1), int(abs(sweep) / math.radians(3)))
            for k in range(1, n + 1):
                a = a0 + sweep * k / n
                paths[-1].moves.append(Move("feed", cx + r * math.cos(a), cy + r * math.sin(a), None, feed))
            here[0], here[1] = ex, ey
            continue
        for k, axis, o in ((0, "X", ox), (1, "Y", oy), (2, "Z", oz)):
            if axis in words:
                here[k] = words[axis] + o
        paths[-1].moves.append(Move(
            "rapid" if motion == "G0" else "feed",
            words["X"] + ox if "X" in words else None,
            words["Y"] + oy if "Y" in words else None,
            words["Z"] + oz if "Z" in words else None,
            feed if motion == "G1" else None))
    return paths
