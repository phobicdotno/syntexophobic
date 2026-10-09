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
    assert g.startswith("(Syntexophobic") and g.rstrip().endswith("M2") and "G43" not in g
    import re
    zs = [float(w[1:]) for line in g.splitlines() if "G53" not in line
          for w in re.sub(r"\(.*?\)", "", line).split() if w.startswith("Z") and len(w) > 1]
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


def test_ramp_never_plunges(pocket_part, tmp_path):
    p = Project.create(tmp_path / "ramp", pocket_part)
    p.job.operations[0].bottom = 10.0
    p.job.operations[0].ramp_angle = 3.0
    tp = cam.generate(p.setup(), p.job.operations[0])
    slope = math.tan(math.radians(3.0)) + 1e-6
    last = None
    descents = 0
    for kind, x, y, z, f in tp.positions():
        if last and kind == "feed" and z < last[2] - 1e-9:
            descents += 1
            assert (last[2] - z) <= slope * math.dist(last[:2], (x, y)) + 1e-6, "steeper than the ramp angle"
        last = (x, y, z)
    assert descents > 0


def test_ramp_lap_covers_the_whole_loop():
    loop = [(0, 0), (10, 0), (10, 10), (0, 10)]
    pts = cam._ramp_and_lap(loop, 1.0, 0.0, 5.0)
    at_depth = [(x, y) for x, y, z in pts if z == 0.0]
    for corner in loop:                        # every corner is visited at full depth
        assert any(math.dist(corner, p) < 1e-9 for p in at_depth)
    assert pts[0] == (0, 0, 1.0) and pts[-1][2] == 0.0


def test_profile_tabs_hold_the_part(pocket_part, tmp_path):
    p = Project.create(tmp_path / "tabs", pocket_part)
    prof = p.job.operations[1]
    prof.type, prof.name, prof.tabs, prof.tab_height, prof.bottom = "profile", "Cut out", 4, 2.0, None
    p.job.operations = [prof]
    tp = cam.generate(p.setup(), prof)
    assert len(tp.tabs) == 4
    r = p.job.tools[0].diameter / 2
    samples = list(_feed_samples(tp, 0.2))
    assert any(abs(z) < 1e-6 for x, y, z in samples)            # it does cut through (model bottom is Z=0)
    for c in tp.tabs:
        near = [z for x, y, z in samples if math.dist(c, (x, y)) < prof.tab_width / 2 + r - 0.05]
        assert near and min(near) >= 2.0 - 1e-6                   # never below the tab top at a tab
    for x, y, z in samples:                                        # profile stays outside the block
        dx, dy = max(0 - x, 0, x - 60), max(0 - y, 0, y - 40)
        assert math.hypot(dx, dy) >= r - 0.02


def _signed_area(paths):
    import pyclipper
    return sum(pyclipper.Area(p) for p in paths) / cam.SCALE ** 2


def test_mesh_slices_match_exact_sections(pocket_part):
    """The fast mesh slicer must agree with OpenCascade's exact sections to within a few µm."""
    import pyclipper
    shape = geometry.load_step(pocket_part)
    slicer = geometry.MeshSlicer(shape)
    for z in (0.5, 5.0, 10.01, 11.0, 12.5, 13.01, 14.0, 14.99):
        exact = cam._region_from_slices(geometry.slice_at(shape, z))
        fast = cam._region_from_slices(slicer.slice(z))
        diff = _signed_area(cam._boolean(exact, fast, pyclipper.CT_XOR))
        outline = sum(sum(math.dist(p[i - 1], p[i]) for i in range(len(p))) for p in exact) / cam.SCALE
        assert diff / outline < 0.005, f"z={z}: mean deviation {diff / outline * 1000:.1f} µm"
    assert slicer.fallbacks == 0


def test_backend_slicing_matches_reference():
    """Vectorised slicing on the active backend gives the same segments as plain geometry."""
    import numpy as np
    from syntexophobic import compute
    be = compute.get("numpy")
    tri = be.asarray(np.array([[[0, 0, 0], [10, 0, 0], [0, 10, 10]],      # crosses z=5
                               [[0, 0, 6], [1, 0, 6], [0, 1, 7]]], float))  # entirely above
    segs = compute.slice_segments(be, tri, 5.0)
    assert segs.shape == (1, 2, 2)
    got = sorted(map(tuple, np.round(segs[0], 9)))
    assert got == [(0.0, 5.0), (5.0, 5.0)]
    assert "numpy" in compute.available()


