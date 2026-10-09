import math

import numpy as np

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from syntexophobic import geometry
from syntexophobic.model import Feature, ModelError, Part, select_edges
from syntexophobic.sketch import Sketch


def volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def plate_part() -> Part:
    base = Sketch()
    ls = base.rectangle(0, 0, 100, 70)
    pocket = Sketch(offset=20)
    pocket.rectangle(20, 15, 80, 55)
    holes = Sketch()
    holes.circle((10, 10), 2.5)
    return Part("Plate", [
        Feature("extrude", "Base", sketch="base", distance=20),
        Feature("extrude", "Pocket", sketch="pocket", distance=-8, operation="cut"),
        Feature("extrude", "Holes", sketch="holes", distance=20, operation="cut", count=4, spacing=25, direction="X"),
    ], {"base": base, "pocket": pocket, "holes": holes})


def test_extrude_cut_and_pattern_volumes():
    part = plate_part()
    v = volume(part.build())
    expect = 100 * 70 * 20 - 60 * 40 * 8 - 4 * math.pi * 2.5 ** 2 * 20
    assert abs(v - expect) < 1e-3 * expect
    holes = geometry.find_holes(part.build())
    assert sorted((h.x, h.y) for h in holes) == [(10, 10), (35, 10), (60, 10), (85, 10)]


def test_sketch_hole_profile_and_revolve():
    ring = Sketch()
    ring.circle((0, 0), 20)
    ring.circle((0, 0), 12)          # inside the first: a hole
    part = Part("Ring", [Feature("extrude", sketch="ring", distance=5)], {"ring": ring})
    assert abs(volume(part.build()) - math.pi * (20 ** 2 - 12 ** 2) * 5) < 1e-2

    prof = Sketch(plane="XZ")
    prof.rectangle(0, 0, 10, 30)
    axis = prof.line((0, -5), (0, 40), construction=True)
    cyl = Part("Cyl", [Feature("revolve", sketch="prof", axis=axis, angle=360)], {"prof": prof})
    assert abs(volume(cyl.build()) - math.pi * 10 ** 2 * 30) < 1e-2


def test_fillet_chamfer_shell_mirror():
    base = Sketch()
    base.rectangle(0, 0, 40, 30)
    box = lambda *more: Part("Box", [Feature("extrude", sketch="base", distance=10), *more], {"base": base})
    plain = volume(box().build())
    assert len(select_edges(box().build(), "|Z")) == 4 and len(select_edges(box().build(), ">Z")) == 4
    fil = volume(box(Feature("fillet", radius=5, edges="|Z")).build())
    assert abs(fil - (plain - 4 * (25 - math.pi * 25 / 4) * 10)) < 1e-3       # corner material removed
    ch = volume(box(Feature("chamfer", radius=2, edges=">Z")).build())
    assert ch < plain
    shell = volume(box(Feature("shell", faces=">Z", thickness=2)).build())
    assert abs(shell - (plain - 36 * 26 * 8)) < 1e-2                           # open-top box, 2 mm walls
    mirrored = volume(box(Feature("mirror", plane="YZ")).build())
    assert abs(mirrored - 2 * plain) < 1e-3


def test_errors_are_clear():
    open_sk = Sketch()
    open_sk.line((0, 0), (10, 0))
    with pytest.raises(ModelError, match="no closed profile"):
        Part("x", [Feature("extrude", sketch="s")], {"s": open_sk}).build()
    base = Sketch()
    base.rectangle(0, 0, 10, 10)
    with pytest.raises(ModelError, match="matches no edges"):
        Part("x", [Feature("extrude", sketch="b", distance=5), Feature("fillet", edges="%CIRCLE")], {"b": base}).build()


def test_save_load_round_trip(tmp_path):
    part = plate_part()
    part.save(tmp_path)
    back = Part.load(tmp_path / "model.toml")
    assert abs(volume(back.build()) - volume(part.build())) < 1e-6
    text = (tmp_path / "model.toml").read_text()
    assert 'operation = "cut"' in text and "suppressed" not in text     # defaults left out


