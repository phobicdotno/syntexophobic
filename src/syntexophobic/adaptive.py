"""Adaptive clearing: roughing that keeps the cutter's engagement near a target.

Concentric offset rings bury the cutter in a full-width slot on the first ring and in
every inside corner. Here the tool is steered instead. On a grid over one Z level:

- A: where the tool centre may go (the same exact polygons the other operations use,
  shrunk by half a cell, so the tool cannot touch the part);
- M: material still to cut at this level.

The tool moves in short steps. At each step it tries headings within ±100° of where it
is going and takes the one whose newly cut area is closest to the target (stepover ×
diameter × step), preferring to keep the material on its right (climb milling with a
clockwise spindle). When nothing is left within reach it lifts and starts again: in the
open next to material when possible, otherwise with a small helix. A last pass along the
walls (done by the caller) leaves them exact.

The inner loop is compiled with numba.
"""

from __future__ import annotations

import math

import numpy as np
from numba import njit


@njit(cache=True)
def _fill_polygons(mask, xs, ys, starts, x0, y0, res):
    """Even-odd scanline fill of polygons (flattened xs/ys, start index per polygon) into
    mask[j, i] for cell centres (x0 + i*res, y0 + j*res)."""
    ny, nx = mask.shape
    npoly = starts.shape[0] - 1
    xing = np.empty(4096)
    for j in range(ny):
        y = y0 + j * res
        n = 0
        for p in range(npoly):
            a, b = starts[p], starts[p + 1]
            for k in range(a, b):
                k2 = k + 1 if k + 1 < b else a
                ya, yb = ys[k], ys[k2]
                if (ya > y) != (yb > y):
                    if n < 4096:
                        xing[n] = xs[k] + (y - ya) * (xs[k2] - xs[k]) / (yb - ya)
                        n += 1
        xs_sorted = np.sort(xing[:n])
        for m in range(0, n - 1, 2):
            i0 = int(math.ceil((xs_sorted[m] - x0) / res))
            i1 = int(math.floor((xs_sorted[m + 1] - x0) / res))
            for i in range(max(i0, 0), min(i1, nx - 1) + 1):
                mask[j, i] = not mask[j, i]


@njit(cache=True)
def _cut_area(M, cx, cy, r):
    """Material cells inside the disc of radius r (cells) at (cx, cy)."""
    ny, nx = M.shape
    n = 0
    ri = int(r) + 1
    r2 = r * r
    for j in range(max(int(cy) - ri, 0), min(int(cy) + ri + 1, ny)):
        dy = j - cy
        for i in range(max(int(cx) - ri, 0), min(int(cx) + ri + 1, nx)):
            dx = i - cx
            if dx * dx + dy * dy <= r2 and M[j, i]:
                n += 1
    return n


@njit(cache=True)
def _clear(M, cx, cy, r):
    ny, nx = M.shape
    ri = int(r) + 1
    r2 = r * r
    for j in range(max(int(cy) - ri, 0), min(int(cy) + ri + 1, ny)):
        dy = j - cy
        for i in range(max(int(cx) - ri, 0), min(int(cx) + ri + 1, nx)):
            dx = i - cx
            if dx * dx + dy * dy <= r2:
                M[j, i] = False


@njit(cache=True)
def _inside(A, x, y):
    i, j = int(round(x)), int(round(y))
    if j < 0 or i < 0 or j >= A.shape[0] or i >= A.shape[1]:
        return False
    return A[j, i]


@njit(cache=True)
def _find_start(A, M, r, target, stride):
    """A tool-centre cell in A, in the open (cuts nothing) right next to material: the
    best place to drop in at full depth. Returns (-1, -1) if there is none."""
    ny, nx = A.shape
    best_i, best_j, best = -1, -1, 1e18
    for j in range(0, ny, stride):
        for i in range(0, nx, stride):
            if not A[j, i]:
                continue
            if _cut_area(M, i, j, r) > 0:
                continue
            near = _cut_area(M, i, j, r + 2.0 * stride)       # material just beyond the disc
            if near > 0:
                score = -near
                if score < best:
                    best, best_i, best_j = score, i, j
    return best_i, best_j


@njit(cache=True)
def _helix_start(A, M, r, stride):
    """For a closed pocket: the A cell with the most room around it, to helix in."""
    ny, nx = A.shape
    best_i, best_j, best_room = -1, -1, -1.0
    for j in range(0, ny, stride):
        for i in range(0, nx, stride):
            if not A[j, i] or _cut_area(M, i, j, r) == 0:
                continue
            room = 0.0
            for k in range(1, int(r) + 2):                   # free A cells around it, up to r
                ok = True
                for a in range(8):
                    t = a * math.pi / 4
                    if not _inside(A, i + k * math.cos(t), j + k * math.sin(t)):
                        ok = False
                        break
                if not ok:
                    break
                room = k
            if room > best_room:
                best_room, best_i, best_j = room, i, j
    return best_i, best_j, best_room


