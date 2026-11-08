from pathlib import Path

from syntexophobic import about

ROOT = Path(__file__).resolve().parent.parent


def test_changelog_summary():
    r = about.summarize("# Changelog\n\n## v1.2.3 (build 7) — 2026-01-02\n\n**Files**\n- a\n\n**Testing**\n- b\n\n"
                        "**Look**\n- c\n\n## v1.2.2 — 2026-01-01\n\n- Trim (X): click a piece to remove it\n")
    assert [(x.version, x.build, x.date) for x in r] == [("1.2.3", 7, "2026-01-02"), ("1.2.2", 0, "2026-01-01")]
    assert r[0].areas == ["Files", "Look"]                       # Testing left out
    assert r[1].areas == ["Trim (X)"]                            # no sections: the first item


def test_newest_entry_is_this_version_and_the_packaged_copy_matches():
    releases = about.summarize(about.changelog_text())
    assert releases[0].version == about.version() and releases[0].build == about.build() > 0
    assert (ROOT / "CHANGELOG.md").read_text() == (ROOT / "src/syntexophobic/assets/CHANGELOG.md").read_text(), \
        "copy CHANGELOG.md to src/syntexophobic/assets/ (the installed program reads that copy)"


def test_graphics_line():
    amd = ("OpenGL vendor string:  ATI Technologies Inc.\nOpenGL renderer string:  AMD Radeon(TM) 860M Graphics\n"
           "OpenGL version string:  4.6.0 Compatibility Profile Context 24.10\nOpenGL extensions:\n  GL_ARB_x\n")
    assert about.graphics(amd) == "AMD Radeon(TM) 860M Graphics (OpenGL 4.6.0)"
    soft = about.graphics("OpenGL renderer string:  llvmpipe (LLVM 20.1.2, 256 bits)\n"
                          "OpenGL version string:  4.5 (Core Profile) Mesa 25.2.8\n")
    assert soft.startswith("llvmpipe (LLVM 20.1.2, 256 bits) (OpenGL 4.5)") and "software" in soft
    assert about.graphics("Display ID not set") == ""


def test_about_dialog_opens():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    dlg = about.dialog()
    dlg.show()
    app.processEvents()
    assert dlg.isVisible() and "About Syntexophobic" == dlg.windowTitle()
    dlg.close()