def test_simulation_finds_no_gouges_and_clears_the_pocket(pocket_part, tmp_path):
    from syntexophobic import simulate
    p = Project.create(tmp_path / "sim", pocket_part)
    for op in p.job.operations:
        op.bottom = 10.0
    tps = p.toolpaths()
    res = simulate.simulate(p.setup(), tps, res=0.25)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm in {res.gouge_cells} cells"
    xs, ys = res.grid.centres()
    j, i = int(round((20 - ys[0]) / 0.25)), int(round((20 - xs[0]) / 0.25))   # pocket floor beside the boss
    assert abs(res.stock[j, i] - 10.0) < 1e-6
    assert res.removed_cm3 > 0


def test_simulation_catches_a_bad_move(pocket_part, tmp_path):
    from syntexophobic import simulate
    p = Project.create(tmp_path / "bad", pocket_part)
    tp = cam.Toolpath(p.job.operations[0], 6.0, moves=[
        cam.Move("rapid", 5, 20, 20), cam.Move("feed", 5, 20, 12, 300), cam.Move("feed", 55, 20, 12, 1000)])
    res = simulate.simulate(p.setup(), [tp], res=0.25)
    assert res.gouge_cells > 0 and abs(res.gouge_depth - 3.0) < 0.05    # 3 mm into the 15 mm block


DEMO = __import__("pathlib").Path(__file__).resolve().parent.parent / "examples" / "demo-plate.step"


def test_find_holes_skips_fillets_and_bosses():
    holes = geometry.find_holes(geometry.load_step(DEMO))
    assert sorted((h.x, h.y, h.diameter) for h in holes) == sorted(
        [(8, 8, 5.2), (92, 8, 5.2), (8, 62, 5.2), (92, 62, 5.2), (38, 35, 5.0), (62, 35, 5.0)])
    assert {h.top for h in holes} == {20.0, 17.0} and {h.bottom for h in holes} == {0.0}


def test_drill_and_bore_cut_the_holes_without_gouging(tmp_path):
    from syntexophobic import simulate
    from syntexophobic.job import Operation, Tool
    p = Project.create(tmp_path / "holes", DEMO)
    p.job.tools = [Tool(1, "6 mm endmill", 6.0), Tool(2, "5 mm drill", 5.0), Tool(3, "3 mm endmill", 3.0)]
    p.job.operations = [
        Operation(type="rough", name="Rough", tool=1, bottom=4.0),
        Operation(type="drill", name="Drill 5", tool=2, stepdown=4.0),
        Operation(type="bore", name="Bore", tool=3, stepover=0.4, hole_min=5.1),   # the 5.2 corner holes
    ]
    tps = p.toolpaths()
    drill, bore = tps[1], tps[2]
    assert len(drill.levels) == 6 and len(bore.levels) == 4
    res = simulate.simulate(p.setup(), tps, res=0.2)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm"
    xs, ys = res.grid.centres()
    for x, y in ((38, 35), (8, 8)):                         # a drilled and a bored hole, all the way down
        j, i = int(round((y - ys[0]) / 0.2)), int(round((x - xs[0]) / 0.2))
        assert res.stock[j, i] <= 0.0 + 1e-6
    # drilling pecks: no single feed deeper than the peck depth below the previous one
    last_z = None
    for kind, x, y, z, f in drill.positions():
        if kind == "feed" and last_z is not None:
            assert last_z - z <= 4.0 + 0.5 + 1e-6
        last_z = z


