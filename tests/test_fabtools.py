import math

import pyclipper
import pytest

from syntexophobic import fabtools as ft


def _area(part):
    return sum(ft._area(r) for r in part)          # holes are clockwise, so they subtract


def _square(x0, y0, x1, y1, ccw=True):
    r = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return r if ccw else r[::-1]


def _in_material(part, pt):
    return bool(ft._inside(ft._material(part), [pt])[0])


# plate 100 x 60 with a 6 x 30 slot cut in from the top edge, outline counter-clockwise
SLOT_PLATE = [[(0, 0), (100, 0), (100, 60), (53, 60), (53, 30), (47, 30), (47, 60), (0, 60)]]


def test_kerf_grows_outline_and_shrinks_hole():
    sq = [_square(0, 0, 50, 50)]
    grown = ft.kerf(sq, 0.2)
    assert _area(grown) == pytest.approx(50.2 ** 2, abs=1e-3)      # sharp corners: square stays square
    assert len(grown[0]) == 4

    holed = [_square(0, 0, 50, 50), _square(20, 20, 30, 30, ccw=False)]
    out = ft.kerf(holed, 0.2)
    assert len(out) == 2
    assert ft._area(out[0]) > 0 and ft._area(out[1]) < 0
    assert -ft._area(out[1]) == pytest.approx(9.8 ** 2, abs=1e-3)
    assert _area(out) == pytest.approx(50.2 ** 2 - 9.8 ** 2, abs=1e-3)


def test_kerf_zero_unchanged_and_orientation_tolerant():
    sq = [_square(0, 0, 10, 10)]
    assert ft.kerf(sq, 0) == sq
    # a clockwise outline still grows
    assert _area(ft.kerf([_square(0, 0, 10, 10, ccw=False)], 1.0)) == pytest.approx(121, abs=1e-3)


def test_dogbones_bite_only_slot_bottom_corners():
    d = 6.0
    r = d / 2
    bites = ft.dogbone_centres(SLOT_PLATE, d)
    assert sorted(v for v, _ in bites) == [(47.0, 30.0), (53.0, 30.0)]
    for v, c in bites:
        assert math.dist(v, c) == pytest.approx(r)          # circle passes through the corner
        assert c[1] > 30 and 47 < c[0] < 53                 # on the bisector, into the slot

    out = ft.dogbones(SLOT_PLATE, d)
    assert len(out) == 1
    verts = set(out[0])
    for corner in [(0, 0), (100, 0), (100, 60), (0, 60), (47, 60), (53, 60)]:
        assert corner in verts                              # convex corners untouched
    for corner in [(47, 30), (53, 30)]:
        assert corner in verts                              # the bite meets the corner exactly

    # material inside each bite circle but outside the slot is gone; just beyond stays
    c53 = dict(bites)[(53.0, 30.0)]
    assert _in_material(SLOT_PLATE, (52.0, 29.5)) and math.dist((52.0, 29.5), c53) < r
    assert not _in_material(out, (52.0, 29.5))
    assert not _in_material(out, (53.5, 31.0))
    assert _in_material(out, (52.0, 28.0)) and _in_material(out, (55.0, 31.0))

    # removed area: the bite circles (they overlap under a 6 mm slot) minus the slot itself
    pc = pyclipper.Pyclipper()
    pc.AddPaths(ft._to_int([ft._circle(c, r, v) for v, c in bites]), pyclipper.PT_SUBJECT, True)
    pc.AddPath(ft._to_int([_square(47, 30, 53, 61)])[0], pyclipper.PT_CLIP, True)
    left = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    lenses = sum(abs(pyclipper.Area(q)) for q in left) / ft.SCALE ** 2
    assert lenses > 1
    assert _area(SLOT_PLATE) - _area(out) == pytest.approx(lenses, abs=1e-3)


def test_dogbones_holes_and_angle_range_and_zero():
    plate = [_square(0, 0, 80, 80), _square(30, 30, 50, 40, ccw=False)]
    assert len(ft.dogbone_centres(plate, 4)) == 4           # every corner of a rectangular hole
    assert ft.dogbone_centres(plate, 4, max_angle_deg=80) == []
    assert ft.dogbones(plate, 0) == plate
    out = ft.dogbones(plate, 4)
    assert len(out) == 2 and _area(out) < _area(plate)
    assert not _in_material(out, (31.0, 29.5))              # bite pokes past the hole corner
    assert _in_material(out, (29.5, 29.5))                  # but not diagonally out of it


def test_tbone_on_long_wall():
    bites = dict(ft.dogbone_centres(SLOT_PLATE, 6, style="tbone"))
    assert bites[(53.0, 30.0)] == pytest.approx((53.0, 33.0))
    assert bites[(47.0, 30.0)] == pytest.approx((47.0, 33.0))
    out = ft.dogbones(SLOT_PLATE, 6, style="tbone")
    assert not _in_material(out, (55.0, 33.0))              # sideways into the wall
    assert _in_material(out, (50.0, 29.5))                  # slot bottom keeps full depth
    with pytest.raises(ValueError):
        ft.dogbone_centres(SLOT_PLATE, 6, style="round")


def test_label_inside_its_box():
    s = ft.label("A12", 10, 20, 5)
    assert s
    pts = [p for line in s for p in line]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    assert min(xs) == pytest.approx(10) and min(ys) == pytest.approx(20)
    assert max(ys) == pytest.approx(25, abs=0.2) and max(xs) < 10 + 3 * 5
    turned = [p for line in ft.label("A12", 10, 20, 5, angle=90) for p in line]
    assert max(x for x, _ in turned) == pytest.approx(10) and min(y for _, y in turned) == pytest.approx(20)


def _check_label(part, strokes):
    mat = ft._material(part)
    for line in strokes:
        for p in line:
            assert ft._inside(mat, [p])[0], p


def test_label_inside_avoids_holes_and_edges():
    # ring plate: the middle is a hole, so the label must go in the frame
    part = [_square(0, 0, 60, 60), _square(10, 10, 50, 50, ccw=False)]
    s = ft.label_inside(part, "P7", 5)
    assert s
    _check_label(part, s)
    for line in s:
        for x, y in line:
            assert not (9 < x < 51 and 9 < y < 51)          # clear of the hole by the margin

    s = ft.label_inside(SLOT_PLATE, "PART 12", 6)
    assert s
    _check_label(SLOT_PLATE, s)
    for line in s:
        for x, y in line:
            assert not (46 < x < 54 and y > 29)


def test_label_inside_shrinks_or_gives_up():
    strip = [_square(0, 0, 80, 6)]                          # only ~4 mm of room for the text
    s = ft.label_inside(strip, "AB", 20)
    assert s
    _check_label(strip, s)
    ys = [y for line in s for _, y in line]
    assert max(ys) - min(ys) <= 4 + 1e-6
    assert ft.label_inside([_square(0, 0, 3, 3)], "AB", 5) == []
