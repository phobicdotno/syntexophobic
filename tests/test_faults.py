import json

from syntexophobic import faults

REQUEST = "make box with height 50 mm, w: 30 mm d: 24mm"
ANSWER = ("I couldn't make the box. Syntexophobic won't create a new project: new_design failed on all three "
          "folders I tried (~/syntexophobic/box, ~/box, /tmp/box) and returned no error message.")


def box_session(error_text: str = "") -> list[dict]:
    """The chat from the bug report as Claude Code's stream-json: new_design fails three times."""
    calls = [("status", {}, "{\"project\": null}", False),
             ("new_design", {"folder": "/home/karstein/syntexophobic/box", "name": "box"}, error_text, True),
             ("set_parameter", {"name": "width", "expression": "30"}, "No project open", True),
             ("set_parameter", {"name": "depth", "expression": "24"}, "No project open", True),
             ("set_parameter", {"name": "height", "expression": "50"}, "No project open", True),
             ("add_sketch", {"name": "base"}, "No project open", True),
             ("new_design", {"folder": "/home/karstein/box", "name": "box"}, error_text.replace("syntexophobic/", ""), True),
             ("status", {}, "{\"project\": null}", False),
             ("new_design", {"folder": "/tmp/box", "name": "box"}, error_text.replace("/home/karstein/syntexophobic", "/tmp"), True),
             ("screenshot", {}, [{"type": "image", "source": {}}], False)]
    out = [{"type": "system", "subtype": "init", "session_id": "s1"}]
    for i, (name, args, result, err) in enumerate(calls):
        out.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": f"mcp__syntexophobic__{name}", "input": args}]}})
        out.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": err, "content": result}]}})
    out.append({"type": "assistant", "message": {"content": [{"type": "text", "text": ANSWER}]}})
    out.append({"type": "result", "is_error": False, "session_id": "s1"})
    return out


def turn_of(events, request=REQUEST) -> faults.Turn:
    t = faults.Turn(request)
    for ev in events:
        t.feed_line(json.dumps(ev))
    return t


class FakeGitHub(faults.GitHub):
    def __init__(self):
        self.tok, self.use_gh, self.issues, self.comments, self.uploads = "x", False, [], [], []

    def call(self, method, path, data=None):
        if method == "GET" and path.startswith("/issues?"):
            return [i for i in self.issues if i["state"] == "open"]
        if method == "POST" and path == "/issues":
            n = len(self.issues) + 1
            self.issues.append({**data, "number": n, "state": "open",
                                "html_url": f"https://github.com/{faults.REPO}/issues/{n}"})
            return self.issues[-1]
        if method == "POST" and path.endswith("/comments"):
            self.comments.append((int(path.split("/")[2]), data["body"]))
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def upload(self, png, name):
        self.uploads.append(name)
        return f"https://example/{name}"


def test_the_box_chat_is_a_fault_with_the_whole_story():
    t = turn_of(box_session())
    assert t.fault().startswith("add_sketch, new_design, set_parameter failed")
    env = {"Version": "0.6.0 (build 7)", "OS": "Ubuntu 24.04, x86_64"}
    b = faults.body(t, faults.signature(t), env=env, logs="12:00 WARNING mcp new_design failed",
                    screenshot_url="https://example/shot.png", project="/tmp/box")
    assert "| Version | 0.6.0 (build 7) |" in b and "Ubuntu 24.04" in b
    assert REQUEST in b and "I couldn't make the box" in b
    assert '`new_design({"folder": "/home/karstein/syntexophobic/box", "name": "box"})` → **error**' in b
    assert b.count("new_design(") == 3 and "`screenshot({})` → **ok**" in b
    assert "![screenshot](https://example/shot.png)" in b and "mcp new_design failed" in b
    assert faults.MARK.format(faults.signature(t)) in b
    assert "[fault]" in faults.title(t) and REQUEST in faults.title(t)