def test_gcode_round_trip_simulates_clean(tmp_path):
    """Write G-code, read it back as the machine would run it, and simulate that."""
    from syntexophobic import gcode, simulate
    from syntexophobic.job import Operation, Tool
    p = Project.create(tmp_path / "rt", DEMO)
    p.job.tools = [Tool(1, "6 mm endmill", 6.0), Tool(2, "5 mm drill", 5.0)]
    p.job.operations = [Operation(type="rough", name="Rough", tool=1, bottom=4.0),
                        Operation(type="finish", name="Finish", tool=1, bottom=4.0, leave=0.0, stepdown=4.0),
                        Operation(type="drill", name="Drill", tool=2, stepdown=3.0)]
    tps = p.toolpaths()
    text = p.gcode(tps)
    assert [l for l in text.splitlines() if l.startswith("M6")] == ["M6 T1", "M6 T2"]   # first tool too
    assert text.count("G53 G0 Z-5") == 3 and text.rstrip().endswith("M2") and "G94" not in text
    p.job.machine.post = "grbl"
    plain = p.gcode(tps)
    assert plain.count("M0") == 1 and "M6" not in plain and "G53" not in plain
    back = gcode.read(text, p.job, p.setup().stock)
    assert [t.op.name for t in back] == ["Rough", "Finish", "Drill"]
    def cutting(tp):
        """Feed end points, rounded like the post, without moves that go nowhere."""
        out = []
        for kind, x, y, z, f in tp.positions():
            q = tuple(round(v, 3) for v in (x, y, z))
            if kind == "feed" and (not out or out[-1] != q):
                out.append(q)
        return out

    for a, b in zip(tps, back):                          # identical cutting moves, one to one
        pa, pb = cutting(a), cutting(b)
        assert len(pa) == len(pb) > 0
        assert max(math.dist(u, v) for u, v in zip(pa, pb)) < 0.002
    res = simulate.simulate(p.setup(), back, res=0.25)
    assert res.gouge_cells == 0


