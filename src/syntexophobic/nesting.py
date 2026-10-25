"""Place flat parts on sheets for laser or CNC cutting.

"Slice for fabrication" turns a solid into flat parts; this module lays those parts out
on sheets of wood or acrylic so as few sheets as possible are used.

Placement is true-shape, not bounding-box: a small part may sit in the notch of an L
or the bay of a ring segment. Each sheet is kept as an occupancy grid. A placed part is
grown by the gap and every grid cell it could touch is marked used; a candidate part is
rasterised the same way (every cell it could touch). Both rasters are conservative, so
"no shared cell" proves the real outlines are at least `gap` apart, whatever the grid
size. One FFT correlation then gives, for every grid position at once, whether the part
fits there, so 100 parts of a few hundred points each nest in a few seconds. The price
is a little extra spacing, up to about 1.5 grid cells, and positions snapped to the grid.
The grid is about 120 000 cells per sheet: up to ~2 mm extra on a 600 x 400 sheet, ~7 mm
on a full 1220 x 2440 board.

Parts go largest first; each tries every allowed rotation on the first sheet where it
fits, at the spot that grows the used area of the sheet least (lowest, then leftmost on
ties). The sheet margin is applied exactly from the part's real bounding box.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import pyclipper

Ring = list[tuple[float, float]]

SCALE = 1000          # clipper works in integers: 1 unit = 1 µm (same as cam.py)
TARGET_CELLS = 120_000  # grid cells per sheet: finer packs tighter, coarser runs faster
MIN_CELL = 0.25       # mm; no point in a finer grid than the kerf
TOL = 0.01            # mm of safety on top of every offset (clipper rounding, arc chords)
EPS = 1e-9
PAD = 3               # empty grid cells around the usable area (candidate rasters reach 2 out)


@dataclass
class Placement:
    part: int          # index into the parts list
    sheet: int         # 0-based sheet number
    angle: int         # degrees about the part's own origin, applied first: 0, 90, 180, 270
    dx: float          # then translated by (dx, dy); sheet origin is its lower-left corner
    dy: float


def _rotate(pts: np.ndarray, angle: int) -> np.ndarray:
    x, y = pts[:, 0], pts[:, 1]
    if angle == 0:
        return pts.copy()
    if angle == 90:
        return np.column_stack([-y, x])
    if angle == 180:
        return np.column_stack([-x, -y])
    if angle == 270:
        return np.column_stack([y, -x])
    raise ValueError(f"angle must be 0, 90, 180 or 270, not {angle}")


def transform(ring: Ring, p: Placement) -> Ring:
    """One ring of part `p.part` as it lies on its sheet: rotate, then translate."""
    if not ring:
        return []
    pts = _rotate(np.asarray(ring, dtype=float), p.angle)
    return [(float(x) + p.dx, float(y) + p.dy) for x, y in pts]


# ---- geometry helpers --------------------------------------------------------------

def _grow(pts: np.ndarray, delta: float) -> list[np.ndarray]:
    """Outline grown outwards by `delta` mm (may split into outline + enclosed pockets)."""
    pco = pyclipper.PyclipperOffset(arc_tolerance=0.005 * SCALE)
    pco.AddPath(np.round(pts * SCALE).astype(np.int64).tolist(),
                pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return [np.asarray(p, dtype=float) / SCALE for p in pco.Execute(delta * SCALE)]


def _raster(paths: list[np.ndarray], x0: float, y0: float, nx: int, ny: int,
            cell: float) -> np.ndarray:
    """Cells (ny, nx) whose centre lies inside `paths` (even-odd); cell (0, 0) starts at x0, y0."""
    if nx <= 0 or ny <= 0 or not paths:
        return np.zeros((max(ny, 0), max(nx, 0)), dtype=bool)
    a = np.concatenate(paths)
    b = np.concatenate([np.roll(p, -1, axis=0) for p in paths])
    keep = a[:, 1] != b[:, 1]           # horizontal edges never cross a scanline
    a, b = a[keep], b[keep]
    yc = y0 + (np.arange(ny) + 0.5) * cell
    # which edges cross each row centre (half-open, so shared vertices count once)
    cross = (a[None, :, 1] <= yc[:, None]) != (b[None, :, 1] <= yc[:, None])
    rows, edges = np.nonzero(cross)
    ea, eb = a[edges], b[edges]
    xs = ea[:, 0] + (yc[rows] - ea[:, 1]) / (eb[:, 1] - ea[:, 1]) * (eb[:, 0] - ea[:, 0])
    # a crossing at x flips every cell centre to its right; parity of the flips = inside
    col = np.clip(np.floor((xs - x0) / cell - 0.5).astype(np.int64) + 1, 0, nx)
    flips = np.zeros((ny, nx + 1), dtype=np.int32)
    np.add.at(flips, (rows, col), 1)
    return (np.cumsum(flips[:, :nx], axis=1) % 2).astype(bool)


def _covers(mask: np.ndarray, other: np.ndarray) -> bool:
    """True if `mask` has every cell of `other` (both anchored at their box corner)."""
    h, w = other.shape
    return mask.shape[0] >= h and mask.shape[1] >= w and not (other & ~mask[:h, :w]).any()


def _fft_size(n: int) -> int:
    """Smallest n' >= n made of factors 2, 3 and 5: FFTs of such sizes are several times faster."""
    while True:
        m = n
        for f in (2, 3, 5):
            while m % f == 0:
                m //= f
        if m == 1:
            return n
        n += 1