def test_designed_part_goes_through_cam(tmp_path):
    """Sketch -> part -> toolpaths -> simulation -> G-code, with no STEP file in between."""
    from syntexophobic import job as jobfile, simulate
    from syntexophobic.history import History
    from syntexophobic.job import Job, Operation, Tool
    from syntexophobic.project import Project
    folder = tmp_path / "designed"
    plate_part().save(folder)
    job = Job(name="plate", model="model.toml",
              tools=[Tool(1, "6 mm", 6.0), Tool(2, "5 mm drill", 5.0)],
              operations=[Operation(type="rough", tool=1, bottom=12.0),
                          Operation(type="drill", tool=2, stepdown=4.0)])
    jobfile.save(job, folder / jobfile.JOB_FILE)
    History(folder).init()
    p = Project(folder)
    tps = p.toolpaths()
    assert len(tps[1].levels) == 4                               # the four patterned holes
    res = simulate.simulate(p.setup(), tps, res=0.25)
    assert res.gouge_cells == 0
    assert "M6 T2" in p.gcode(tps)
    # change the design: deeper pocket. The project rebuilds the part on reload.
    part = Part.load(folder / "model.toml")
    part.features[1].distance = -10
    part.save(folder)
    p.reload()
    assert abs(p.setup().flats[0] - 10.0) < 1e-6 or 10.0 in p.setup().flats


def test_picked_edges_and_faces_survive_a_rebuild():
    from syntexophobic.model import pick_token, select_faces
    base = Sketch()
    ls = base.rectangle(0, 0, 40, 30)
    width = base.constrain("distance", ls[0], value=40)
    base.constrain("fixed", base.lines[ls[0]].a)
    picks = ";".join(pick_token(p) for p in [(0, 0, 5), (40, 30, 5)])      # two vertical corner edges
    part = Part("Box", [Feature("extrude", sketch="base", distance=10),
                        Feature("fillet", radius=4, edges=picks),
                        Feature("shell", faces=pick_token((20, 15, 10)), thickness=1.5)], {"base": base})
    v1 = volume(part.build())
    plain = 40 * 30 * 10
    rounded = plain - 2 * (16 - math.pi * 4) * 10
    assert abs(volume(part.build(2)) - rounded) < 1e-3                      # only the two picked corners
    assert v1 < rounded and len(select_faces(part.build(1), pick_token((20, 15, 10)))) == 1
    next(k for k in base.constraints if k.id == width).value = 40.4         # small design change
    assert abs(volume(part.build(2)) - (rounded + 0.4 * 30 * 10)) < 1e-2    # same corners still found
    next(k for k in base.constraints if k.id == width).value = 60          # the edge moved far away
    with pytest.raises(ModelError, match="pick the edges again"):
        part.build(2)


def test_loft_and_sweep_volumes():
    bottom, top = Sketch(), Sketch(offset=10)
    bottom.rectangle(-10, -10, 10, 10)
    top.rectangle(-5, -5, 5, 5)
    frustum = Part("Loft", [Feature("loft", sketch="b;t")], {"b": bottom, "t": top})
    assert abs(volume(frustum.build()) - 10 / 3 * (400 + 100 + 200)) < 1e-2

    prof = Sketch()
    prof.circle((0, 0), 2)
    straight = Sketch(plane="XZ")
    straight.line((0, 0), (0, 30))
    pipe = Part("Pipe", [Feature("sweep", sketch="p", path="s")], {"p": prof, "s": straight})
    assert abs(volume(pipe.build()) - math.pi * 4 * 30) < 1e-2

    bend = Sketch(plane="XZ")                       # quarter circle, radius 20, starting at the profile
    bend.arc((20, 0), (20, 20), (0, 0))
    elbow = Part("Elbow", [Feature("sweep", sketch="p", path="b")], {"p": prof, "b": bend})
    assert abs(volume(elbow.build()) - math.pi * 4 * (math.pi / 2 * 20)) < 0.05   # Pappus

    forked = Sketch(plane="XZ")
    forked.line((0, 0), (0, 10))
    forked.line((0, 10), (5, 15))
    forked.line((0, 10), (-5, 15))
    with pytest.raises(ModelError, match="one chain"):
        Part("x", [Feature("sweep", sketch="p", path="f")], {"p": prof, "f": forked}).build()


def test_undo_redo_over_project_files(tmp_path):
    from syntexophobic.project import Project, Undo
    p = Project.create_design(tmp_path / "u", "u")
    undo = Undo(p.folder, merge_seconds=0)
    part = Part.load(p.folder / "model.toml")
    sk = Sketch(name="s1")
    sk.rectangle(0, 0, 10, 10)
    part.sketches["s1"] = sk
    part.features.append(Feature("extrude", sketch="s1", distance=5))
    part.save(p.folder)
    undo.record()
    part.features[0].distance = 8
    part.save(p.folder)
    undo.record()
    assert undo.undo() and Part.load(p.folder / "model.toml").features[0].distance == 5
    assert undo.undo() and not Part.load(p.folder / "model.toml").features
    assert not (p.folder / "sketches" / "s1.toml").exists()          # the new sketch is gone too
    assert undo.redo() and undo.redo() and Part.load(p.folder / "model.toml").features[0].distance == 8
    assert not undo.redo()
    merged = Undo(p.folder, merge_seconds=60)                          # rapid edits: one step
    for d in (9, 10, 11):
        part.features[0].distance = d
        part.save(p.folder)
        merged.record()
    assert merged.undo() and Part.load(p.folder / "model.toml").features[0].distance == 8


