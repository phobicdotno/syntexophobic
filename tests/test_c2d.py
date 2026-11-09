import json
import math
import sqlite3
import zlib

import pytest

from syntexophobic import c2d, dxf

K = 0.5522847498307936     # Carbide Create's bezier circle constant


def write_c2d(path, elements):
    """A minimal .c2d: Carbide Create's items table with zlib-compressed JSON elements."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, uuid TEXT, name TEXT, type TEXT,"
               " version TEXT, sz INTEGER, data BLOB)")
    db.execute("CREATE TABLE params (key TEXT, value TEXT)")
    for el in elements:
        raw = json.dumps(el).encode()
        db.execute("INSERT INTO items (uuid, name, type, version, sz, data) VALUES (?, '', 'element', 'J1', ?, ?)",
                   (el.get("id", ""), len(raw), zlib.compress(raw)))
    db.execute("INSERT INTO items (uuid, name, type, version, sz, data) VALUES ('tp', 'x', 'toolpath', 'J1', 2, ?)",
               (zlib.compress(b"{}"),))
    db.commit()
    db.close()
    return path


def rect(cx, cy, w, h, **extra):
    """Shapes store points relative to `position`, closed by a type-4 point."""
    x, y = w / 2, h / 2
    return {"geometryType": "rectangle", "position": [cx, cy], "center": [cx, cy],
            "points": [[-x, -y], [x, -y], [x, y], [-x, y], [-x, -y], [0, 0]],
            "cp1": [[0, 0]] * 6, "cp2": [[0, 0]] * 6, "point_type": [0, 1, 1, 1, 1, 4], **extra}


def bezier_circle(cx, cy, r):
    """A circle as a path of four cubics, as Carbide Create writes ellipses and imported curves."""
    return {"geometryType": "path", "position": [cx, cy],
            "points": [[-r, 0], [0, r], [r, 0], [0, -r], [-r, 0], [0, 0]],
            "cp1": [[0, 0], [-r, K * r], [K * r, r], [r, -K * r], [-K * r, -r], [0, 0]],
            "cp2": [[0, 0], [-K * r, r], [r, K * r], [K * r, -r], [-r, -K * r], [0, 0]],
            "point_type": [0, 3, 3, 3, 3, 4]}


def test_shapes_paths_and_text(tmp_path):
    f = write_c2d(tmp_path / "plate.c2d", [
        rect(60, 40, 100, 50),
        {"geometryType": "circle", "position": [20, 20], "center": [20, 20], "radius": 5,
         "points": [], "point_type": []},
        bezier_circle(150, 100, 10),
        # open path: a line, then a curve, absolute coordinates
        {"geometryType": "path", "position": [0, 0], "points": [[0, 100], [30, 100], [60, 100]],
         "cp1": [[0, 0], [0, 0], [40, 130]], "cp2": [[0, 0], [0, 0], [50, 130]], "point_type": [0, 1, 3]},
        {"geometryType": "path", "position": [0, 0], "points": [[0, 0], [200, 200]],
         "cp1": [[0, 0]] * 2, "cp2": [[0, 0]] * 2, "point_type": [0, 1], "construction": True},
        {"geometryType": "text", "text": "I", "transform": [1, 0, 0, 0, 1, 0, 100, 5, 1],
         "rendered": [[[0, 0], [2, 0], [2, 10], [0, 10]]]},
    ])
    sk = c2d.import_c2d(f, name="plate")

    assert len(sk.circles) == 1
    (circle,) = sk.circles.values()
    assert circle.r == pytest.approx(5)
    centre = sk.points[circle.center]
    assert (centre.x, centre.y) == pytest.approx((20, 20))

    real = [l for l in sk.lines.values() if not l.construction]
    assert len(real) == 4 + 1 + 4           # rectangle, the open path's line, the text's glyph
    assert sum(l.construction for l in sk.lines.values()) == 1

    closed = [s for s in sk.splines.values() if s.closed]
    open_ = [s for s in sk.splines.values() if not s.closed]
    assert len(closed) == 1 and len(open_) == 1
    ring = [sk.points[p] for p in closed[0].points]
    assert all(math.dist((p.x, p.y), (150, 100)) == pytest.approx(10, abs=0.02) for p in ring)
    curve = [sk.points[p] for p in open_[0].points]
    assert (curve[0].x, curve[0].y) == pytest.approx((30, 100))      # joined to the line's end
    assert (curve[-1].x, curve[-1].y) == pytest.approx((60, 100))
    # bezier peak 0.75 * 30 above the chord; the samples land within a few hundredths of it
    assert max(p.y for p in curve) == pytest.approx(122.5, abs=0.05)

    xs = sorted({round(sk.points[p].x, 6) for l in real for p in (l.a, l.b)})
    assert xs == [0, 10, 30, 100, 102, 110]   # path line, rectangle sides, glyph sides

    # the rectangle, the circle, the bezier ring and the glyph are closed profiles
    assert len(sk.profiles()) == 4


def test_import_drawing_picks_by_extension(tmp_path):
    f = write_c2d(tmp_path / "Part.C2D", [rect(10, 10, 20, 20)])
    sk = dxf.import_drawing(f, name="part")
    assert sk.name == "part" and len(sk.lines) == 4


def test_not_a_c2d(tmp_path):
    bad = tmp_path / "bad.c2d"
    bad.write_bytes(b"this is no sqlite file at all" * 10)
    with pytest.raises(c2d.FormatError):
        c2d.import_c2d(bad)


def test_damaged_element_is_skipped(tmp_path):
    f = write_c2d(tmp_path / "x.c2d", [rect(10, 10, 20, 20)])
    db = sqlite3.connect(f)
    db.execute("INSERT INTO items (uuid, name, type, version, sz, data) VALUES ('d', '', 'element', 'J1', 9, ?)",
               (b"not zlib",))
    db.commit()
    db.close()
    assert len(c2d.import_c2d(f).lines) == 4
