"""2D sketches: geometry, constraints and a solver.

A sketch is points, lines, circles and arcs on a plane, plus constraints between them.
Every point coordinate and circle/arc radius is an unknown; every constraint is one or
more equations that should be zero. A damped Gauss-Newton (Levenberg-Marquardt) solve
moves the unknowns the least amount needed to satisfy them all, so dragging or editing
a value keeps the rest of the sketch where it was.

Sketches are stored as plain TOML (one entity or constraint per table) so they diff well
in the project's git history, like job.toml.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---- entities ------------------------------------------------------------------------


@dataclass
class Point:
    id: str
    x: float
    y: float
    construction: bool = False


@dataclass
class Line:
    id: str
    a: str            # point ids
    b: str
    construction: bool = False


@dataclass
class Circle:
    id: str
    center: str
    r: float
    construction: bool = False


@dataclass
class Arc:
    """Counter-clockwise from start to end around center. |start-c| = |end-c| = r."""
    id: str
    center: str
    start: str
    end: str
    r: float
    construction: bool = False


@dataclass
class Constraint:
    type: str
    refs: list[str]
    value: float | None = None
    id: str = ""


# Each constraint type: which entity kinds it takes, and how many equations it adds.
CONSTRAINT_TYPES = {
    "fixed": "point",                    # point stays where it is now (value unused)
    "coincident": "point, point",
    "horizontal": "line",
    "vertical": "line",
    "parallel": "line, line",
    "perpendicular": "line, line",
    "equal": "line, line | circle/arc, circle/arc",
    "distance": "point, point | point, line | line (length)",
    "horizontal_distance": "point, point",
    "vertical_distance": "point, point",
    "radius": "circle/arc",
    "diameter": "circle/arc",
    "angle": "line, line (degrees, a to b counter-clockwise)",
    "point_on_line": "point, line",
    "point_on_circle": "point, circle/arc",
    "midpoint": "point, line",
    "tangent": "line, circle/arc | circle/arc, circle/arc",
    "concentric": "circle/arc, circle/arc",
    "symmetric": "point, point, line (mirror line)",
    "x": "point (value = X)",
    "y": "point (value = Y)",
}


class SolveError(RuntimeError):
    pass


@dataclass
class SolveResult:
    ok: bool
    iterations: int
    residual: float
    dof: int                       # remaining degrees of freedom (0 = fully constrained)
    conflicting: list[str]         # constraint ids that are not satisfied


@dataclass
class Sketch:
    name: str = "Sketch"
    plane: str = "XY"              # XY | XZ | YZ
    offset: float = 0.0            # along the plane normal
    points: dict[str, Point] = field(default_factory=dict)
    lines: dict[str, Line] = field(default_factory=dict)
    circles: dict[str, Circle] = field(default_factory=dict)
    arcs: dict[str, Arc] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    _next: int = 1

    # ---- building ---------------------------------------------------------------------

    def _id(self, prefix: str) -> str:
        while True:
            i = f"{prefix}{self._next}"
            self._next += 1
            if i not in self.points and i not in self.lines and i not in self.circles and i not in self.arcs:
                return i

    def point(self, x: float, y: float, construction: bool = False) -> str:
        p = Point(self._id("p"), float(x), float(y), construction)
        self.points[p.id] = p
        return p.id

    def line(self, a, b, construction: bool = False) -> str:
        """a and b are point ids or (x, y) tuples."""
        a = a if isinstance(a, str) else self.point(*a)
        b = b if isinstance(b, str) else self.point(*b)
        ln = Line(self._id("l"), a, b, construction)
        self.lines[ln.id] = ln
        return ln.id

    def circle(self, center, r: float, construction: bool = False) -> str:
        c = center if isinstance(center, str) else self.point(*center)
        ci = Circle(self._id("c"), c, float(r), construction)
        self.circles[ci.id] = ci
        return ci.id

    def arc(self, center, start, end, construction: bool = False) -> str:
        c = center if isinstance(center, str) else self.point(*center)
        s = start if isinstance(start, str) else self.point(*start)
        e = end if isinstance(end, str) else self.point(*end)
        P = self.points
        r = math.dist((P[c].x, P[c].y), (P[s].x, P[s].y))
        a = Arc(self._id("a"), c, s, e, r, construction)
        self.arcs[a.id] = a
        return a.id

    def rectangle(self, x0, y0, x1, y1) -> list[str]:
        """Four lines with coincident corners and horizontal/vertical constraints."""
        p = [self.point(x0, y0), self.point(x1, y0), self.point(x1, y1), self.point(x0, y1)]
        ls = [self.line(p[i], p[(i + 1) % 4]) for i in range(4)]
        for i, kind in enumerate(("horizontal", "vertical", "horizontal", "vertical")):
            self.constrain(kind, ls[i])
        return ls

    def constrain(self, type: str, *refs: str, value: float | None = None) -> str:
        if type not in CONSTRAINT_TYPES:
            raise ValueError(f"Unknown constraint {type!r}")
        c = Constraint(type, list(refs), value, id=f"k{len(self.constraints) + 1}")
        if type == "fixed" and value is None:
            p = self.points[refs[0]]
            c.value = None
            c.refs = [refs[0], f"{p.x!r}", f"{p.y!r}"]
        self.constraints.append(c)
        return c.id

    def delete(self, eid: str) -> None:
        """Remove an entity (and the constraints and lines that use it)."""
        for store in (self.lines, self.circles, self.arcs):
            store.pop(eid, None)
        if eid in self.points:
            del self.points[eid]
            for ln in [l for l in self.lines.values() if eid in (l.a, l.b)]:
                del self.lines[ln.id]
            for c in [c for c in self.circles.values() if c.center == eid]:
                del self.circles[c.id]
            for a in [a for a in self.arcs.values() if eid in (a.center, a.start, a.end)]:
                del self.arcs[a.id]
        alive = set(self.points) | set(self.lines) | set(self.circles) | set(self.arcs)
        self.constraints = [c for c in self.constraints
                            if all(r in alive or _is_number(r) for r in c.refs)]

    # ---- the unknowns -----------------------------------------------------------------

    def _layout(self):
        """Index of every unknown: point x/y and circle/arc radius."""
        idx = {}
        for pid in self.points:
            idx[(pid, "x")] = len(idx)
            idx[(pid, "y")] = len(idx)
        for cid in list(self.circles) + list(self.arcs):
            idx[(cid, "r")] = len(idx)
        return idx

    def _vector(self, idx) -> np.ndarray:
        v = np.zeros(len(idx))
        for (eid, k), i in idx.items():
            if k == "r":
                v[i] = (self.circles.get(eid) or self.arcs[eid]).r
            else:
                v[i] = getattr(self.points[eid], k)
        return v

    def _store(self, idx, v) -> None:
        for (eid, k), i in idx.items():
            if k == "r":
                (self.circles.get(eid) or self.arcs[eid]).r = float(v[i])
            else:
                setattr(self.points[eid], k, float(v[i]))

    # ---- equations --------------------------------------------------------------------

    def _equations(self, idx):
        """[(constraint id, f(v) -> residual array)] for every constraint plus arc radii."""
        P = lambda pid: (idx[(pid, "x")], idx[(pid, "y")])          # noqa: E731
        R = lambda cid: idx[(cid, "r")]                              # noqa: E731

        def centre(eid):
            return P((self.circles.get(eid) or self.arcs[eid]).center)

        def ends(lid):
            ln = self.lines[lid]
            return P(ln.a), P(ln.b)

        def vec(v, a, b):
            return v[b[0]] - v[a[0]], v[b[1]] - v[a[1]]

        eqs = []
        for a in self.arcs.values():                    # arc end points stay on the arc
            c, s, e, r = P(a.center), P(a.start), P(a.end), R(a.id)
            eqs.append((a.id, lambda v, c=c, s=s, e=e, r=r: np.array([
                math.hypot(*vec(v, c, s)) - v[r], math.hypot(*vec(v, c, e)) - v[r]])))

        for k in self.constraints:
            t, refs, val = k.type, k.refs, k.value
            f = None
            if t == "fixed":
                p, x0, y0 = P(refs[0]), float(refs[1]), float(refs[2])
                f = lambda v, p=p, x0=x0, y0=y0: np.array([v[p[0]] - x0, v[p[1]] - y0])
            elif t == "coincident":
                a, b = P(refs[0]), P(refs[1])
                f = lambda v, a=a, b=b: np.array([v[a[0]] - v[b[0]], v[a[1]] - v[b[1]]])
            elif t in ("horizontal", "vertical"):
                a, b = ends(refs[0])
                k_ = 1 if t == "horizontal" else 0
                f = lambda v, a=a, b=b, k_=k_: np.array([v[a[k_]] - v[b[k_]]])
            elif t in ("parallel", "perpendicular"):
                (a1, b1), (a2, b2) = ends(refs[0]), ends(refs[1])

                def f(v, a1=a1, b1=b1, a2=a2, b2=b2, t=t):
                    ux, uy = vec(v, a1, b1)
                    wx, wy = vec(v, a2, b2)
                    n = math.hypot(ux, uy) * math.hypot(wx, wy) or 1.0
                    return np.array([(ux * wy - uy * wx) / n if t == "parallel" else (ux * wx + uy * wy) / n])
            elif t == "equal":
                if refs[0] in self.lines:
                    (a1, b1), (a2, b2) = ends(refs[0]), ends(refs[1])
                    f = lambda v, a1=a1, b1=b1, a2=a2, b2=b2: np.array(
                        [math.hypot(*vec(v, a1, b1)) - math.hypot(*vec(v, a2, b2))])
                else:
                    r1, r2 = R(refs[0]), R(refs[1])
                    f = lambda v, r1=r1, r2=r2: np.array([v[r1] - v[r2]])
            elif t == "distance":
                if len(refs) == 1:                      # line length
                    a, b = ends(refs[0])
                    f = lambda v, a=a, b=b, d=val: np.array([math.hypot(*vec(v, a, b)) - d])
                elif refs[1] in self.lines:             # point to line
                    p, (a, b) = P(refs[0]), ends(refs[1])

                    def f(v, p=p, a=a, b=b, d=val):
                        ux, uy = vec(v, a, b)
                        px, py = vec(v, a, p)
                        n = math.hypot(ux, uy) or 1.0
                        return np.array([abs(ux * py - uy * px) / n - d])
                else:
                    a, b = P(refs[0]), P(refs[1])
                    f = lambda v, a=a, b=b, d=val: np.array([math.hypot(*vec(v, a, b)) - d])
            elif t in ("horizontal_distance", "vertical_distance"):
                a, b = P(refs[0]), P(refs[1])
                k_ = 0 if t == "horizontal_distance" else 1
                f = lambda v, a=a, b=b, k_=k_, d=val: np.array([v[b[k_]] - v[a[k_]] - d])
            elif t in ("radius", "diameter"):
                r = R(refs[0])
                d = val / 2 if t == "diameter" else val
                f = lambda v, r=r, d=d: np.array([v[r] - d])
            elif t == "angle":
                (a1, b1), (a2, b2) = ends(refs[0]), ends(refs[1])

                def f(v, a1=a1, b1=b1, a2=a2, b2=b2, ang=math.radians(val)):
                    t1 = math.atan2(*vec(v, a1, b1)[::-1])
                    t2 = math.atan2(*vec(v, a2, b2)[::-1])
                    return np.array([math.remainder(t2 - t1 - ang, 2 * math.pi)])
            elif t == "point_on_line":
                p, (a, b) = P(refs[0]), ends(refs[1])

                def f(v, p=p, a=a, b=b):
                    ux, uy = vec(v, a, b)
                    px, py = vec(v, a, p)
                    return np.array([(ux * py - uy * px) / (math.hypot(ux, uy) or 1.0)])
            elif t == "point_on_circle":
                p, c, r = P(refs[0]), centre(refs[1]), R(refs[1])
                f = lambda v, p=p, c=c, r=r: np.array([math.hypot(*vec(v, c, p)) - v[r]])
            elif t == "midpoint":
                p, (a, b) = P(refs[0]), ends(refs[1])
                f = lambda v, p=p, a=a, b=b: np.array([2 * v[p[0]] - v[a[0]] - v[b[0]],
                                                      2 * v[p[1]] - v[a[1]] - v[b[1]]])
            elif t == "tangent":
                if refs[0] in self.lines:
                    (a, b), c, r = ends(refs[0]), centre(refs[1]), R(refs[1])

                    def f(v, a=a, b=b, c=c, r=r):
                        ux, uy = vec(v, a, b)
                        px, py = vec(v, a, c)
                        return np.array([abs(ux * py - uy * px) / (math.hypot(ux, uy) or 1.0) - v[r]])
                else:
                    c1, r1, c2, r2 = centre(refs[0]), R(refs[0]), centre(refs[1]), R(refs[1])

                    def f(v, c1=c1, r1=r1, c2=c2, r2=r2):
                        d = math.hypot(*vec(v, c1, c2))
                        # outside or inside tangency, whichever is closer now
                        return np.array([min(abs(d - (v[r1] + v[r2])), abs(d - abs(v[r1] - v[r2])))])
            elif t == "concentric":
                a, b = centre(refs[0]), centre(refs[1])
                f = lambda v, a=a, b=b: np.array([v[a[0]] - v[b[0]], v[a[1]] - v[b[1]]])
            elif t == "symmetric":
                p1, p2, (a, b) = P(refs[0]), P(refs[1]), ends(refs[2])

                def f(v, p1=p1, p2=p2, a=a, b=b):
                    ux, uy = vec(v, a, b)
                    n = math.hypot(ux, uy) or 1.0
                    mx, my = (v[p1[0]] + v[p2[0]]) / 2 - v[a[0]], (v[p1[1]] + v[p2[1]]) / 2 - v[a[1]]
                    dx, dy = v[p2[0]] - v[p1[0]], v[p2[1]] - v[p1[1]]
                    return np.array([(ux * my - uy * mx) / n, (ux * dx + uy * dy) / n])
            elif t in ("x", "y"):
                p = P(refs[0])
                k_ = 0 if t == "x" else 1
                f = lambda v, p=p, k_=k_, d=val: np.array([v[p[k_]] - d])
            if f is not None:
                eqs.append((k.id, f))
        return eqs

    # ---- solving ----------------------------------------------------------------------

    def solve(self, max_iter: int = 200, tol: float = 1e-22) -> SolveResult:
        idx = self._layout()
        if not idx:
            return SolveResult(True, 0, 0.0, 0, [])
        eqs = self._equations(idx)
        v = self._vector(idx)

        def F(v):
            return np.concatenate([f(v) for _, f in eqs]) if eqs else np.zeros(0)

        def J(v, fv):
            cols = []
            for i in range(len(v)):             # central differences: accurate to ~1e-10
                h = 1e-6 * max(1.0, abs(v[i]))
                w1, w2 = v.copy(), v.copy()
                w1[i] += h
                w2[i] -= h
                cols.append((F(w1) - F(w2)) / (2 * h))
            return np.array(cols).T

        lam = 1e-3
        fv = F(v)
        it = 0
        for it in range(1, max_iter + 1):
            cost = float(fv @ fv)
            if cost < tol:
                break
            Jm = J(v, fv)
            # Damped least squares: the damping term also keeps directions no constraint
            # cares about (unconstrained geometry) where the user left them.
            A = Jm.T @ Jm + lam * np.eye(len(v))
            g = Jm.T @ fv
            step = np.linalg.solve(A, -g)
            nv = v + step
            nf = F(nv)
            if float(nf @ nf) < cost:
                v, fv, lam = nv, nf, max(lam / 3, 1e-12)
            else:
                lam *= 4
                if lam > 1e8:
                    break
        self._store(idx, v)
        residual = float(np.sqrt(fv @ fv)) if len(fv) else 0.0
        bad = [cid for cid, f in eqs if np.max(np.abs(f(v))) > 1e-7]
        dof = len(v) - (np.linalg.matrix_rank(J(v, fv), tol=1e-6) if len(fv) else 0)
        return SolveResult(not bad, it, residual, int(dof), bad)

    # ---- profiles for 3D ----------------------------------------------------------------

    def profiles(self) -> list[list[tuple]]:
        """Closed loops of non-construction edges, as lists of ("line", a, b) / ("arc", id) /
        ("circle", id) segments, walked end to end. Open chains are ignored."""
        loops = [[("circle", c.id)] for c in self.circles.values() if not c.construction]
        edges = []
        for ln in self.lines.values():
            if not ln.construction:
                edges.append((ln.a, ln.b, ("line", ln.a, ln.b)))
        for a in self.arcs.values():
            if not a.construction:
                edges.append((a.start, a.end, ("arc", a.id)))
        # merge coincident points so edges meet even when they use separate point ids
        same = {}
        for k in self.constraints:
            if k.type == "coincident":
                a, b = (same.get(r, r) for r in k.refs)
                for key, val in list(same.items()):
                    if val == b:
                        same[key] = a
                same[b] = a
        node = lambda p: same.get(p, p)                     # noqa: E731
        used = [False] * len(edges)
        for i, (a, b, seg) in enumerate(edges):
            if used[i]:
                continue
            used[i] = True
            start, cur, loop = node(a), node(b), [seg]
            while cur != start:
                nxt = next((j for j, (p, q, _) in enumerate(edges)
                            if not used[j] and cur in (node(p), node(q))), None)
                if nxt is None:
                    break
                used[nxt] = True
                p, q, s = edges[nxt]
                loop.append(s)
                cur = node(q) if node(p) == cur else node(p)
            if cur == start:
                loops.append(loop)
        return loops

    # ---- text form ----------------------------------------------------------------------

    def dumps(self) -> str:
        def val(v):
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, str):
                return '"' + v + '"'
            if isinstance(v, list):
                return "[" + ", ".join(val(x) for x in v) + "]"
            return repr(v)

        out = ["[sketch]", f'name = "{self.name}"', f'plane = "{self.plane}"', f"offset = {self.offset!r}", ""]
        for table, items in (("points", self.points), ("lines", self.lines),
                             ("circles", self.circles), ("arcs", self.arcs)):
            for e in items.values():
                out.append(f"[[{table}]]")
                out += [f"{k} = {val(v)}" for k, v in vars(e).items() if not (k == "construction" and not v)]
                out.append("")
        for c in self.constraints:
            out.append("[[constraints]]")
            out += [f"{k} = {val(v)}" for k, v in vars(c).items() if v is not None]
            out.append("")
        return "\n".join(out)

    @classmethod
    def loads(cls, text: str) -> "Sketch":
        d = tomllib.loads(text)
        s = d.get("sketch", {})
        sk = cls(name=s.get("name", "Sketch"), plane=s.get("plane", "XY"), offset=s.get("offset", 0.0))
        for p in d.get("points", []):
            sk.points[p["id"]] = Point(**p)
        for ln in d.get("lines", []):
            sk.lines[ln["id"]] = Line(**ln)
        for c in d.get("circles", []):
            sk.circles[c["id"]] = Circle(**c)
        for a in d.get("arcs", []):
            sk.arcs[a["id"]] = Arc(**a)
        sk.constraints = [Constraint(**c) for c in d.get("constraints", [])]
        sk._next = 1 + max([int("".join(ch for ch in i if ch.isdigit()) or 0)
                            for i in list(sk.points) + list(sk.lines) + list(sk.circles) + list(sk.arcs)] or [0])
        return sk

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps())

    @classmethod
    def load(cls, path: str | Path) -> "Sketch":
        return cls.loads(Path(path).read_text())


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