def test_parallel_ball_finish_on_a_dome(tmp_path):
    """3D finishing: no gouges, and the dome comes out within the scallop height."""
    from syntexophobic import job as jobfile, simulate
    from syntexophobic.history import History
    from syntexophobic.job import Job, Operation, Tool
    from syntexophobic.project import Project
    prof = Sketch(plane="XZ")                      # quarter disc: revolve -> hemisphere r=20
    o = prof.point(0, 0)
    a = prof.point(20, 0)
    b = prof.point(0, 20)
    prof.line(o, a)
    prof.arc(o, a, b)
    axis = prof.line(b, o)
    plinth = Sketch(offset=-5)
    plinth.rectangle(-25, -25, 25, 25)
    dome = Part("Dome", [Feature("revolve", sketch="prof", axis=axis),
                         Feature("extrude", sketch="plinth", distance=5, operation="join")],
                {"prof": prof, "plinth": plinth})
    folder = tmp_path / "dome"
    dome.save(folder)
    jobfile.save(Job(name="dome", model="model.toml", tools=[Tool(1, "6 mm ball", 6.0, 2, "ball")],
                     operations=[Operation(type="parallel", name="Finish 3D", tool=1, stepover=0.08,
                                           leave=0.0, bottom=0.0)]), folder / jobfile.JOB_FILE)
    History(folder).init()
    p = Project(folder)
    tps = p.toolpaths()
    res = simulate.simulate(p.setup(), tps, res=0.1)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm"
    xs, ys = res.grid.centres()
    X, Y = np.meshgrid(xs, ys)
    on_dome = np.hypot(X, Y) < 17                     # away from the steep rim
    left = (res.stock - res.part)[on_dome]
    scallop = 3 - math.sqrt(9 - (0.08 * 6 / 2) ** 2)
    assert left.max() < scallop + 0.12, f"left {left.max():.3f} mm on the dome"
    assert "ball)" in p.gcode(tps) and "TOOL/MILL,6, 3," in p.gcode(tps)


def test_simplifier_never_strays_from_the_samples():
    """Douglas-Peucker: every dropped point stays within tolerance of its replacement, even
    along a long gentle curve where each point is close to its neighbours' chord."""
    from syntexophobic.surface import _simplify
    x = np.linspace(-50, 50, 1001)
    z = 0.002 * x ** 2 / 10                      # sags 0.5 mm over 100 mm: gentle everywhere
    pts = np.column_stack([x, np.zeros_like(x), z])
    kept = np.asarray(_simplify(pts, tol=0.003))
    assert len(kept) < len(pts) / 3
    for px, py, pz in pts:                        # every sample is near the kept polyline
        i = np.searchsorted(kept[:, 0], px)
        a, b = kept[max(i - 1, 0)], kept[min(i, len(kept) - 1)]
        t = 0 if b[0] == a[0] else (px - a[0]) / (b[0] - a[0])
        assert abs(a[2] + t * (b[2] - a[2]) - pz) < 0.0031


