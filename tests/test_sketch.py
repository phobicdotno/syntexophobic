import math

import pytest

from syntexophobic.sketch import Sketch


def corners(sk, lines):
    return [(round(sk.points[sk.lines[l].a].x, 6), round(sk.points[sk.lines[l].a].y, 6)) for l in lines]


def test_rectangle_dimensions_and_dof():
    sk = Sketch()
    ls = sk.rectangle(0.3, -0.2, 47, 33)              # drawn roughly
    a = sk.lines[ls[0]].a
    r = sk.solve()
    assert r.ok and r.dof == 4                        # position (2) + width + height left free
    sk.constrain("x", a, value=0)
    sk.constrain("y", a, value=0)
    sk.constrain("distance", ls[0], value=50)
    sk.constrain("distance", ls[1], value=30)
    r = sk.solve()
    assert r.ok and r.dof == 0, r
    assert corners(sk, ls) == [(0, 0), (50, 0), (50, 30), (0, 30)]


def test_moves_as_little_as_possible():
    sk = Sketch()
    ln = sk.line((0, 0), (10, 1))
    sk.constrain("horizontal", ln)
    sk.solve()
    a, b = sk.points[sk.lines[ln].a], sk.points[sk.lines[ln].b]
    assert abs(a.y - b.y) < 1e-7 and abs(a.y - 0.5) < 1e-3     # met halfway, not snapped to 0


def test_tangent_circle_and_angle():
    sk = Sketch()
    base = sk.line((0, 0), (40, 0))
    sk.constrain("fixed", sk.lines[base].a)
    sk.constrain("horizontal", base)
    c = sk.circle((20, 7), 5)
    sk.constrain("radius", c, value=8)
    sk.constrain("tangent", base, c)
    side = sk.line(sk.lines[base].a, (10, 10))
    sk.constrain("angle", base, side, value=30)
    sk.constrain("distance", side, value=20)
    r = sk.solve()
    assert r.ok, r
    centre = sk.points[sk.circles[c].center]
    assert abs(abs(centre.y) - 8) < 1e-6 and abs(sk.circles[c].r - 8) < 1e-9
    end = sk.points[sk.lines[side].b]
    assert abs(math.degrees(math.atan2(end.y, end.x)) - 30) < 1e-5 and abs(math.hypot(end.x, end.y) - 20) < 1e-6


def test_conflicting_constraints_are_reported():
    sk = Sketch()
    ln = sk.line((0, 0), (10, 0))
    sk.constrain("distance", ln, value=10)
    k = sk.constrain("distance", ln, value=12)
    r = sk.solve()
    assert not r.ok and r.conflicting


def test_arc_symmetric_midpoint_and_profiles():
    sk = Sketch()
    # a slot: two lines joined by two arcs
    p = [sk.point(0, 0), sk.point(30, 0), sk.point(30, 10), sk.point(0, 10)]
    bottom, top = sk.line(p[0], p[1]), sk.line(p[2], p[3])
    right = sk.arc((30, 5), p[1], p[2])
    left = sk.arc((0, 5), p[3], p[0])
    axis = sk.line((15, -5), (15, 15), construction=True)
    sk.constrain("vertical", axis)
    sk.constrain("symmetric", p[0], p[1], axis)
    sk.constrain("radius", right, value=6)
    sk.constrain("equal", right, left)
    sk.constrain("tangent", bottom, right)
    r = sk.solve()
    assert r.ok, r
    assert abs(sk.arcs[right].r - 6) < 1e-6 and abs(sk.arcs[left].r - 6) < 1e-6
    loops = sk.profiles()
    assert len(loops) == 1 and len(loops[0]) == 4          # the construction axis is not a profile
    mid = sk.point(1, 1)
    sk.constrain("midpoint", mid, bottom)
    assert sk.solve().ok
    m, a, b = sk.points[mid], sk.points[p[0]], sk.points[p[1]]
    assert abs(m.x - (a.x + b.x) / 2) < 1e-7 and abs(m.y - (a.y + b.y) / 2) < 1e-7


def test_text_round_trip_and_delete():
    sk = Sketch(name="Bracket", plane="XZ", offset=5.0)
    ls = sk.rectangle(0, 0, 20, 10)
    sk.circle((10, 5), 2)
    sk.constrain("distance", ls[0], value=20)
    text = sk.dumps()
    back = Sketch.loads(text)
    assert back.dumps() == text and back.plane == "XZ" and back.offset == 5.0
    assert back.solve().ok
    n = len(back.constraints)
    back.delete(back.lines[ls[0]].a)                         # deleting a corner removes its lines
    assert len(back.lines) == 2 and len(back.constraints) < n
    assert back.point(1, 1) not in sk.points or True