def test_spacemouse_protocol_and_daemon_client(tmp_path):
    """Parse spacenavd events and receive them through a fake daemon socket."""
    import os, socket, struct
    from PySide6.QtCore import QCoreApplication
    from syntexophobic import spacemouse as sm
    m = sm.parse(struct.pack("=8i", 0, 10, -20, 30, 1, 2, 3, 16))
    assert (m.x, m.y, m.z, m.rx, m.ry, m.rz, m.period) == (10, -20, 30, 1, 2, 3, 16)
    assert sm.parse(struct.pack("=8i", 1, 0, 0, 0, 0, 0, 0, 0)) == ("button", 0, True)
    assert sm.parse(struct.pack("=8i", 2, 1, 0, 0, 0, 0, 0, 0)) == ("button", 1, False)

    app = QCoreApplication.instance() or QCoreApplication([])
    path = str(tmp_path / "spnav.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    client = sm.SpaceMouse(paths=(path,))
    conn, _ = server.accept()
    got = []
    client.motion.connect(lambda ev: got.append(("motion", ev.x, ev.rz)))
    client.button.connect(lambda n, p: got.append(("button", n, p)))
    # two motion events in one read: only the newest is delivered; then a button press
    conn.sendall(struct.pack("=8i", 0, 1, 0, 0, 0, 0, 5, 8) + struct.pack("=8i", 0, 7, 0, 0, 0, 0, 9, 8)
                 + struct.pack("=8i", 1, 1, 0, 0, 0, 0, 0, 0))
    for _ in range(50):
        app.processEvents()
        if len(got) >= 2:
            break
    assert ("motion", 7, 9) in got and ("button", 1, True) in got
    assert ("motion", 1, 5) not in got
    conn.close()
    server.close()
    os.unlink(path)


def test_feeds_and_speeds_suggestions():
    from syntexophobic import feeds
    from syntexophobic.job import Job, Machine, Operation, Tool
    end6 = Tool(1, "6 mm", 6.0, 2)
    rough = feeds.suggest("hardwood", end6, Operation(type="rough"), Machine(rpm=18000))
    assert rough["feed"] == 1620 and rough["plunge"] == 540 and rough["stepdown"] == 1.2
    alu = feeds.suggest("aluminium", end6, Operation(type="rough"), Machine(rpm=18000))
    assert alu["stepdown"] == 0.3 and alu["feed"] < rough["feed"]
    small = feeds.suggest("hardwood", Tool(2, "1/8", 3.175, 2), Operation(type="rough"), Machine(rpm=18000))
    assert small["feed"] < rough["feed"]                      # thinner chip for a smaller cutter
    assert feeds.suggest("hdpe", Tool(3, "big", 12, 4), Operation(), Machine(rpm=24000))["feed"] == feeds.MAX_FEED
    ad = feeds.suggest("hardwood", end6, Operation(type="adaptive"), Machine(rpm=18000))
    assert ad["stepdown"] == 6.0 and ad["stepover"] == 0.15 and ad["feed"] > rough["feed"]   # chip thinning
    job = Job()
    job.stock.material = "mdf"
    op = job.operations[0]
    assert feeds.apply(job, op)["feed"] == op.feed == 2160


def test_rest_machining_only_cuts_what_the_big_tool_left(pocket_part, tmp_path):
    from syntexophobic import simulate
    from syntexophobic.job import Operation, Tool
    p = Project.create(tmp_path / "rest", pocket_part)
    p.job.tools = [Tool(1, "6 mm", 6.0), Tool(2, "2 mm", 2.0)]
    big = Operation(type="rough", name="Big", tool=1, bottom=10.0, leave=0.0)
    rest = Operation(type="rough", name="Rest", tool=2, bottom=10.0, leave=0.0, rest_from=1, stepover=0.4)
    p.job.operations = [big, rest]
    tps = p.toolpaths()
    corners = [(15, 10), (45, 10), (15, 30), (45, 30)]
    small_cuts = [(x, y) for kind, x, y, z, f in tps[1].positions() if kind == "feed" and z < 15 - 1e-6]
    assert small_cuts
    # inside the pocket, the small tool only works near the four sharp corners
    in_pocket = [(x, y) for x, y in small_cuts if 15 < x < 45 and 10 < y < 30]
    assert in_pocket and all(min(math.dist(c, q) for c in corners) < 4.5 for q in in_pocket)
    # and it is far less work than roughing the pocket again with the small tool
    full = cam.generate(p.setup(), Operation(type="rough", tool=2, bottom=10.0, leave=0.0, stepover=0.4))
    assert len(tps[1].moves) < 0.5 * len(full.moves)
    res = simulate.simulate(p.setup(), tps, res=0.1)
    assert res.gouge_cells == 0
    xs, ys = res.grid.centres()
    j, i = int(round((10.6 - ys[0]) / 0.1)), int(round((15.6 - xs[0]) / 0.1))   # 0.85 mm from the corner
    assert res.stock[j, i] <= 10.0 + 1e-6          # the 6 mm tool alone leaves this at the top (Z 16)


def _engagement(tp, stock, r, cell=0.1):
    """Worst and 95th-percentile cutter engagement (fraction of the diameter) over all flat
    feed moves, each level measured on a fresh full block (what each level must remove)."""
    import math
    from syntexophobic import adaptive as ad
    x0, y0 = stock.xmin - 2 * r, stock.ymin - 2 * r
    shape = (int((stock.ymax - stock.ymin + 4 * r) / cell), int((stock.xmax - stock.xmin + 4 * r) / cell))
    block = [[(stock.xmin, stock.ymin), (stock.xmax, stock.ymin), (stock.xmax, stock.ymax), (stock.xmin, stock.ymax)]]
    hist, worst = [], 0.0
    for level in tp.levels:
        m = ad.rasterize(block, x0, y0, cell, shape)
        prev = max([l for l in tp.levels if l > level + 1e-9], default=stock.zmax)
        last = None
        for kind, x, y, z, f in tp.positions():
            if last is not None and kind == "feed" and level - 1e-9 <= min(z, last[2]) < prev - 1e-9:
                flat = abs(last[2] - z) < 1e-9 and abs(z - level) < 1e-9
                n = max(1, math.ceil(math.dist(last[:2], (x, y)) / 0.6))
                st = math.dist(last[:2], (x, y)) / n
                for k in range(1, n + 1):
                    px, py = last[0] + (x - last[0]) * k / n, last[1] + (y - last[1]) * k / n
                    if flat and st > 0.3:
                        e = ad._cut_area(m, (px - x0) / cell, (py - y0) / cell, r / cell) * cell * cell / (st * 2 * r)
                        worst = max(worst, e)
                        hist.append(e)
                    ad._clear(m, (px - x0) / cell, (py - y0) / cell, r / cell)
            last = (x, y, z)
    hist.sort()
    return worst, (hist[int(len(hist) * 0.95)] if hist else 0.0)


def test_adaptive_clears_without_gouging_and_never_slots(pocket_part, tmp_path):
    from syntexophobic import simulate
    from syntexophobic.job import Operation
    p = Project.create(tmp_path / "adapt", pocket_part)
    op = Operation(type="adaptive", name="Adaptive", tool=1, stepdown=6.0, stepover=0.15, leave=0.0, bottom=10.0)
    p.job.operations = [op]
    tp = cam.generate(p.setup(), op)
    res = simulate.simulate(p.setup(), [tp], res=0.2)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f}"
    xs, ys = res.grid.centres()
    pocket = [(x, y) for x in (20, 30, 40) for y in (14, 20, 26) if math.hypot(x - 30, y - 20) > 4 + 3.2]
    for x, y in pocket:                                  # pocket floor cleared around the boss
        j, i = int(round((y - ys[0]) / 0.2)), int(round((x - xs[0]) / 0.2))
        assert res.stock[j, i] <= 10.0 + 1e-6, (x, y, res.stock[j, i])

    # engagement per level on a fresh block: adaptive stays near its stepover,
    # concentric roughing buries the cutter in a full-width slot
    worst, p95 = _engagement(tp, p.setup().stock, 3.0)
    rough = cam.generate(p.setup(), Operation(type="rough", tool=1, stepdown=6.0, stepover=0.45, leave=0.0, bottom=10.0))
    rough_worst, _ = _engagement(rough, p.setup().stock, 3.0)
    assert rough_worst > 0.9                                 # the slotting this replaces
    assert p95 < 0.35, f"95% of adaptive cuts under 0.35 D, got {p95:.2f}"
    assert worst < 0.75, f"worst adaptive engagement {worst:.2f} of the diameter"


