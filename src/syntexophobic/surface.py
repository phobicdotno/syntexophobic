"""3D finishing: parallel raster passes with an exact drop-cutter on the part's mesh.

For every point along a pass the tool is lowered until it touches the part. The tool is
tested against every nearby triangle exactly, at its corners, along its edges and on its
face (for a ball end and for a flat endmill), so nothing between sample points is
missed and nothing is left on steep walls. The mesh is fine (5 µm chordal error).
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit, prange

from .cam import Move, Toolpath
from .job import Operation


@njit(cache=True)
def _in_tri(px, py, t):
    ax, ay, bx, by, cx, cy = t[0, 0], t[0, 1], t[1, 0], t[1, 1], t[2, 0], t[2, 1]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-15:
        return False
    w0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    w1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    return w0 >= -1e-9 and w1 >= -1e-9 and 1 - w0 - w1 >= -1e-9


@njit(cache=True)
def _tip_on_triangle(x, y, t, r, ball):
    """Highest tool-tip z at (x, y) touching triangle t (3x3), or -inf: vertices, edges, face."""
    best = -1e18
    r2 = r * r
    for k in range(3):                                   # vertices
        dx, dy = t[k, 0] - x, t[k, 1] - y
        d2 = dx * dx + dy * dy
        if d2 <= r2:
            z = t[k, 2] + (math.sqrt(r2 - d2) - r if ball else 0.0)
            if z > best:
                best = z
    for k in range(3):                                   # edges
        p, q = t[k], t[(k + 1) % 3]
        ex, ey, ez = q[0] - p[0], q[1] - p[1], q[2] - p[2]
        h2 = ex * ex + ey * ey
        if h2 < 1e-18:
            continue
        wx, wy = x - p[0], y - p[1]
        if ball:
            D2 = h2 + ez * ez
            kk = wx * ex + wy * ey
            A = h2 / D2
            Bh = kk * ez / D2
            C = wx * wx + wy * wy - kk * kk / D2 - r2
            disc = Bh * Bh - A * C
            if disc < 0:
                continue
            s = (Bh + math.sqrt(disc)) / A               # sphere centre height above p
            tt = (kk + s * ez) / D2
            if 0.0 <= tt <= 1.0:
                z = p[2] + s - r
                if z > best:
                    best = z
        else:
            # disc of radius r at (x, y): the part of the edge inside it, highest end of that
            tt = (wx * ex + wy * ey) / h2
            dist2 = wx * wx + wy * wy - tt * tt * h2
            if dist2 > r2:
                continue
            half = math.sqrt((r2 - dist2) / h2)
            for u in (min(max(tt - half, 0.0), 1.0), min(max(tt + half, 0.0), 1.0)):
                if abs(tt - u) <= half + 1e-12:
                    z = p[2] + u * ez
                    if z > best:
                        best = z
    # face
    ux, uy, uz = t[1, 0] - t[0, 0], t[1, 1] - t[0, 1], t[1, 2] - t[0, 2]
    vx, vy, vz = t[2, 0] - t[0, 0], t[2, 1] - t[0, 1], t[2, 2] - t[0, 2]
    a, b, c = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = math.sqrt(a * a + b * b + c * c)
    if n > 1e-18:
        a, b, c = a / n, b / n, c / n
        if c < 0:
            a, b, c = -a, -b, -c
        if c > 1e-9:
            if ball:
                zc = t[0, 2] + (r - a * (x - t[0, 0]) - b * (y - t[0, 1])) / c
                qx, qy = x - r * a, y - r * b                 # contact point
                if _in_tri(qx, qy, t):
                    z = zc - r
                    if z > best:
                        best = z
            else:
                ab = math.sqrt(a * a + b * b)
                if ab < 1e-12:
                    qx, qy = x, y
                else:
                    qx, qy = x - r * a / ab, y - r * b / ab   # uphill rim point
                if _in_tri(qx, qy, t):
                    z = t[0, 2] - (a * (qx - t[0, 0]) + b * (qy - t[0, 1])) / c
                    if z > best:
                        best = z
    return best


@njit(cache=True, parallel=True)
def drop_mesh(tris, top, starts, items, gx0, gy0, cell, gnx, gny, xs, ys, r, ball, floor):
    """Exact drop-cutter on a triangle soup, with triangles bucketed on a grid of `cell`.

    Each bucket lists its triangles highest first; a triangle cannot lift the tool above
    its own highest corner, so the scan of a bucket stops at the first one that is lower
    than the best contact found so far."""
    out = np.empty(xs.shape[0])
    for k in prange(xs.shape[0]):
        x, y = xs[k], ys[k]
        best = floor
        bi, bj = int((x - gx0) / cell), int((y - gy0) / cell)
        for j in range(max(bj - 1, 0), min(bj + 2, gny)):
            for i in range(max(bi - 1, 0), min(bi + 2, gnx)):
                b = j * gnx + i
                for m in range(starts[b], starts[b + 1]):
                    t = items[m]
                    if top[t] <= best:
                        break
                    z = _tip_on_triangle(x, y, tris[t], r, ball)
                    if z > best:
                        best = z
        out[k] = best
    return out


class MeshDropper:
    """Triangles of the part bucketed on a grid of cells at least r wide."""

    def __init__(self, shape, r: float, deflection: float = 0.005):
        from . import geometry
        verts, tris = geometry.mesh(shape, deflection)
        v = np.asarray(verts, float)
        self.tris = v[np.asarray(tris)]
        self.r = r
        lo = self.tris.reshape(-1, 3).min(0)
        hi = self.tris.reshape(-1, 3).max(0)
        self.cell = max(r, 1.0)
        self.gx0, self.gy0 = lo[0] - r, lo[1] - r
        self.gnx = int((hi[0] - self.gx0 + r) / self.cell) + 1
        self.gny = int((hi[1] - self.gy0 + r) / self.cell) + 1
        x0 = ((self.tris[:, :, 0].min(1) - self.gx0) / self.cell).astype(int)
        x1 = ((self.tris[:, :, 0].max(1) - self.gx0) / self.cell).astype(int)
        y0 = ((self.tris[:, :, 1].min(1) - self.gy0) / self.cell).astype(int)
        y1 = ((self.tris[:, :, 1].max(1) - self.gy0) / self.cell).astype(int)
        buckets = [[] for _ in range(self.gnx * self.gny)]
        for t in range(len(self.tris)):
            for j in range(y0[t], y1[t] + 1):
                for i in range(x0[t], x1[t] + 1):
                    buckets[j * self.gnx + i].append(t)
        self.top = self.tris[:, :, 2].max(1)
        buckets = [sorted(b, key=lambda t: -self.top[t]) for b in buckets]   # highest first
        self.starts = np.cumsum([0] + [len(b) for b in buckets]).astype(np.int64)
        self.items = np.asarray([t for b in buckets for t in b], dtype=np.int64)

    def drop(self, xs, ys, ball: bool, floor: float) -> np.ndarray:
        return drop_mesh(self.tris, self.top, self.starts, self.items, self.gx0, self.gy0, self.cell,
                         self.gnx, self.gny, np.asarray(xs, float), np.asarray(ys, float),
                         self.r, ball, floor)


@njit(cache=True)
def _keep(P, tol):
    """Douglas-Peucker: keep the fewest points such that every dropped point lies within
    tol (in 3D) of the straight segment that replaces it."""
    n = P.shape[0]
    keep = np.zeros(n, dtype=np.bool_)
    keep[0] = True
    keep[n - 1] = True
    stack = np.empty((n, 2), dtype=np.int64)
    top = 0
    stack[0, 0], stack[0, 1] = 0, n - 1
    top = 1
    while top > 0:
        top -= 1
        a, b = stack[top, 0], stack[top, 1]
        if b - a < 2:
            continue
        ax, ay, az = P[a, 0], P[a, 1], P[a, 2]
        cx, cy, cz = P[b, 0] - ax, P[b, 1] - ay, P[b, 2] - az
        L2 = cx * cx + cy * cy + cz * cz
        worst, at = -1.0, -1
        for i in range(a + 1, b):
            bx, by, bz = P[i, 0] - ax, P[i, 1] - ay, P[i, 2] - az
            if L2 == 0:
                d2 = bx * bx + by * by + bz * bz
            else:
                t = max(0.0, min(1.0, (bx * cx + by * cy + bz * cz) / L2))
                ex, ey, ez = bx - t * cx, by - t * cy, bz - t * cz
                d2 = ex * ex + ey * ey + ez * ez
            if d2 > worst:
                worst, at = d2, i
        if worst > tol * tol:
            keep[at] = True
            stack[top, 0], stack[top, 1] = a, at
            top += 1
            stack[top, 0], stack[top, 1] = at, b
            top += 1
    return keep


def _simplify(points, tol: float = 0.003):
    """Drop points that lie within tol of the straight line between their neighbours."""
    P = np.asarray(points, float)
    if len(P) < 3:
        return [tuple(p) for p in P]
    return [tuple(p) for p in P[_keep(P, tol)]]


def parallel(setup, op: Operation) -> Toolpath:
    tool = setup.job.tool(op.tool)
    r = tool.diameter / 2
    ball = tool.shape == "ball"
    part, stock = setup.part, setup.stock
    floor = part.zmin if op.bottom is None else op.bottom
    res = min(0.1, tool.diameter / 40)            # sample spacing along a pass
    dropper = MeshDropper(setup.shape, r)
    tp = Toolpath(op, tool.diameter, tool_shape=tool.shape)
    moves = tp.moves
    safe = stock.zmax + setup.job.machine.safe_z
    moves.append(Move("rapid", None, None, safe))
    # passes over the part's footprint plus the tool radius, clipped to the stock
    lo_x, hi_x = max(stock.xmin, part.xmin - r), min(stock.xmax, part.xmax + r)
    lo_y, hi_y = max(stock.ymin, part.ymin - r), min(stock.ymax, part.ymax + r)
    along_x = op.raster != "Y"
    a0, a1 = (lo_x, hi_x) if along_x else (lo_y, hi_y)
    b0, b1 = (lo_y, hi_y) if along_x else (lo_x, hi_x)
    step = max(0.01, op.stepover * tool.diameter)
    n_pass = int(math.floor((b1 - b0) / step)) + 1
    samples = int(math.ceil((a1 - a0) / res)) + 1
    t = np.linspace(a0, a1, samples)
    first = True
    for k in range(n_pass):
        b = min(b0 + k * step, b1)
        line = t if k % 2 == 0 else t[::-1]
        xs, ys = (line, np.full_like(line, b)) if along_x else (np.full_like(line, b), line)
        zs = dropper.drop(xs, ys, ball, floor) + op.leave
        pts = _simplify(np.column_stack([xs, ys, zs]))
        if first:
            moves += [Move("rapid", None, None, stock.zmax + 1.0), Move("rapid", pts[0][0], pts[0][1], None),
                      Move("rapid", None, None, max(stock.zmax + 0.5, pts[0][2] + 0.5)),
                      Move("feed", None, None, pts[0][2], op.plunge)]
            first = False
        else:
            # step over to this pass along the stock edge, riding on the part as well
            lx, ly = moves[-1].x, moves[-1].y
            link_x = np.linspace(lx, pts[0][0], 8)
            link_y = np.linspace(ly, pts[0][1], 8)
            lz = dropper.drop(link_x, link_y, ball, floor) + op.leave
            moves += [Move("feed", x, y, z, op.feed) for x, y, z in zip(link_x[1:], link_y[1:], lz[1:])]
        moves += [Move("feed", x, y, z, op.feed) for x, y, z in pts[1:]]
    moves.append(Move("rapid", None, None, safe))
    return tp
