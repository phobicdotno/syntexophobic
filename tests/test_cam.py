import math

from syntexophobic import cam, geometry
from syntexophobic.project import Project


def test_geometry(pocket_part):
    shape = geometry.load_step(pocket_part)
    box = geometry.bounding_box(shape)
    assert round(box.xmax - box.xmin) == 60 and round(box.zmax - box.zmin) == 15
    assert geometry.upward_flat_levels(shape) == [10.0, 13.0, 15.0]
    # through the pocket: outer rectangle + pocket outline (hole) + boss circle
    assert [len(s) for s in geometry.slice_at(shape, 12.0)] == [3]


def test_levels_land_on_floors():
    lv = cam.cutting_levels(16, 0, 1.5, [10.0, 13.0, 15.0])
    assert {10.0, 13.0, 15.0, 0.0} <= set(lv) and lv == sorted(lv, reverse=True)


def _feed_samples(tp, step=0.5):
    """Every point the tool centre passes while feeding, every `step` mm."""
    last = None
    for kind, x, y, z, f in tp.positions():
        if kind == "feed" and last:
            n = max(1, math.ceil(math.dist(last, (x, y, z)) / step))
            for i in range(1, n + 1):
                t = i / n
                yield tuple(a + (b - a) * t for a, b in zip(last, (x, y, z)))
        last = (x, y, z)


def _gouges(x, y, z, r):
    """Does a flat endmill of radius r with its tip at (x, y, z) cut into the test part?"""
    tol = 0.02
    if z >= 15 - 1e-6:
        return False                                  # above the block
    in_pocket = 15 + r - tol <= x <= 45 - r + tol and 10 + r - tol <= y <= 30 - r + tol
    if z < 10 - 1e-6:
        return True                                   # below every floor
    if z < 13 - 1e-6 and math.hypot(x - 30, y - 20) < 4 + r - tol:
        return True                                   # into the boss
    dx, dy = max(0 - x, 0, x - 60), max(0 - y, 0, y - 40)
    outside_block = math.hypot(dx, dy) >= r - tol         # distance from the block, round corners
    return not (in_pocket or outside_block)


def test_toolpaths_never_cut_the_part(pocket_part, tmp_path):
    p = Project.create(tmp_path / "proj", pocket_part)
    for op in p.job.operations:
        op.bottom = 10.0            # machine down to the pocket floor, outside walls included
        op.leave = 0.0 if op.type == "finish" else op.leave
    p.save_job()
    tps = p.toolpaths()
    r = p.job.tools[0].diameter / 2
    for tp in tps:
        samples = list(_feed_samples(tp))
        assert samples, f"{tp.op.name} produced no cutting moves"
        bad = [s for s in samples if _gouges(*s, r)]
        assert not bad, f"{tp.op.name} gouges the part at {bad[:3]}"
        # it must actually reach the pocket floor and go around the boss
        assert any(abs(z - 10) < 1e-6 and 15 < x < 45 for x, y, z in samples)
    g = p.gcode(tps)
    assert g.startswith("(Syntexophobic") and "M30" in g and "G43" not in g
    zs = [float(w[1:]) for line in g.splitlines() for w in line.split() if w.startswith("Z")]
    assert abs(min(zs) + 6.0) < 1e-6      # stock top is Z0 with 1 mm skin: pocket floor = -6


def test_history_roundtrip(pocket_part, tmp_path):
    p = Project.create(tmp_path / "h", pocket_part)
    p.job.tools[0].diameter = 3.175
    p.save_job()
    assert p.history.save("1/8 inch endmill")
    log = p.history.log()
    assert [v.message for v in log] == ["1/8 inch endmill", f"New project from {pocket_part.name}"]
    assert "diameter = 3.175" in p.history.diff(log[1].hash, log[0].hash)
    p.history.restore(log[1].hash)
    p.reload()
    assert p.job.tools[0].diameter == 6.0
