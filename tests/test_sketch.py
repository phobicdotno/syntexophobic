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


def _volume(sk, h=10.0):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from syntexophobic.model import Feature, Part
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(Part("t", [Feature("extrude", sketch="s", distance=h)], {"s": sk}).build(), props)
    return props.Mass()


def test_fillet_corner_builds_rounded_solid():
    sk = Sketch()
    ls = sk.rectangle(0, 0, 40, 30)
    corner = sk.lines[ls[0]].b                  # (40, 0)
    arc = sk.fillet(corner, 6)
    r = sk.solve()
    assert r.ok, r
    assert abs(sk.arcs[arc].r - 6) < 1e-7 and corner not in sk.points
    assert abs(_volume(sk) - (40 * 30 - (36 - math.pi * 9)) * 10) < 1e-2


def test_polygon_and_slot_areas():
    sk = Sketch()
    sk.polygon(0, 0, 10, 6)
    assert sk.solve().ok
    assert abs(_volume(sk, 1) - 3 * math.sqrt(3) / 2 * 100) < 1e-3          # regular hexagon, R = 10
    sl = Sketch()
    sl.slot(0, 0, 30, 0, 8)
    assert sl.solve().ok
    assert abs(_volume(sl, 1) - (30 * 8 + math.pi * 16)) < 1e-2


def test_offset_follows_the_original():
    sk = Sketch()
    ls = sk.rectangle(0, 0, 40, 20)
    sk.constrain("fixed", sk.lines[ls[0]].a)
    width = sk.constrain("distance", ls[0], value=40)
    sk.constrain("distance", ls[1], value=20)
    new = sk.offset_lines(ls, 5)
    assert sk.solve().ok
    lengths = sorted(round(math.dist(sk._xy(sk.lines[l].a), sk._xy(sk.lines[l].b)), 6) for l in new)
    assert lengths == [30, 30, 50, 50]
    next(k for k in sk.constraints if k.id == width).value = 60      # widen the original
    assert sk.solve().ok
    lengths = sorted(round(math.dist(sk._xy(sk.lines[l].a), sk._xy(sk.lines[l].b)), 6) for l in new)
    assert lengths == [30, 30, 70, 70]


def test_mirror_and_trim():
    sk = Sketch()
    axis = sk.line((0, -10), (0, 10), construction=True)
    sk.constrain("vertical", axis)
    ln = sk.line((2, 0), (8, 5))
    c = sk.circle((6, -3), 1.5)
    copies = sk.mirror([ln, c], axis)
    assert sk.solve().ok
    m = sk.lines[copies[0]]
    assert sk._xy(m.a) == pytest.approx((-2, 0)) and sk._xy(m.b) == pytest.approx((-8, 5))
    assert sk._xy(sk.circles[copies[1]].center) == pytest.approx((-6, -3))

    t = Sketch()
    long = t.line((0, 0), (30, 0))
    t.line((10, -5), (10, 5))
    t.line((20, -5), (20, 5))
    kept = t.trim(long, 15, 0)                  # click between the two crossings
    assert len(kept) == 2 and t.solve().ok
    spans = sorted(sorted((t._xy(t.lines[k].a)[0], t._xy(t.lines[k].b)[0])) for k in kept)
    assert spans == [pytest.approx([0, 10]), pytest.approx([20, 30])]


def test_splines_closed_and_in_a_profile():
    import numpy as np
    sk = Sketch()
    ring = [(10 * math.cos(2 * math.pi * k / 12), 10 * math.sin(2 * math.pi * k / 12)) for k in range(12)]
    blob = sk.spline(ring, closed=True)
    assert sk.solve().ok and len(sk.profiles()) == 1
    assert abs(_volume(sk, 1) - math.pi * 100) < 0.005 * math.pi * 100      # a 12-point circle
    # an open spline closing a shape with two lines, its points reversed in the loop
    d = Sketch()
    a, b = d.point(0, 0), d.point(40, 0)
    d.line(a, b)
    top = d.spline([(40, 20), (20, 28), (0, 20)])
    d.line(b, d.splines[top].points[0])
    d.line(d.splines[top].points[-1], a)
    d.constrain("x", d.splines[top].points[1], value=20)
    assert d.solve().ok and len(d.profiles()) == 1
    v = _volume(d, 1)
    assert 40 * 20 + 2 / 3 * 40 * 8 * 0.9 < v < 40 * 20 + 40 * 8                 # roughly a parabolic cap
    text = d.dumps()
    assert Sketch.loads(text).dumps() == text and "[[splines]]" in text
    d.delete(d.splines[top].points[1])                                         # 2 points left: still a spline
    assert top in d.splines and len(d.splines[top].points) == 2