def test_rough_and_ball_finish_gentle_curves_without_gouging(tmp_path):
    """Stand-in for a real enclosure lid: a long, gently arched top (R200 over 90 mm), the
    kind of surface where a careless point simplifier merges whole passes into straight
    moves that cut into it. Adaptive roughing, ball finishing, simulated end to end."""
    from syntexophobic import job as jobfile, simulate
    from syntexophobic.history import History
    from syntexophobic.job import Job, Operation, Tool
    from syntexophobic.project import Project
    arch = Sketch(plane="XZ")                          # 90 mm wide, 10 mm high, top an R200 arc
    p0, p1, p2, p3 = arch.point(0, 0), arch.point(90, 0), arch.point(90, 10), arch.point(0, 10)
    arch.line(p0, p1)
    arch.line(p1, p2)
    arch.arc((45, 10 - math.sqrt(200 ** 2 - 45 ** 2)), p2, p3)
    arch.line(p3, p0)
    part = Part("Lid", [Feature("extrude", sketch="arch", distance=-50),
                        Feature("fillet", radius=4, edges="|Z")], {"arch": arch})
    folder = tmp_path / "lid"
    part.save(folder)
    jobfile.save(Job(name="lid", model="model.toml",
                     tools=[Tool(1, "6 mm", 6.0, 2, "flat"), Tool(2, "3 mm ball", 3.0, 2, "ball")],
                     operations=[Operation(type="adaptive", tool=1, stepdown=6.0, stepover=0.15, leave=0.3, bottom=1.0),
                                 Operation(type="parallel", tool=2, stepover=0.15, leave=0.0, bottom=1.0)]),
                 folder / jobfile.JOB_FILE)
    History(folder).init()
    p = Project(folder)
    res = simulate.simulate(p.setup(), p.toolpaths(), res=0.2)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm in {res.gouge_cells} cells"
    left = (res.stock - res.part)[np.isfinite(res.part) & (res.part > 2.0)]
    assert np.percentile(left, 95) < 0.15


def test_waterline_ball_finish_on_a_dome(tmp_path):
    """Waterline (finish op) with a ball end: contours at fine steps; no gouges, and the
    steep rim (where raster passes leave the most) comes out close."""
    from syntexophobic import job as jobfile, simulate
    from syntexophobic.history import History
    from syntexophobic.job import Job, Operation, Tool
    from syntexophobic.project import Project
    prof = Sketch(plane="XZ")
    o, a, b = prof.point(0, 0), prof.point(20, 0), prof.point(0, 20)
    prof.line(o, a)
    prof.arc(o, a, b)
    axis = prof.line(b, o)
    plinth = Sketch(offset=-5)
    plinth.rectangle(-25, -25, 25, 25)
    dome = Part("Dome", [Feature("revolve", sketch="prof", axis=axis),
                         Feature("extrude", sketch="plinth", distance=5, operation="join")],
                {"prof": prof, "plinth": plinth})
    folder = tmp_path / "wl"
    dome.save(folder)
    jobfile.save(Job(name="wl", model="model.toml", tools=[Tool(1, "6 mm ball", 6.0, 2, "ball")],
                     operations=[Operation(type="finish", name="Waterline", tool=1, stepdown=0.4, leave=0.0,
                                           bottom=0.0)]), folder / jobfile.JOB_FILE)
    History(folder).init()
    p = Project(folder)
    res = simulate.simulate(p.setup(), p.toolpaths(), res=0.1)
    assert res.gouge_cells == 0, f"gouge {res.gouge_depth:.3f} mm"
    xs, ys = res.grid.centres()
    X, Y = np.meshgrid(xs, ys)
    rim = (np.hypot(X, Y) > 15) & (np.hypot(X, Y) < 19.5)        # steep: 49° to 77°
    # material left, measured perpendicular to the surface (on a 77° wall a 0.07 mm skin
    # reads as 0.3 mm in Z): the keep-out is designed to be at most r/40 too wide
    left = -simulate._normal_depth(res.part, res.stock, 0.1)[rim]
    assert np.percentile(left, 95) < 0.1, f"{np.percentile(left, 95):.3f} mm left on the rim"
    assert np.percentile(left, 5) > -0.02


def test_sketch_on_a_slanted_face():
    base = Sketch(plane="YZ")                        # 30 x 20 profile, extruded 40 along X
    base.rectangle(0, 0, 30, 20)
    from syntexophobic.model import pick_token
    part = Part("Wedge", [Feature("extrude", sketch="base", distance=40),
                          Feature("chamfer", radius=10, edges=pick_token((20, 0, 20)))],   # front top edge
                {"base": base})
    plain = volume(part.build())
    assert abs(plain - (30 * 20 - 50) * 40) < 1e-3          # 10 mm chamfer along the top front edge
    # the chamfer face: centre (20, 5, 15), normal (0, -1, 1) out of the material
    on_face = Sketch.on_plane("slant", (20, 5, 15), (0, -1, 1))
    assert on_face.plane == "custom"
    on_face.circle((0, 0), 3)
    part.sketches["slant"] = on_face
    part.features.append(Feature("extrude", sketch="slant", distance=-2, operation="cut"))
    assert abs(volume(part.build()) - (plain - math.pi * 9 * 2)) < 1e-3
    text = on_face.dumps()
    back = Sketch.loads(text)
    assert back.plane == "custom" and back.normal == on_face.normal and back.dumps() == text
    # square planes stay named: a face looking up becomes XY with an offset
    assert Sketch.on_plane("top", (3, 4, 12.5), (0, 0, 1)).plane == "XY"
    assert Sketch.on_plane("top", (3, 4, 12.5), (0, 0, 1)).offset == 12.5


