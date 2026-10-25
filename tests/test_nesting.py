import math
import random
import time

import pyclipper
import pytest

from syntexophobic.nesting import Placement, nest, transform

SCALE = 1000


def rect(w, h, x=0.0, y=0.0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def blob(r, n=200, seed=0, stretch=1.0):
    """A wavy closed outline around the origin, n points."""
    return [(r * (1 + 0.3 * math.sin(5 * t + seed)) * math.cos(t) * stretch,
             r * (1 + 0.3 * math.sin(5 * t + seed)) * math.sin(t))
            for t in (2 * math.pi * i / n for i in range(n))]


def ell(a, b, t):
    """An L: a x a square with a (a-t) x (a-t) bite out of the upper right."""
    return [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]


def _int(ring):
    return [(round(x * SCALE), round(y * SCALE)) for x, y in ring]


def check(parts, placements, w, h, gap, margin):
    """Every part once, inside the margin, and every pair at least `gap` apart."""
    assert sorted(p.part for p in placements) == list(range(len(parts)))
    assert all(p.angle in (0, 90, 180, 270) for p in placements)
    placed = {}
    for p in placements:
        outline = transform(parts[p.part][0], p)
        xs, ys = [x for x, _ in outline], [y for _, y in outline]
        assert min(xs) >= margin - 1e-6 and max(xs) <= w - margin + 1e-6
        assert min(ys) >= margin - 1e-6 and max(ys) <= h - margin + 1e-6
        placed.setdefault(p.sheet, []).append(outline)
    assert sorted(placed) == list(range(len(placed)))   # no empty sheet in between
    for outlines in placed.values():
        for a in range(len(outlines)):
            # grow one outline by a hair less than the gap: it must not reach any other
            pco = pyclipper.PyclipperOffset()
            pco.AddPath(_int(outlines[a]), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
            grown = pco.Execute(max(gap - 0.01, 0.0) * SCALE)
            for b in range(a + 1, len(outlines)):
                pc = pyclipper.Pyclipper()
                pc.AddPaths(grown, pyclipper.PT_SUBJECT, True)
                pc.AddPath(_int(outlines[b]), pyclipper.PT_CLIP, True)
                hit = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO,
                                 pyclipper.PFT_NONZERO)
                area = sum(abs(pyclipper.Area(q)) for q in hit) / SCALE ** 2
                assert area < 1e-3, f"parts {a} and {b} closer than {gap} mm"
    return placed


def test_transform_rotates_then_translates():
    ring = [(1.0, 0.0), (2.0, 1.0)]
    assert transform(ring, Placement(0, 0, 0, 10, 20)) == [(11, 20), (12, 21)]
    assert transform(ring, Placement(0, 0, 90, 10, 20)) == [(10, 21), (9, 22)]
    assert transform(ring, Placement(0, 0, 180, 10, 20)) == [(9, 20), (8, 19)]
    assert transform(ring, Placement(0, 0, 270, 10, 20)) == [(10, 19), (11, 18)]


def test_mixed_shapes_never_overlap():
    rng = random.Random(3)
    parts = []
    for k in range(40):
        kind = k % 3
        if kind == 0:
            parts.append([rect(rng.uniform(10, 80), rng.uniform(10, 60), rng.uniform(-50, 50), 7)])
        elif kind == 1:
            parts.append([blob(rng.uniform(8, 30), 60, seed=k), blob(3, 12)])   # with a hole
        else:
            parts.append([ell(rng.uniform(30, 70), rng.uniform(30, 70), rng.uniform(8, 15))])
    pl = nest(parts, 300, 200, gap=3, margin=5)
    check(parts, pl, 300, 200, 3, 5)


def test_gap_and_margin_respected_for_other_values():
    parts = [[blob(20, 80, seed=k)] for k in range(12)]
    pl = nest(parts, 250, 180, gap=6, margin=12)
    check(parts, pl, 250, 180, 6, 12)
    pl = nest(parts, 250, 180, gap=0, margin=0)
    check(parts, pl, 250, 180, 0, 0)


def test_overflow_opens_new_sheets():
    # 90 + 3 + 90 fits twice across the 190 mm usable width: four squares a sheet
    parts = [[rect(90, 90)] for _ in range(10)]
    pl = nest(parts, 200, 200, gap=3, margin=5)
    sheets = check(parts, pl, 200, 200, 3, 5)
    assert len(sheets) == 3 and [len(v) for v in sheets.values()] == [4, 4, 2]


def test_rotation_when_it_is_the_only_way():
    strip = [[rect(50, 150)]]               # 150 tall, usable height is only 110
    pl = nest(strip, 200, 120, gap=3, margin=5)
    assert pl[0].angle in (90, 270)
    check(strip, pl, 200, 120, 3, 5)
    with pytest.raises(ValueError):
        nest(strip, 200, 120, rotate=False)


def test_rotation_saves_a_sheet():
    # the big part leaves a 57 mm wide column; the strips only fit there standing up
    parts = [[rect(140, 100)], [rect(90, 22)], [rect(90, 22)]]
    pl = nest(parts, 210, 110, gap=3, margin=5)
    sheets = check(parts, pl, 210, 110, 3, 5)
    assert len(sheets) == 1
    assert pl[1].angle in (90, 270) and pl[2].angle in (90, 270)
    pl = nest(parts, 210, 110, gap=3, margin=5, rotate=False)
    assert len(check(parts, pl, 210, 110, 3, 5)) == 2


def test_small_part_sits_in_the_notch_of_a_big_one():
    # the L fills the whole usable 200 x 200; only true-shape nesting finds the notch
    parts = [[ell(200, 200, 60)], [rect(100, 100)], [blob(8, 100)]]
    pl = nest(parts, 210, 210, gap=3, margin=5)
    sheets = check(parts, pl, 210, 210, 3, 5)
    assert len(sheets) == 1


def test_too_big_raises_with_index_and_size():
    parts = [[rect(50, 50)], [rect(250, 40)]]
    with pytest.raises(ValueError, match=r"part 1 \(250\.0 x 40\.0 mm\)"):
        nest(parts, 200, 300, margin=5, rotate=False)
    pl = nest(parts, 200, 300, margin=5)           # but it fits standing up
    assert pl[1].angle in (90, 270)
    with pytest.raises(ValueError, match="part 0"):
        nest([[rect(300, 300)]], 200, 200)


def test_exact_fit_on_an_empty_sheet():
    parts = [[rect(190, 90, 33, -12)]]            # exactly the usable area
    pl = nest(parts, 200, 100, margin=5)
    check(parts, pl, 200, 100, 3, 5)


def test_deterministic():
    parts = [[blob(10 + k % 7 * 4, 50, seed=k)] for k in range(30)]
    assert nest(parts, 200, 150) == nest(parts, 200, 150)


def test_empty_and_bad_input():
    assert nest([], 100, 100) == []
    with pytest.raises(ValueError):
        nest([[[(0, 0), (1, 1)]]], 100, 100)
    with pytest.raises(ValueError):
        nest([[rect(5, 5)]], 10, 10, margin=5)


def test_hundred_detailed_parts_are_fast_enough():
    rng = random.Random(1)
    parts = [[blob(rng.uniform(15, 45), 200, seed=k, stretch=1.5)] for k in range(100)]
    t = time.perf_counter()
    pl = nest(parts, 600, 400)
    took = time.perf_counter() - t
    check(parts, pl, 600, 400, 3, 5)
    assert took < 30   # about 3 s on a laptop; loose so a busy test machine passes
