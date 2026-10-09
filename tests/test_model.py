import math

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