def test_ball_waterline_on_vertical_walls(pocket_part, tmp_path):
    """Vertical walls are where a too-narrow ball keep-out shows: the material does not
    shrink going up, so every band must be widened by the ball's full width at its top."""
    from syntexophobic import simulate
    from syntexophobic.job import Operation, Tool
    p = Project.create(tmp_path / "ballwl", pocket_part)
    p.job.tools = [Tool(1, "4 mm ball", 4.0, 2, "ball")]
    p.job.operations = [Operation(type="finish", name="Waterline", tool=1, stepdown=0.5, leave=0.0, bottom=10.0)]
    res = simulate.simulate(p.setup(), p.toolpaths(), res=0.05)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm in {res.gouge_cells} cells"


def test_tool_library_and_exact_stock(pocket_part, tmp_path, monkeypatch):
    from syntexophobic import library
    from syntexophobic.job import Tool
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    tools = library.load()                                 # starter library created
    assert any(abs(t.diameter - 6.35) < 1e-9 for t in tools) and library.path().exists()
    library.add(Tool(99, "Custom 4 mm ball", 4.0, 3, "ball"))
    assert library.load()[-1].name == "Custom 4 mm ball"            # numbers are given per job
    p = Project.create(tmp_path / "lib", pocket_part)
    new = library.into_job(p.job, library.load()[-1])
    assert new.id == 2 and p.job.tools[-1].shape == "ball"

    # exact blank 80 x 50 x 20, centred on the 60 x 40 x 15 part, 1 mm above it
    p.job.stock.width, p.job.stock.depth, p.job.stock.height = 80, 50, 20
    p.save_job()
    s = p.setup().stock
    assert (s.xmax - s.xmin, s.ymax - s.ymin, s.zmax - s.zmin) == (80, 50, 20)
    assert (s.xmin, s.ymin, s.zmax) == (-10, -5, 16) and p.setup().warning == ""
    p.job.stock.width = 50
    p.save_job()
    assert "smaller" in p.setup().warning
