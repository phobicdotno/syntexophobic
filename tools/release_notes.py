"""The GitHub release's title and notes for a version, from CHANGELOG.md (the release
workflow runs it; the update bar's *What's new* shows the notes):

    python3 tools/release_notes.py 0.6.3 title    # Syntexophobic 0.6.3 (build 10)
    python3 tools/release_notes.py 0.6.3 notes    # that version's section, and how to install

Fails when CHANGELOG.md has no section for the version.
"""

import re
import sys
from pathlib import Path

INSTALL = """
**Install**
- Linux (x86-64, X11 or XWayland): download `Syntexophobic-{v}-x86_64.AppImage`,
  `chmod +x` it and run it.
- macOS (Apple silicon, macOS 12 or later): download `Syntexophobic-{v}-macOS-arm64.zip`,
  open it and move *Syntexophobic* to Applications. The app is not signed by Apple, so the
  first start says it cannot check it: open System Settings → Privacy & Security and click
  *Open Anyway* (or run `xattr -dr com.apple.quarantine /Applications/Syntexophobic.app`).
  Updates from the update bar need none of this.
- Windows (x64): download `Syntexophobic-{v}-windows-x64.zip`, unpack it and run
  `Syntexophobic\\Syntexophobic.exe`. If SmartScreen stops it (the program is not signed),
  click *More info* → *Run anyway*.
"""


def section(changelog: str, version: str) -> tuple[str, str]:
    """(heading, body) of '## v<version> (build N) — date'."""
    m = re.search(rf"^## v{re.escape(version)}(?![\w.])([^\n]*)\n(.*?)(?=^## v|\Z)", changelog, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for v{version}")
    return m.group(1), m.group(2)


def main(version: str, what: str):
    head, body = section((Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8"),
                         version)
    if what == "title":
        build = re.search(r"\(build \d+\)", head)
        print(f"Syntexophobic {version}" + (f" {build.group(0)}" if build else ""))
    else:
        print("\n" + body.strip() + "\n" + INSTALL.format(v=version))


if __name__ == "__main__":
    main(*sys.argv[1:3])
