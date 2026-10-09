"""Projects open on machines where git is unconfigured or missing (a fresh desktop install)."""

import pytest

from syntexophobic import history
from syntexophobic.project import Project


@pytest.fixture
def no_git_identity(tmp_path, monkeypatch):
    """git with no user.name / user.email anywhere, as on a new Ubuntu install."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    for var in ("EMAIL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("socket.getfqdn", lambda *a: "laptop")


def test_new_project_without_git_identity(pocket_part, tmp_path, no_git_identity):
    p = Project.create(tmp_path / "job", pocket_part)
    log = p.history.log()
    assert [v.message for v in log] == ["New project from pocket.step"]
    assert p.shape is not None


def test_new_design_without_git_identity(tmp_path, no_git_identity):
    p = Project.create_design(tmp_path / "design", "part")
    assert [v.message for v in p.history.log()] == ["New design part"]


def test_configured_identity_is_kept(tmp_path, no_git_identity):
    (tmp_path / "no-gitconfig").write_text("[user]\n\tname = Ada\n\temail = ada@example.com\n")
    h = history.History(tmp_path)
    assert h._identity_args() == []


def test_new_project_without_git(pocket_part, tmp_path, monkeypatch):
    monkeypatch.setattr(history, "available", lambda: False)
    p = Project.create(tmp_path / "job", pocket_part)
    assert p.shape is not None
    assert p.history.log() == []
    assert p.history.branches() == ("", [])
    assert not p.history.has_changes()
    assert not (tmp_path / "job" / ".git").exists()