@njit(cache=True)
def _trace(A, M, sx, sy, heading, r, step, target, out):
    """Greedy constant-engagement walk from (sx, sy). Writes cell coordinates to out and
    returns how many points it wrote."""
    x, y, h = sx, sy, heading
    n = 0
    out[n, 0], out[n, 1] = x, y
    n += 1
    idle = 0
    while n < out.shape[0] - 1:
        best_a, best_err, best_cut = 0.0, 1e18, 0
        for k in range(41):                                   # -100° .. +100° in 5° steps
            a = h + math.radians(-100.0 + 5.0 * k)
            nx_, ny_ = x + step * math.cos(a), y + step * math.sin(a)
            if not (_inside(A, nx_, ny_) and _inside(A, x + 0.5 * step * math.cos(a), y + 0.5 * step * math.sin(a))):
                continue
            cut = _cut_area(M, nx_, ny_, r)
            # climb: prefer turning right (negative angle) when equally good
            err = abs(cut - target) + 0.02 * target * (k / 40.0)
            if cut == 0:
                err += 10.0 * target
            if err < best_err:
                best_err, best_a, best_cut = err, a, cut
        if best_err >= 1e17 or best_cut == 0:
            idle += 1
            if idle > 2 or best_err >= 1e17:
                break
        else:
            idle = 0
        x, y, h = x + step * math.cos(best_a), y + step * math.sin(best_a), best_a
        _clear(M, x, y, r)
        out[n, 0], out[n, 1] = x, y
        n += 1
    return n


def rasterize(paths_mm, x0, y0, res, shape) -> np.ndarray:
    """Polygons (lists of (x, y) in mm) -> boolean mask of cell centres inside (even-odd)."""
    mask = np.zeros(shape, dtype=np.bool_)
    if not paths_mm:
        return mask
    xs = np.concatenate([np.asarray([p[0] for p in poly], float) for poly in paths_mm])
    ys = np.concatenate([np.asarray([p[1] for p in poly], float) for poly in paths_mm])
    starts = np.cumsum([0] + [len(p) for p in paths_mm]).astype(np.int64)
    _fill_polygons(mask, xs, ys, starts, float(x0), float(y0), float(res))
    return mask


def clear_level(allowed_mm, material_mm, bbox, r: float, stepover: float, res: float | None = None):
    """Tool-centre paths (lists of (x, y) mm, plus 'helix' flag) that clear `material_mm`
    with the centre kept inside `allowed_mm`.

    Returns [(points, helix)] where helix=True means the path starts with a helical entry
    (the caller ramps down along its first circle).
    """
    D = 2 * r
    res = res or max(0.05, D / 40)
    xmin, ymin, xmax, ymax = bbox
    x0, y0 = xmin - r - res, ymin - r - res
    nx = int(math.ceil((xmax - xmin + 2 * r) / res)) + 3
    ny = int(math.ceil((ymax - ymin + 2 * r) / res)) + 3
    A = rasterize(allowed_mm, x0, y0, res, (ny, nx))
    M = rasterize(material_mm, x0, y0, res, (ny, nx))
    # Material out of every reachable disc can't be cut by this tool: drop it so the walk
    # does not hunt for it. (The wall pass and rest machining deal with what is left.)
    rc = r / res
    step = max(1.0, 0.1 * D / res)
    target = stepover * D * (step * res) / (res * res)          # cells cut per step
    out = np.empty((200000, 2))
    stride = max(1, int(0.25 * D / res))
    paths = []
    to_mm = lambda pts: [(x0 + px * res, y0 + py * res) for px, py in pts]   # noqa: E731
    for _ in range(500):
        if not M.any():
            break
        si, sj = _find_start(A, M, rc, target, stride)
        helix = False
        if si < 0:
            hi, hj, room = _helix_start(A, M, rc, stride)
            if hi < 0:
                break
            # helical bore: a circle of up to 0.4 D radius, cleared, then walk on from it
            rb = max(1.0, min(room, 0.4 * D / res))
            circle = [(hi + rb * math.cos(2 * math.pi * k / 24), hj + rb * math.sin(2 * math.pi * k / 24))
                      for k in range(25)]
            for cx, cy in circle:
                _clear(M, cx, cy, rc)
            _clear(M, float(hi), float(hj), rc)
            si, sj, helix = int(round(circle[-1][0])), int(round(circle[-1][1])), True
            n = _trace(A, M, float(circle[-1][0]), float(circle[-1][1]), math.pi / 2, rc, step, target, out)
            paths.append((to_mm(circle) + to_mm(out[1:n]), True))
            continue
        # head towards the material
        best_h, best = 0.0, -1
        for k in range(36):
            a = 2 * math.pi * k / 36
            c = _cut_area(M, si + 2 * step * math.cos(a), sj + 2 * step * math.sin(a), rc)
            if c > best:
                best, best_h = c, a
        before = int(M.sum())
        n = _trace(A, M, float(si), float(sj), best_h, rc, step, target, out)
        if before - int(M.sum()) < target:
            # this start reaches (almost) nothing: write the material around it off so the
            # search does not come back here; the wall pass cleans what little is left
            _clear(M, float(si), float(sj), rc + 2 * stride)
            continue
        paths.append((to_mm(out[:n]), helix))
    return paths
