"""The .cccp project file and the History panel's subway map."""

import subprocess
import zipfile

import pytest

from syntexophobic import automation, cccp, history
from syntexophobic.history import History, Version, layout
from syntexophobic.project import Project


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(automation, "STATE", tmp_path / "cache")


def _edit(p: Project, name: str):
    p.job.name = name
    p.save_job()


def test_new_design_in_a_cccp_keeps_the_history(tmp_path):
    path = tmp_path / "bracket.cccp"
    p = Project.create_design(path, "bracket")
    assert p.archive == path and path.exists()
    _edit(p, "bracket-2")
    p.history.save("Renamed")                               # every saved version repacks the file
    with zipfile.ZipFile(path) as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert "job.toml" in z.namelist() and ".git/HEAD" in z.namelist()

    cccp.remove(p.folder)                                   # a fresh machine: only the file is left
    q = Project.open(path)
    assert q.archive == path and q.job.name == "bracket-2"
    assert [v.message for v in q.history.log()] == ["Renamed", "New design bracket"]
    assert not q.history.has_changes()


def test_unsaved_edits_travel_in_the_file(tmp_path):
    p = Project.create_design(tmp_path / "a.cccp", "a")
    _edit(p, "draft")
    p.pack()
    q = Project.open(tmp_path / "a.cccp")
    assert q.job.name == "draft" and q.history.has_changes()


def test_variants_travel_in_the_file(tmp_path):
    p = Project.create_design(tmp_path / "v.cccp", "v")
    p.history.new_branch("thin")
    _edit(p, "thin")
    p.history.save("Thinner")
    q = Project.open(tmp_path / "v.cccp")
    assert q.history.branches() == ("thin", ["main", "thin"])


def test_save_folder_project_as_cccp(tmp_path):
    p = Project.create_design(tmp_path / "folder", "f")
    p.keep_in(tmp_path / "out" / "f")                      # the suffix is added
    assert (tmp_path / "out" / "f.cccp").exists()
    assert Project.open(tmp_path / "folder" / "job.toml").folder == tmp_path / "folder"


def test_gcode_is_left_out(tmp_path):
    p = Project.create_design(tmp_path / "g.cccp", "g")
    (p.folder / "g-top.nc").write_text("G0 X0\n")
    p.pack()
    assert "g-top.nc" not in zipfile.ZipFile(tmp_path / "g.cccp").namelist()


def test_not_a_project_file(tmp_path):
    (tmp_path / "x.cccp").write_text("hello")
    with pytest.raises(cccp.FormatError):
        Project.open(tmp_path / "x.cccp")
    with zipfile.ZipFile(tmp_path / "y.cccp", "w") as z:
        z.writestr("job.toml", "")
    with pytest.raises(cccp.FormatError):
        cccp.unpack(tmp_path / "y.cccp")


def test_no_files_outside_the_project(tmp_path):
    with zipfile.ZipFile(tmp_path / "evil.cccp", "w") as z:
        z.writestr("mimetype", cccp.MIMETYPE)
        z.writestr("../../escaped.txt", "x")
    with pytest.raises(cccp.FormatError):
        cccp.unpack(tmp_path / "evil.cccp", tmp_path / "work" / "evil")
    assert not (tmp_path / "escaped.txt").exists()


# ---- subway map ----------------------------------------------------------------------------

def _v(h: str, refs: str = "") -> Version:
    return Version(h, h, "2026-10-14 08:00", f"save {h}", refs)


def test_layout_keeps_the_current_variant_straight():
    # m1 - m2 - m3 (main, current), with "thin" branching off m2
    rows = [(_v("t1", "thin"), ["m2"]), (_v("m3", "HEAD -> main"), ["m2"]), (_v("m2"), ["m1"]), (_v("m1"), [])]
    s = layout(rows, ["main", "thin"], first="m3")
    assert [x.lane for x in s] == [1, 0, 0, 0]
    assert [x.colour for x in s] == [1, 0, 0, 0]
    assert s[0].top == []                                    # nothing above a tip
    assert s[1].top == [(1, 1, 1)]                           # thin passes beside main's tip
    assert (1, 0, 1) in s[1].bottom                          # and bends into main to meet m2
    assert s[3].bottom == []


def test_layout_merge():
    rows = [(_v("m", "HEAD -> main"), ["a", "b"]), (_v("b", "side"), ["a"]), (_v("a"), [])]
    s = layout(rows, ["main", "side"], first="m")
    assert s[0].lane == 0 and sorted(s[0].bottom) == [(0, 0, 0), (0, 1, 2)]
    assert s[1].lane == 1 and s[2].lane == 0