def test_parameters_drive_sketch_and_features(tmp_path):
    from syntexophobic.params import ParameterError, evaluate, resolve
    base = Sketch()
    ls = base.rectangle(0, 0, 50, 30)
    base.constrain("fixed", base.lines[ls[0]].a)
    w = base.constrain("distance", ls[0], value=50)
    next(k for k in base.constraints if k.id == w).expr = "width"
    d = base.constrain("distance", ls[1], value=30)
    next(k for k in base.constraints if k.id == d).expr = "width * 0.6"
    holes = Sketch()
    holes.circle((10, 15), 2)
    part = Part("Box", [Feature("extrude", sketch="base", distance="thick"),
                        Feature("extrude", sketch="holes", distance="thick", operation="cut",
                                count="floor_holes", spacing="width / 4", direction="X")],
                {"base": base, "holes": holes},
                parameters={"width": "60", "thick": "width / 10", "floor_holes": "3"})
    v = volume(part.build())
    assert abs(v - (60 * 36 * 6 - 3 * math.pi * 4 * 6)) < 1e-2
    part.parameters["width"] = "80"                       # one change, everything follows
    assert abs(volume(part.build()) - (80 * 48 * 8 - 3 * math.pi * 4 * 8)) < 1e-2
    part.save(tmp_path)
    back = Part.load(tmp_path / "model.toml")
    assert back.parameters == part.parameters and abs(volume(back.build()) - volume(part.build())) < 1e-6
    assert 'distance = "thick"' in (tmp_path / "model.toml").read_text()
    assert 'expr = "width * 0.6"' in (tmp_path / "sketches" / "base.toml").read_text()

    assert evaluate("2 * (3 + 4) ** 2 - sqrt(16) + 2 * inch", {}) == 2 * 49 - 4 + 50.8
    with pytest.raises(ParameterError, match="loop"):
        resolve({"a": "b + 1", "b": "a * 2"})
    with pytest.raises(ParameterError, match="Unknown"):
        resolve({"a": "nope + 1"})
    for evil in ("__import__('os').system('true')", "().__class__", "open('x')", "[1][0]"):
        with pytest.raises(ParameterError):
            evaluate(evil, {})
    part.parameters["thick"] = "missing * 2"
    with pytest.raises(ModelError, match="Unknown parameter"):
        part.build()


def test_hole_feature_simple_counterbore_countersink():
    base = Sketch()
    base.rectangle(0, 0, 60, 20)
    top = Sketch(offset=10)                       # points on the top face
    for x in (10, 30, 50):
        top.point(x, 10)

    def made(**kw):
        part = Part("Plate", [Feature("extrude", sketch="base", distance=10),
                              Feature("hole", sketch="top", **kw)], {"base": base, "top": top})
        return part.build()

    plain = 60 * 20 * 10
    through = made(diameter=5, depth=0)
    assert abs(volume(through) - (plain - 3 * math.pi * 2.5 ** 2 * 10)) < 1e-2
    assert sorted((h.x, h.diameter) for h in geometry.find_holes(through)) == [(10, 5), (30, 5), (50, 5)]
    blind = made(diameter=5, depth=6)
    assert abs(volume(blind) - (plain - 3 * math.pi * 2.5 ** 2 * 6)) < 1e-2
    cb = made(hole="counterbore", diameter=5, depth=0, head_diameter=9, head_depth=4)
    assert abs(volume(cb) - (plain - 3 * (math.pi * 4.5 ** 2 * 4 + math.pi * 2.5 ** 2 * 6))) < 1e-2
    cs = made(hole="countersink", diameter=5, depth=0, head_diameter=9)
    cone = math.pi * 2 / 3 * (4.5 ** 2 + 4.5 * 2.5 + 2.5 ** 2)        # 90° frustum, 2 mm tall
    assert abs(volume(cs) - (plain - 3 * (cone + math.pi * 2.5 ** 2 * 8))) < 1e-2
    with pytest.raises(ModelError, match="no free points"):
        Part("x", [Feature("extrude", sketch="base", distance=10), Feature("hole", sketch="base")],
             {"base": base}).build()


