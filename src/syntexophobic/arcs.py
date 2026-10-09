"""Fit circular arcs (G2/G3) to runs of short straight moves in the XY plane.

A run of feed moves at one Z, all within `tol` of one circle, sweeping less than 180° in
one direction, becomes a single arc. The check is on every input point and on the chord
midpoints, so the arc never strays more than `tol` from the original path.
"""

from __future__ import annotations

import math


def _circle(a, b, c):
    """Centre and radius of the circle through three points, or None if they are in line."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.dist((ux, uy), a)


def _fits(pts, centre, r, tol, ccw):
    """All points and chord midpoints within tol of the circle, turning one way, < 180°."""
    total = 0.0
    for p, q in zip(pts, pts[1:]):
        if abs(math.dist(q, centre) - r) > tol:
            return False
        mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if abs(math.dist(mid, centre) - r) > tol:
            return False
        a0 = math.atan2(p[1] - centre[1], p[0] - centre[0])
        a1 = math.atan2(q[1] - centre[1], q[0] - centre[0])
        da = math.remainder(a1 - a0, 2 * math.pi)
        if (da > 0) != ccw or abs(da) < 1e-9:
            return False
        total += abs(da)
    return total < math.pi * 0.98


def _try(points, i, j, tol, max_radius):
    """(centre, ccw) if points[i..j] fit one arc, else None."""
    mid = points[(i + j) // 2]
    circ = _circle(points[i], mid, points[j])
    if circ is None or circ[1] > max_radius:
        return None
    centre, r = circ
    p0, p1, pj = points[i], points[i + 1], points[j]
    ccw = (p1[0] - p0[0]) * (pj[1] - p0[1]) - (p1[1] - p0[1]) * (pj[0] - p0[0]) > 0
    return (centre, ccw) if _fits(points[i:j + 1], centre, r, tol, ccw) else None


def fit(points, tol: float = 0.005, min_points: int = 4, max_radius: float = 2000.0):
    """Split an XY polyline into ("line", end) and ("arc", end, centre, ccw) pieces.

    Each arc is grown as far as it still fits: doubling the span, then a binary search
    for the last point that fits."""
    out = []
    i, n = 0, len(points)
    while i < n - 1:
        j0 = i + min_points - 1
        ok = _try(points, i, j0, tol, max_radius) if j0 < n else None
        if not ok:
            out.append(("line", points[i + 1]))
            i += 1
            continue
        good, bad, step = j0, None, 1
        while bad is None:
            j = good + step
            if j >= n:
                j = n - 1
                if j == good:
                    break
            found = _try(points, i, j, tol, max_radius)
            if found:
                good, ok = j, found
                if j == n - 1:
                    break
                step *= 2
            else:
                bad = j
        if bad is not None:
            while bad - good > 1:
                mid = (good + bad) // 2
                found = _try(points, i, mid, tol, max_radius)
                if found:
                    good, ok = mid, found
                else:
                    bad = mid
        centre, ccw = ok
        out.append(("arc", points[good], centre, ccw))
        i = good
    return out
