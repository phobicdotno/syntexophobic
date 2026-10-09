"""Stock removal simulation on a height map, and a gouge check against the part.

The stock is a grid of columns over XY. Every feed move is walked in small steps and a
flat endmill disc is stamped into the grid at the move's height. The part is rasterised
into its own height map (the top surface seen from above), so any column cut below the
part surface is a gouge. All array work goes through the compute backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit

from . import compute, geometry


@dataclass
class Grid:
    x0: float
    y0: float
    res: float
    nx: int
    ny: int

    @classmethod
    def over(cls, box: geometry.Box, res: float) -> "Grid":
        return cls(box.xmin, box.ymin, res,
                   int(math.ceil((box.xmax - box.xmin) / res)) + 1,
                   int(math.ceil((box.ymax - box.ymin) / res)) + 1)

    def centres(self):
        xs = self.x0 + np.arange(self.nx) * self.res
        ys = self.y0 + np.arange(self.ny) * self.res
        return xs, ys


@dataclass
class Result:
    grid: Grid
    stock: np.ndarray          # (ny, nx) remaining stock top after all toolpaths
    part: np.ndarray           # (ny, nx) part top surface, -inf where there is no part
    gouge_depth: float         # deepest cut below the part surface, mm (0 = none)
    gouge_cells: int
    removed_cm3: float
    frames: list = field(default_factory=list)   # (operation, tool tip (x, y, z), tool Ø, stock)
    rapid_hits: list = field(default_factory=list)   # (operation, (x, y, z), depth) where a rapid hits stock

    def gouges(self, tol: float = 0.02) -> np.ndarray:
        return _normal_depth(self.part, self.stock, self.grid.res) > tol


def part_heightmap(shape, grid: Grid, deflection: float = 0.01) -> np.ndarray:
    """Highest point of the part above each grid centre (-inf outside the part)."""
    verts, tris = geometry.mesh(shape, deflection)
    v = np.asarray(verts, float)
    t = v[np.asarray(tris)]                                 # (n, 3, 3)
    h = np.full((grid.ny, grid.nx), -np.inf)
    # Only triangles that face up or down describe a surface seen from above.
    # Walls (normal nearly horizontal) are skipped: seen from above they are slivers
    # whose barycentric weights are pure rounding noise.
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    normal = np.cross(e1, e2)
    area2 = normal[:, 2]                                     # 2 × projected area, signed
    facing = np.abs(area2) > 1e-3 * np.linalg.norm(normal, axis=1)
    t, area2 = t[facing], area2[facing]
    gx = (t[:, :, 0] - grid.x0) / grid.res
    gy = (t[:, :, 1] - grid.y0) / grid.res
    ix0 = np.clip(np.ceil(gx.min(1)).astype(int), 0, grid.nx - 1)
    ix1 = np.clip(np.floor(gx.max(1)).astype(int), 0, grid.nx - 1)
    iy0 = np.clip(np.ceil(gy.min(1)).astype(int), 0, grid.ny - 1)
    iy1 = np.clip(np.floor(gy.max(1)).astype(int), 0, grid.ny - 1)
    xs, ys = grid.centres()
    covers = (ix1 >= ix0) & (iy1 >= iy0)
    small = covers & (ix1 - ix0 < 4) & (iy1 - iy0 < 4)
    # Small triangles (most of a fine mesh) in one batch over a 4 × 4 window each.
    k = np.nonzero(small)[0]
    if len(k):
        ox, oy = np.meshgrid(np.arange(4), np.arange(4))
        I = ix0[k][:, None] + ox.reshape(-1)[None, :]
        J = iy0[k][:, None] + oy.reshape(-1)[None, :]
        valid = (I <= ix1[k][:, None]) & (J <= iy1[k][:, None])
        X, Y = grid.x0 + I * grid.res, grid.y0 + J * grid.res
        z = _bary_z(t[k], area2[k], X, Y)
        ok = valid & np.isfinite(z)
        np.maximum.at(h.reshape(-1), (J * grid.nx + I)[ok], z[ok])
    for k in np.nonzero(covers & ~small)[0]:
        X, Y = np.meshgrid(xs[ix0[k]:ix1[k] + 1], ys[iy0[k]:iy1[k] + 1])
        z = _bary_z(t[k:k + 1], area2[k:k + 1], X.reshape(1, -1), Y.reshape(1, -1)).reshape(X.shape)
        sub = h[iy0[k]:iy1[k] + 1, ix0[k]:ix1[k] + 1]
        np.maximum(sub, z, out=sub)
    return h


def _bary_z(t, area2, X, Y):
    """Height of triangles t (k, 3, 3) at points X, Y (k, m); -inf outside the triangle."""
    ax, ay, az = (t[:, 0, i:i + 1] for i in range(3))
    bx, by, bz = (t[:, 1, i:i + 1] for i in range(3))
    cx, cy, cz = (t[:, 2, i:i + 1] for i in range(3))
    a2 = area2[:, None]
    w0 = ((bx - X) * (cy - Y) - (by - Y) * (cx - X)) / a2
    w1 = ((cx - X) * (ay - Y) - (cy - Y) * (ax - X)) / a2
    w2 = 1 - w0 - w1
    inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
    return np.where(inside, w0 * az + w1 * bz + w2 * cz, -np.inf)


def _disc(r: float, res: float):
    n = int(math.floor(r / res))
    dy, dx = np.mgrid[-n:n + 1, -n:n + 1]
    keep = (dx * dx + dy * dy) * res * res <= r * r + 1e-9
    return dx[keep], dy[keep]


def _cut(be, grid, stock, P, tp, res):
    """Remove material along the ordered points P of one toolpath. Returns the stock."""
    xp = be.xp
    r = tp.tool_diameter / 2
    # A quarter cell narrower than the tool: cells the tool edge only grazes (exactly on
    # the part outline) are not counted as cut, so the check is exact to ±res/4.
    r_cut = r - res * 0.25
    # Candidates: snapping the centre to the grid moves it at most res·√2/2.
    dx, dy = (be.asarray(a.astype(np.int32)) for a in _disc(r_cut + res * 0.71, res))
    ball = getattr(tp, "tool_shape", "flat") == "ball"
    flat = stock.reshape(-1)
    if getattr(tp, "tool_shape", "flat") == "vbit":
        _stamp_cone(stock, grid.x0, grid.y0, res, P, r_cut, getattr(tp, "vbit_k", 1.0))
        return stock
    if ball and xp is np:                     # compiled kernel on the CPU
        _stamp_ball(stock, grid.x0, grid.y0, res, P, r_cut, r)
        return stock
    if ball:                                  # every cell under a ball sits at its own height
        for start in range(0, P.shape[0], 20000):
            idx, zv = _stamp(xp, grid, be.asarray(P[start:start + 20000]), dx, dy, r_cut, r)
            _scatter_min(xp, flat, idx, zv)
        return flat.reshape(grid.ny, grid.nx)
    # Most points cut at one depth per level: for those a boolean mask of every cell
    # the disc touches is enough. Ramp points (each at its own depth) need a min-reduce.
    zs, counts = np.unique(np.round(P[:, 2], 6), return_counts=True)
    level_z = set(zs[counts >= 200].tolist())
    on_level = np.isin(np.round(P[:, 2], 6), list(level_z))
    for z in level_z:
        sel = be.asarray(P[on_level & (np.round(P[:, 2], 6) == z)])
        mask = xp.zeros(flat.shape[0], dtype=bool)
        for start in range(0, sel.shape[0], 20000):
            idx, _ = _stamp(xp, grid, sel[start:start + 20000], dx, dy, r_cut)
            mask[idx] = True
        flat = xp.where(mask, xp.minimum(flat, z), flat)
    rest = be.asarray(P[~on_level])
    for start in range(0, rest.shape[0], 20000):
        idx, zv = _stamp(xp, grid, rest[start:start + 20000], dx, dy, r_cut)
        _scatter_min(xp, flat, idx, zv)
    return flat.reshape(grid.ny, grid.nx)


def _runs(tp, step: float):
    """Feed points in runs, each followed by the rapid moves (as segments) before the next
    run: [(feed points (n, 3), [((x, y, z), (x, y, z)), ...])], in machine order."""
    pos = [(k, (x, y, z)) for k, x, y, z, _ in tp.positions()]
    runs, feeds, rapids = [], [], []
    for (k0, a), (k1, b) in zip(pos, pos[1:]):
        if k1 == "feed":
            if rapids:
                runs.append((feeds, rapids))
                feeds, rapids = [], []
            feeds.append((a, b))
        else:
            rapids.append((a, b))
    runs.append((feeds, rapids))
    out = []
    for segs, rap in runs:
        if segs:
            a = np.array([s[0] for s in segs], float)
            b = np.array([s[1] for s in segs], float)
            n = np.maximum(1, np.ceil(np.hypot(*(b - a)[:, :2].T) / step).astype(int))
            seg = np.repeat(np.arange(len(a)), n)
            first = np.repeat(np.cumsum(n) - n, n)
            t = (np.arange(n.sum()) - first + 1) / n[seg]
            P = a[seg] + (b[seg] - a[seg]) * t[:, None]
        else:
            P = np.empty((0, 3))
        out.append((P, rap))
    return out


@njit(cache=True)
def _rapid_depth(stock, x0, y0, res, ax, ay, az, bx, by, bz, r):
    """How deep (mm) a tool of radius r moving in a straight rapid from a to b goes into
    the stock (0 = clear), and where."""
    ny, nx = stock.shape
    L = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
    n = max(1, int(L / (res * 0.5)) + 1)
    worst, wx, wy, wz = 0.0, 0.0, 0.0, 0.0
    ri = int(r / res) + 1
    for s in range(n + 1):
        t = s / n
        px, py, pz = ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t
        ci, cj = int(round((px - x0) / res)), int(round((py - y0) / res))
        for j in range(max(cj - ri, 0), min(cj + ri + 1, ny)):
            dy = y0 + j * res - py
            for i in range(max(ci - ri, 0), min(ci + ri + 1, nx)):
                dx = x0 + i * res - px
                if dx * dx + dy * dy <= r * r:
                    d = stock[j, i] - pz
                    if d > worst:
                        worst, wx, wy, wz = d, px, py, pz
    return worst, wx, wy, wz


def simulate(setup, toolpaths, res: float = 0.25, backend=None, snapshots: int = 0) -> Result:
    """Cut the stock with every toolpath in machine order. Rapid moves are checked against
    the stock as it is when they happen (Result.rapid_hits). With snapshots > 0, also keep
    about that many copies of the stock along the way, for playback (Result.frames)."""
    be = backend or compute.get()
    xp = be.xp
    grid = Grid.over(setup.stock, res)
    stock = xp.full((grid.ny, grid.nx), setup.stock.zmax, dtype=xp.float64)
    runs = [(tp, _runs(tp, res)) for tp in toolpaths]
    total = sum(len(P) for _, rr in runs for P, _ in rr) or 1
    every = max(1, total // snapshots) if snapshots else 0
    done, next_frame = 0, every
    frames, hits = [], []
    for tp, rr in runs:
        r = tp.tool_diameter / 2 - res * 0.25
        # group consecutive runs into chunks of a few thousand points: the cutting kernels
        # are fastest on big batches, and rapids are checked between chunks in order
        chunk, chunk_rapids = [], []
        def flush():
            nonlocal stock, chunk, chunk_rapids, done, next_frame
            if chunk:
                P = np.vstack(chunk)
                stock = _cut(be, grid, stock, P, tp, res)
                done += len(P)
                if every and done >= next_frame:
                    frames.append((tp.op.name, tuple(P[-1]), tp.tool_diameter, be.to_numpy(stock).copy()))
                    next_frame += every
            host = be.to_numpy(stock) if chunk_rapids else None
            for (a, b) in chunk_rapids:
                depth, x, y, z = _rapid_depth(host, grid.x0, grid.y0, res, *a, *b, max(r, res))
                if depth > 0.05:
                    hits.append((tp.op.name, (x, y, z), depth))
            chunk, chunk_rapids = [], []
        for P, rapids in rr:
            if len(P):
                chunk.append(P)
            if rapids:
                chunk_rapids = rapids
                flush()
        flush()
    if frames:                                   # the last frame is the finished stock
        name, tip, dia, _ = frames[-1]
        frames[-1] = (name, tip, dia, be.to_numpy(stock).copy())
    stock = be.to_numpy(stock)
    part = part_heightmap(setup.shape, grid)
    # V-carving cuts into the part on purpose: inside its shapes the part surface is
    # expected to go, so those cells are left out of the gouge check
    for tp in toolpaths:
        if tp.op.type == "vcarve" and tp.op.source:
            from . import adaptive, cam
            try:
                region = cam._to_mm(cam.vcarve_regions(setup, tp.op))
            except ValueError:
                continue
            carved = adaptive.rasterize(region, grid.x0, grid.y0, res, part.shape)
            part = np.where(carved, -np.inf, part)
    depth = _normal_depth(part, stock, res)
    removed = float(np.sum(setup.stock.zmax - stock)) * res * res / 1000.0
    return Result(grid, stock, part, float(max(depth.max(), 0.0)),
                  int((depth > 0.02).sum()), removed, frames, hits)


def _normal_depth(part, stock, res: float) -> np.ndarray:
    """How far each cut goes into the part, measured perpendicular to its surface.

    A height difference on a steep wall is mostly sideways: 0.02 mm in Z on a wall at 80°
    is 0.003 mm into it. Dividing by sqrt(1 + slope²) turns the vertical difference into
    the real depth, so near-vertical surfaces are judged like flat ones.
    """
    finite = np.isfinite(part)
    depth = np.where(finite, part - stock, 0.0)
    h = np.where(finite, part, np.nan)
    gy, gx = np.gradient(h, res)
    slope2 = np.nan_to_num(gx * gx + gy * gy, nan=0.0)
    # at the part's outline the gradient is undefined: judge those cells vertically
    return depth / np.sqrt(1.0 + np.minimum(slope2, 1e6))


@njit(cache=True)
def _stamp_ball(stock, x0, y0, res, P, r_cut, r):
    """Lower the stock to the surface of a ball end at every point of P (CPU kernel)."""
    ny, nx = stock.shape
    ri = int(r_cut / res) + 2
    for k in range(P.shape[0]):
        px, py, pz = P[k, 0], P[k, 1], P[k, 2]
        ci, cj = int(round((px - x0) / res)), int(round((py - y0) / res))
        for j in range(max(cj - ri, 0), min(cj + ri + 1, ny)):
            dy = y0 + j * res - py
            for i in range(max(ci - ri, 0), min(ci + ri + 1, nx)):
                dx = x0 + i * res - px
                d2 = dx * dx + dy * dy
                if d2 <= r_cut * r_cut:
                    z = pz + r - math.sqrt(r * r - d2)
                    if z < stock[j, i]:
                        stock[j, i] = z


@njit(cache=True)
def _stamp_cone(stock, x0, y0, res, P, r_max, k):
    """Lower the stock to a V-bit's cone (tip at each point, k = width per depth)."""
    ny, nx = stock.shape
    ri = int(r_max / res) + 2
    for n in range(P.shape[0]):
        px, py, pz = P[n, 0], P[n, 1], P[n, 2]
        ci, cj = int(round((px - x0) / res)), int(round((py - y0) / res))
        for j in range(max(cj - ri, 0), min(cj + ri + 1, ny)):
            dy = y0 + j * res - py
            for i in range(max(ci - ri, 0), min(ci + ri + 1, nx)):
                dx = x0 + i * res - px
                d = math.sqrt(dx * dx + dy * dy)
                if d <= r_max:
                    z = pz + d / k
                    if z < stock[j, i]:
                        stock[j, i] = z


