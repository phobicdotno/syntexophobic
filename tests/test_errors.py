import logging
import sys

import pytest

from syntexophobic import errors, faults
from syntexophobic.automation import CommandError, Session

from test_faults import FakeGitHub


@pytest.fixture(autouse=True)
def reporting_on(monkeypatch):
    monkeypatch.delenv("SYNTEXOPHOBIC_NO_FAULT_REPORTS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(faults, "environment", lambda: {"Version": "0.8.1 (build 14)", "OS": "Ubuntu 24.04, x86_64"})


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def reporter(tmp_path, clock, sent):
    return errors.Reporter(send=lambda inc, repeats: sent.append((inc, repeats)) or
                           faults.Filed(f"https://github.com/{faults.REPO}/issues/1", 1, len(sent) == 1),
                           state=tmp_path / "sent.json", clock=clock, threaded=False)


def a_bug(folder="/home/karstein/box") -> errors.Incident:
    try:
        Session()._need()                                  # raises inside the program
    except CommandError as e:
        inc = errors.from_exception("Unhandled exception", e)
    inc.message += f" ({folder})"
    return inc


def test_an_exception_knows_where_in_the_program_it_was_raised():
    inc = a_bug()
    assert inc.where == "automation:_need" and inc.exc_type == "CommandError"
    assert "Traceback" in inc.detail and "_need" in inc.detail
    b = inc.body(logs="10:00 INFO menu: New design…", repeats=3)
    assert "0.8.1 (build 14)" in b and "No project open" in b and "Traceback" in b
    assert "Happened 3 more times since the last report" in b and "menu: New design" in b
    assert faults.MARK.format(inc.signature()) in b
    assert inc.title().startswith("[error] Unhandled exception: CommandError: No project open")


def test_repeats_are_sent_once_per_run_then_once_a_day_with_the_count(tmp_path):
    clock, sent = Clock(), []
    r = reporter(tmp_path, clock, sent)
    assert r.capture(a_bug("/home/karstein/box"))
    assert not r.capture(a_bug("/tmp/box"))               # same signature, other folder: counted only
    assert len(sent) == 1 and sent[0][1] == 0
    r = reporter(tmp_path, clock, sent)                    # next run, same day: counted only
    clock.t += 3600
    assert not r.capture(a_bug())
    clock.t += errors.QUIET                                # a day later: sent with what it missed
    r = reporter(tmp_path, clock, sent)
    assert r.capture(a_bug())
    assert len(sent) == 2 and sent[1][1] == 2


def test_different_errors_each_go_out_up_to_the_limit(tmp_path):
    sent = []
    r = reporter(tmp_path, Clock(), sent)
    for i in range(errors.MAX_PER_RUN + 5):
        r.capture(errors.Incident("Logged error", "boom", where=f"model:step{i}"))
    assert len(sent) == errors.MAX_PER_RUN


def test_nothing_is_sent_when_turned_off(tmp_path, monkeypatch):
    sent = []
    on = [False]
    r = reporter(tmp_path, Clock(), sent)
    r.enabled = lambda: on[0]
    assert not r.capture(a_bug())
    on[0] = True
    monkeypatch.setenv("CI", "true")                       # never from a CI run
    assert not r.capture(a_bug())
    monkeypatch.delenv("CI")
    assert r.capture(a_bug()) and len(sent) == 1


def test_a_failed_send_is_tried_again_next_run(tmp_path):
    clock, calls = Clock(), []

    def offline(inc, repeats):
        calls.append(inc)
        raise faults.FaultError("GitHub unreachable")
    r = errors.Reporter(send=offline, state=tmp_path / "sent.json", clock=clock, threaded=False)
    r.capture(a_bug())
    sent = []
    assert reporter(tmp_path, clock, sent).capture(a_bug()) and len(sent) == 1


def test_github_gets_one_issue_and_then_comments():
    gh = FakeGitHub()
    first = errors.send_to_github(a_bug(), 0, gh)
    again = errors.send_to_github(a_bug("/tmp/other"), 4, gh)
    assert first.new and not again.new and len(gh.issues) == 1
    assert gh.issues[0]["labels"] == [faults.LABEL, errors.LABEL]
    assert "Happened 4 more times" in gh.comments[0][1]


def test_logged_errors_are_incidents_and_warnings_are_not(tmp_path):
    sent = []
    r = reporter(tmp_path, Clock(), sent)
    h = errors._LogHandler(r)
    log = logging.getLogger("syntexophobic.test-errors")
    log.addHandler(h)
    try:
        log.warning("only a warning")
        log.error("reporting itself failed", extra={"no_report": True})
        try:
            {}["model"]
        except KeyError:
            log.exception("could not load the part")
    finally:
        log.removeHandler(h)
    assert len(sent) == 1
    inc = sent[0][0]
    assert inc.kind == "Logged error" and inc.exc_type == "KeyError"
    assert inc.message.startswith("could not load the part")


def test_error_dialogs_are_reported(tmp_path, monkeypatch):
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a) or 0))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a) or 0))
    sent = []
    try:
        errors._install_qt(reporter(tmp_path, Clock(), sent))
        QMessageBox.critical(None, "Could not open", "No job.toml in /home/karstein/box")
    finally:
        qInstallMessageHandler(None)
    assert shown and len(sent) == 1
    inc = sent[0][0]
    assert inc.kind == "Error dialog “Could not open”" and "No job.toml" in inc.message
    assert inc.where.startswith("test_errors:")


def test_a_crash_is_reported_at_the_next_start(tmp_path):
    import faulthandler
    log = tmp_path / "crash.log"
    log.write_text('Fatal Python error: Segmentation fault\n\nCurrent thread 0x1 (most recent call first):\n'
                   '  File "/x/syntexophobic/viewer.py", line 88 in render\n  File "/x/app.py", line 9 in run\n')
    sent = []
    f = errors.watch_crashes(reporter(tmp_path, Clock(), sent), log)
    try:
        assert len(sent) == 1 and sent[0][0].message == "Fatal Python error: Segmentation fault"
        assert sent[0][0].where == "viewer.py:render" and "viewer.py" in sent[0][0].detail
        assert log.read_text() == ""                       # ready for this run
        sent.clear()
        errors.watch_crashes(reporter(tmp_path, Clock(), sent), log).close()
        assert not sent                                    # a clean run reports nothing
    finally:
        faulthandler.enable(sys.__stderr__)               # back where pytest had it
        f.close()