def test_the_same_fault_in_other_folders_has_the_same_signature():
    a = turn_of(box_session("FileExistsError: /home/karstein/syntexophobic/box/job.toml exists"))
    b = turn_of(box_session("FileExistsError: /tmp/box/job.toml exists"), request="a 10 mm cube")
    other = turn_of(box_session("PermissionError: /tmp/box"))
    assert faults.signature(a) == faults.signature(b) != faults.signature(other)
    ann = turn_of(box_session(r"FileExistsError: C:\Users\ann\box\job.toml exists"))
    bob = turn_of(box_session(r"FileExistsError: C:\Users\bob\b\job.toml exists"))
    assert faults.signature(ann) == faults.signature(bob) == faults.signature(a)


def test_report_files_once_then_adds_to_the_open_issue(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNTEXOPHOBIC_NO_FAULT_REPORTS", raising=False)
    monkeypatch.setattr(faults, "environment", lambda: {"Version": "0.6.0"})
    gh = FakeGitHub()
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")
    first = faults.report(turn_of(box_session()), shot, gh=gh)
    assert first.new and first.number == 1 and gh.issues[0]["labels"] == [faults.LABEL]
    assert gh.uploads and "https://example/" in gh.issues[0]["body"]
    again = faults.report(turn_of(box_session()), gh=gh)
    assert not again.new and again.number == 1 and len(gh.issues) == 1
    assert gh.comments[0][0] == 1 and gh.comments[0][1].startswith("**Happened again.**")
    gh.issues[0]["state"] = "closed"                     # fixed and closed: a new one next time
    assert faults.report(turn_of(box_session()), gh=gh).number == 2


def test_what_is_not_a_fault():
    ok = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "status", "input": {}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "{}"}]}},
          {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done: the box is 30 × 24 × 50 mm."}]}}]
    assert turn_of(ok).fault() == ""
    assert faults.report(turn_of(ok), gh=FakeGitHub()) is None
    gave_up = ok[:2] + [{"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I couldn't create the pocket: the sketch has no closed profile."}]}}]
    assert turn_of(gave_up).fault() == "Claude could not do what was asked"
    asked = ok[:2] + [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "r",
        "name": "mcp__syntexophobic__report_problem", "input": {"summary": "status says no project, panel says STEP"}}]}}]
    assert turn_of(asked).fault() == "Claude reported a problem: status says no project, panel says STEP"


def test_reports_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SYNTEXOPHOBIC_NO_FAULT_REPORTS", "1")
    assert faults.report(turn_of(box_session()), gh=FakeGitHub()) is None


def test_no_github_login_means_no_report(monkeypatch):
    monkeypatch.setattr(faults, "token", lambda: "")
    monkeypatch.setattr(faults.shutil, "which", lambda name: None)
    try:
        faults.GitHub()
    except faults.FaultError:
        pass
    else:
        raise AssertionError("expected FaultError")


def test_panel_reports_a_failed_turn_and_links_the_issue(monkeypatch, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from syntexophobic import assistant_ui
    from syntexophobic.faults import Filed
    monkeypatch.delenv("SYNTEXOPHOBIC_NO_FAULT_REPORTS", raising=False)

    class Window:
        project = None

        def screenshot(self, path):
            (tmp_path / "s.png").write_bytes(b"png")
            return str(tmp_path / "s.png")

    seen = []
    monkeypatch.setattr(faults, "report", lambda turn, shot, project: seen.append((turn, shot)) or
                        Filed(f"https://github.com/{faults.REPO}/issues/9", 9, True))
    panel = assistant_ui.AssistantPanel(Window())
    panel.turn = faults.Turn(REQUEST)
    for ev in box_session():
        panel.turn.feed_line(json.dumps(ev))
        for kind, text in assistant_ui.parse(json.dumps(ev)):
            if kind not in ("session", "done"):
                panel._add(kind, text)
    panel.report(panel.turn)
    panel.pool.waitForDone(5000)
    for _ in range(20):
        app.processEvents()
    assert seen and seen[0][0].request == REQUEST and seen[0][1].name == "s.png"
    html = panel.log.toHtml()
    assert "issues/9" in html and "Reported this to GitHub" in html and "(no error message)" in panel.log.toPlainText()