def _feed_points(tp, step: float) -> np.ndarray:
    """(n, 3) points along every feed move of a toolpath, at most `step` apart."""
    pos = np.array([(x, y, z) for _, x, y, z, _ in tp.positions()], float)
    kinds = np.array([k == "feed" for k, *_ in tp.positions()])
    if len(pos) < 2:
        return np.empty((0, 3))
    a, b = pos[:-1][kinds[1:]], pos[1:][kinds[1:]]
    if not len(a):
        return np.empty((0, 3))
    n = np.maximum(1, np.ceil(np.hypot(*(b - a)[:, :2].T) / step).astype(int))
    seg = np.repeat(np.arange(len(a)), n)
    first = np.repeat(np.cumsum(n) - n, n)
    t = (np.arange(n.sum()) - first + 1) / n[seg]
    return a[seg] + (b[seg] - a[seg]) * t[:, None]


def _stamp(xp, grid: Grid, chunk, dx, dy, r_cut: float, ball_r: float | None = None):
    """Flat cell indices under the tool disc at each point of chunk, with the height the
    tool leaves there: the tip z for a flat endmill, the ball's surface for a ball end."""
    res = grid.res
    fx = (chunk[:, 0] - grid.x0) / res
    fy = (chunk[:, 1] - grid.y0) / res
    cx = xp.rint(fx).astype(xp.int32)
    cy = xp.rint(fy).astype(xp.int32)
    X = cx[:, None] + dx[None, :]
    Y = cy[:, None] + dy[None, :]
    ex = (X - fx[:, None].astype(xp.float32)) * res     # exact offsets from the true tool centre
    ey = (Y - fy[:, None].astype(xp.float32)) * res
    ok = ((X >= 0) & (X < grid.nx) & (Y >= 0) & (Y < grid.ny)
          & (ex * ex + ey * ey <= r_cut * r_cut))
    Z = xp.broadcast_to(chunk[:, 2:3], X.shape)
    if ball_r is not None:
        d2 = xp.minimum(ex * ex + ey * ey, ball_r * ball_r)
        Z = Z + ball_r - xp.sqrt(ball_r * ball_r - d2)
    return (Y.astype(xp.int64) * grid.nx + X)[ok], Z[ok]


def _scatter_min(xp, target, idx, values):
    if xp is np:
        np.minimum.at(target, idx, values)
    else:  # CuPy and friends
        try:
            import cupyx
            cupyx.scatter_min(target, idx, values)
        except ImportError:
            order = xp.argsort(values)[::-1]            # write largest first, smallest wins
            target[idx[order]] = xp.minimum(target[idx[order]], values[order])