def test_measure_distance_and_descriptions():
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from syntexophobic.model import _nearest
    base = Sketch()
    base.rectangle(0, 0, 40, 30)
    base.circle((20, 15), 4)
    box = Part("Box", [Feature("extrude", sketch="base", distance=10)], {"base": base}).build()
    faces = geometry.unique(box, TopAbs_FACE)
    left, right = _nearest(faces, (0, 15, 5)), _nearest(faces, (40, 15, 5))
    d, p, q = geometry.distance(left, right)
    assert abs(d - 40) < 1e-9 and abs(q[0] - p[0]) == pytest.approx(40)
    hole_edge = _nearest(geometry.unique(box, TopAbs_EDGE), (20, 19, 10))    # top rim, away from the seam
    assert "Ø8.000" in geometry.describe(hole_edge)
    assert geometry.describe(_nearest(faces, (5, 5, 10))).startswith(f"face {40 * 30 - math.pi * 16:.2f}")
    assert "Ø8.000" in geometry.describe(_nearest(faces, (24, 15, 5)))      # the hole's wall


def test_two_sided_machining(tmp_path):
    """Pocket on top, another on the bottom, a hole drilled from below: both sides cut where
    they should, nothing gouged, the hole goes through, one G-code file per side."""
    from syntexophobic import gcode, job as jobfile, simulate
    from syntexophobic.history import History
    from syntexophobic.job import Job, Operation, Tool
    from syntexophobic.project import Project
    base, top, under, hole = Sketch(), Sketch(offset=15), Sketch(), Sketch()
    base.rectangle(0, 0, 60, 40)
    top.rectangle(30, 10, 50, 30)                      # top pocket, 5 deep (floor Z 10)
    under.rectangle(8, 4, 24, 16)                      # bottom pocket, 4 deep (ceiling Z 4), off-centre in Y
    hole.circle((50, 5), 2.5)
    part = Part("Plate", [Feature("extrude", sketch="base", distance=15),
                          Feature("extrude", sketch="top", distance=-5, operation="cut"),
                          Feature("extrude", sketch="under", distance=4, operation="cut"),
                          Feature("extrude", sketch="hole", distance=15, operation="cut")],
                {"base": base, "top": top, "under": under, "hole": hole})
    folder = tmp_path / "two"
    part.save(folder)
    # stock 62 x 42 x 16 (1 mm on top), flipped about its middle: Z' = 16 - Z
    jobfile.save(Job(name="two", model="model.toml",
                     tools=[Tool(1, "6 mm", 6.0), Tool(2, "5 mm drill", 5.0)],
                     operations=[Operation(type="rough", name="Top pocket", tool=1, bottom=10.0),
                                 Operation(type="rough", name="Bottom pocket", tool=1, bottom=12.0, side="bottom"),
                                 Operation(type="drill", name="Hole", tool=2, stepdown=4, side="bottom")]),
                 folder / jobfile.JOB_FILE)
    History(folder).init()
    p = Project(folder)
    tps = p.toolpaths()
    assert [t.side for t in tps] == ["top", "bottom", "bottom"]
    res = simulate.simulate(p.setup(), tps, res=0.2)
    assert res.gouge_cells == 0 and res.rapid_hits == [], (res.gouge_depth, res.rapid_hits[:2])
    xs, ys = res.grid.centres()
    at = lambda m, x, y: m[int(round((y - ys[0]) / 0.2)), int(round((x - xs[0]) / 0.2))]   # noqa: E731
    assert abs(at(res.stock, 40, 20) - 10.0) < 1e-6           # top pocket floor
    assert abs(at(res.bottom, 16, 10) - 4.0) < 1e-6           # bottom pocket ceiling
    assert at(res.bottom, 16, 30) <= 1e-6                      # its mirror image is untouched
    assert at(res.bottom, 40, 20) <= 1e-6 and abs(at(res.stock, 16, 20) - 15) < 1e-6   # sides untouched there
    assert at(res.stock, 50, 5) <= p.setup().stock.zmin + 1e-6   # the hole goes through
    top_code, bottom_code = p.gcode(tps, "top"), p.gcode(tps, "bottom")
    assert "(Top pocket:" in top_code and "Bottom pocket" not in top_code
    assert "(Side: bottom." in bottom_code and "(Hole:" in bottom_code and "(Top pocket:" not in bottom_code
    back = gcode.read(bottom_code, p.job, p.setup().stock, side="bottom")
    again = simulate.simulate(p.setup(), [tps[0]] + back, res=0.2)
    assert again.gouge_cells == 0 and abs(at(again.bottom, 16, 10) - 4.0) < 1e-6