@pytest.mark.skipif(not history.available(), reason="needs git")
def test_changes_names_files_and_lines(tmp_path):
    p = Project.create_design(tmp_path / "c", "c")
    _edit(p, "renamed")
    p.history.save("Rename")
    text = p.history.changes("HEAD")
    assert "job.toml" in text and '+name = "renamed"' in text
    stations = p.history.subway()
    assert [s.version.message for s in stations] == ["Rename", "New design c"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=p.folder, capture_output=True, text=True).stdout == ""


@pytest.mark.skipif(not history.available(), reason="needs git")
def test_overview_matches_the_separate_queries(tmp_path, monkeypatch):
    p = Project.create_design(tmp_path / "o", "o")
    _edit(p, "o2")
    p.history.save("Second")
    p.history.new_branch("thin")
    _edit(p, "o3")
    p.history.save("Thinner")
    calls = []
    real = history.subprocess.run
    monkeypatch.setattr(history.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or real(cmd, **kw))
    o = p.history.overview()
    assert len(calls) == 2                                       # git starts twice, not nine times
    assert (o.current, o.branches) == p.history.branches() == ("thin", ["main", "thin"])
    assert [v.message for v in o.versions] == ["Thinner", "Second", "New design o"]
    assert o.stations[0].version.message == "Thinner" and o.stations[0].lane == 0     # on thin, drawn left-most
    empty = history.History(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    empty.init()
    assert empty.overview().versions == [] and empty.overview().current == "main"


def test_subway_tab(tmp_path):
    from PySide6.QtWidgets import QApplication
    from syntexophobic.app import MainWindow
    app = QApplication.instance()
    win = MainWindow()
    p = Project.create_design(tmp_path / "w.cccp", "w")
    _edit(p, "w2")
    p.history.save("Second")
    win.load(p)
    win.history_tabs.setCurrentIndex(1)
    app.processEvents()
    assert len(win.subway.stations) == 2
    assert "Second" in win.subway.tooltip(0) and "job.toml" in win.subway.tooltip(0)
    win.subway.picked.emit(win.subway.stations[1].version.hash)
    assert win.l_versions.currentRow() == 1
    win.close()


def test_final_product_for_other_programs(pocket_part, tmp_path):
    import json
    p = Project.create(tmp_path / "pocket.cccp", pocket_part)
    with zipfile.ZipFile(tmp_path / "pocket.cccp") as z:
        names = z.namelist()
        info = json.loads(z.read("final/info.json"))
    assert {"final/part.stl", "final/info.json"} <= set(names)
    assert info["files"] == ["part.stl"] and info["size"] == [60, 40, 15] and info["units"] == "mm"
    assert info["version"]["note"] == "New project from pocket.step"
    assert not p.history.has_changes()                        # final/ stays out of the history


def test_final_drawing_has_every_sketch(tmp_path):
    from syntexophobic.sketch import Sketch
    p = Project.create_design(tmp_path / "d.cccp", "d")
    sk = Sketch("base")
    sk.rectangle(0, 0, 30, 20)
    sk.save(p.folder / "sketches" / "base.toml")
    from syntexophobic.model import Part
    part = Part.load(p.folder / "model.toml")
    part.sketches["base"] = sk
    part.save(p.folder)
    p.history.save("Base sketch")
    with zipfile.ZipFile(tmp_path / "d.cccp") as z:
        svg = z.read("final/drawing.svg").decode()
    assert '<g id="base">' in svg and svg.count("<line") == 4


def test_a_file_from_before_1980_still_packs(tmp_path):
    import os
    p = Project.create_design(tmp_path / "old.cccp", "old")
    old = p.folder / "notes.txt"
    old.write_text("from a tar")
    os.utime(old, (0, 0))                                   # 1970
    p.pack()
    assert "notes.txt" in zipfile.ZipFile(tmp_path / "old.cccp").namelist()
    assert not (tmp_path / "old.cccp.part").exists()


def test_final_version_is_the_one_the_files_are_at(tmp_path):
    import json
    p = Project.create_design(tmp_path / "v.cccp", "v")
    first = p.history.log()[0].hash
    _edit(p, "newer")
    p.history.save("Newer")
    subprocess.run(["git", "checkout", "-q", "-b", "older", first], cwd=p.folder, check=True)
    p.reload()
    p.write_final()
    assert json.loads((p.folder / "final" / "info.json").read_text())["version"]["hash"] == first
