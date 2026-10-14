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