def _area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2


# ---- the packer ----------------------------------------------------------------------

@dataclass
class _Pose:
    angle: int
    pts: np.ndarray        # rotated outline, bounding box moved to (0, 0)
    ox: float              # rotated bounding-box minimum: dx = x_on_sheet - ox
    oy: float
    w: float
    h: float
    mask: np.ndarray       # grid cells the part could touch, from cell (-2, -2) of its box
    cells: int             # mask.sum(): the sheet needs at least this many free cells
    spectrum: np.ndarray | None = None   # FFT of mask padded to the sheet grid, made on first use


class _Sheet:
    def __init__(self, gh: int, gw: int):
        self.occ = np.zeros((gh, gw), dtype=bool)
        self.spectrum: np.ndarray | None = None   # FFT of occ, rebuilt after each placement
        self.free = gh * gw                       # free cells, for a cheap early reject
        self.failed: list[np.ndarray] = []        # masks that found no room here
        self.ex = 0.0                             # used extent from the margin corner
        self.ey = 0.0


def nest(parts: list[list[Ring]], sheet_w: float, sheet_h: float, gap: float = 3.0,
         margin: float = 5.0, rotate: bool = True) -> list[Placement]:
    """Place every part once on as few sheet_w x sheet_h sheets as it takes.

    Ring 0 of each part is its outline (holes are ignored). Parts end up at least `gap`
    apart and at least `margin` from the sheet edges. Returns one Placement per part, in
    part order. Raises ValueError if a part cannot fit on an empty sheet.
    """
    iw, ih = sheet_w - 2 * margin, sheet_h - 2 * margin   # usable area
    if iw <= 0 or ih <= 0:
        raise ValueError(f"sheet {sheet_w} x {sheet_h} mm leaves no room inside a {margin} mm margin")
    if gap < 0:
        raise ValueError("gap must not be negative")
    if not parts:
        return []
    cell = max(MIN_CELL, math.sqrt(iw * ih / TARGET_CELLS))
    hd = cell * math.sqrt(0.5)   # half a cell diagonal: anything touching a cell is this close to its centre
    gw = _fft_size(math.ceil(iw / cell) + 2 * PAD + 1)
    gh = _fft_size(math.ceil(ih / cell) + 2 * PAD + 1)
    angles = (0, 90, 180, 270) if rotate else (0,)

    outlines, areas = [], []
    for k, part in enumerate(parts):
        if not part or len(part[0]) < 3:
            raise ValueError(f"part {k} has no outline")
        pts = np.asarray(part[0], dtype=float)
        outlines.append(pts)
        areas.append(_area(pts))

    def poses(k: int) -> list[_Pose]:
        out, seen = [], []
        for angle in angles:
            rp = _rotate(outlines[k], angle)
            lo = rp.min(axis=0)
            w, h = (rp.max(axis=0) - lo).tolist()
            if w > iw + EPS or h > ih + EPS:
                continue
            pts = rp - lo
            # every cell the part could touch; cell (0, 0) of the raster is cell (-2, -2) of the part
            mask = _raster(_grow(pts, hd + TOL), -2 * cell, -2 * cell,
                           math.ceil(w / cell) + 5, math.ceil(h / cell) + 5, cell)
            key = (round(w, 6), round(h, 6))
            if any(key == k2 and np.array_equal(mask, m2) for k2, m2 in seen):
                continue   # symmetric part: this rotation looks the same as an earlier one
            seen.append((key, mask))
            out.append(_Pose(angle, pts, float(lo[0]), float(lo[1]), w, h, mask,
                             int(mask.sum())))
        if not out:
            w, h = (outlines[k].max(axis=0) - outlines[k].min(axis=0)).tolist()
            raise ValueError(f"part {k} ({w:.1f} x {h:.1f} mm) does not fit on a "
                             f"{sheet_w:g} x {sheet_h:g} mm sheet with {margin:g} mm margin")
        return out

    def best_spot(sheet: _Sheet, pose: _Pose):
        """(score, j, i) of the best grid position for this pose on this sheet, or None."""
        jmax = math.floor((ih - pose.h) / cell + EPS)
        imax = math.floor((iw - pose.w) / cell + EPS)
        if pose.spectrum is None:
            padded = np.zeros((gh, gw), dtype=np.float32)
            padded[:pose.mask.shape[0], :pose.mask.shape[1]] = pose.mask
            pose.spectrum = np.conj(np.fft.rfft2(padded))
        corr = np.fft.irfft2(sheet.spectrum * pose.spectrum, s=(gh, gw))
        # raster cell (r, c) of a pose placed at grid (j, i) lands on occ (r + j + 1, c + i + 1)
        free = corr[1:jmax + 2, 1:imax + 2] < 0.5
        # score: area of the sheet's used box after placing here (lowest, then leftmost on ties)
        ex = np.maximum(sheet.ex, np.arange(imax + 1) * cell + pose.w)
        ey = np.maximum(sheet.ey, np.arange(jmax + 1) * cell + pose.h)
        score = np.where(free, np.outer(ey, ex), np.inf)
        mh, mw = pose.mask.shape
        while True:
            n = int(np.argmin(score))   # first minimum in row-major order
            j, i = divmod(n, imax + 1)
            if score[j, i] == np.inf:
                return None
            # the FFT runs in float32: confirm the winner on the raster itself
            if not (sheet.occ[j + 1:j + 1 + mh, i + 1:i + 1 + mw] & pose.mask).any():
                return (float(score[j, i]), j, i)
            score[j, i] = np.inf

    def place(k: int, pool: ThreadPoolExecutor) -> None:
        cands = poses(k)
        for s in range(len(sheets) + 1):
            if s == len(sheets):
                sheets.append(_Sheet(gh, gw))
            sheet = sheets[s]
            # cheap rejects: too few free cells, or the mask covers one that already failed
            # here (occupancy only grows, so that one would still fail)
            fits = [ps for ps in cands if ps.cells <= sheet.free
                    and not any(_covers(ps.mask, f) for f in sheet.failed)]
            if not fits:
                continue
            if sheet.spectrum is None:
                sheet.spectrum = np.fft.rfft2(sheet.occ.astype(np.float32))
            best = None
            # the rotations are independent and numpy's FFT releases the GIL
            spots = list(pool.map(lambda ps: best_spot(sheet, ps), fits))
            for pose, spot in zip(fits, spots):
                if spot and (best is None or spot[0] < best[0][0]):
                    best = (spot, pose)
            for pose, spot in zip(fits, spots):
                if spot is None and len(sheet.failed) < 32:
                    sheet.failed.append(pose.mask)
            if best is None:
                continue
            (_, j, i), pose = best
            x, y = margin + i * cell, margin + j * cell   # bounding-box corner on the sheet
            result[k] = Placement(k, s, pose.angle, x - pose.ox, y - pose.oy)
            # mark every cell the grown part could touch
            grown = [p + (x, y) for p in _grow(pose.pts, gap + hd + TOL)]
            lo = np.min([p.min(axis=0) for p in grown], axis=0)
            hi = np.max([p.max(axis=0) for p in grown], axis=0)
            c0 = max(0, math.floor((lo[0] - margin) / cell) + PAD - 1)
            r0 = max(0, math.floor((lo[1] - margin) / cell) + PAD - 1)
            c1 = min(gw, math.ceil((hi[0] - margin) / cell) + PAD + 1)
            r1 = min(gh, math.ceil((hi[1] - margin) / cell) + PAD + 1)
            sheet.occ[r0:r1, c0:c1] |= _raster(grown, margin + (c0 - PAD) * cell,
                                               margin + (r0 - PAD) * cell, c1 - c0, r1 - r0, cell)
            sheet.spectrum = None
            sheet.free = int((~sheet.occ).sum())
            sheet.ex = max(sheet.ex, i * cell + pose.w)
            sheet.ey = max(sheet.ey, j * cell + pose.h)
            return
        raise AssertionError("an empty sheet always takes a part that passed poses()")

    sheets: list[_Sheet] = []
    result: dict[int, Placement] = {}
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        for k in sorted(range(len(parts)), key=lambda k: (-areas[k], k)):   # largest first
            place(k, pool)
    return [result[k] for k in range(len(parts))]
