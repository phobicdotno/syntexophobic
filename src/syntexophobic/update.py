"""Update check against the GitHub repository's releases: is there a version newer than
this one, what changed in it (the release notes are the CHANGELOG section of that
version), and, when this is the AppImage, download the new one and point the
~/Applications link at it.

The repository is private, so the check needs a GitHub login: SYNTEXOPHOBIC_GITHUB_TOKEN,
GH_TOKEN or GITHUB_TOKEN, else `gh auth token`, else the token git already stores for
github.com (`git credential fill`, never prompting). Without one, or offline, the check
finds nothing and says nothing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = "phobicdotno/syntexophobic"
API = f"https://api.github.com/repos/{REPO}"
LINK = Path.home() / "Applications" / "Syntexophobic.AppImage"   # what the menu entry starts


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    name: str
    notes: str                 # markdown: the CHANGELOG section of this version
    page: str                  # the release on github.com
    asset: str = ""            # AppImage file name
    asset_url: str = ""        # API url of the AppImage (needs the token, as the repo is private)
    asset_size: int = 0


@dataclass
class Update:
    current: str
    releases: list[Release]    # every release newer than this one, newest first

    @property
    def latest(self) -> Release:
        return self.releases[0]

    def notes(self) -> str:
        """Release notes of every version since this one, newest first."""
        return "\n\n".join(f"## {r.name or r.tag}\n\n{r.notes.strip()}" for r in self.releases)

    def areas(self) -> list[str]:
        """The bold section titles of the new releases (Testing left out), as Help > About."""
        from .about import summarize
        out: list[str] = []
        for r in summarize("\n".join(f"## v{r.version}\n\n{r.notes}" for r in self.releases)):
            out += [a for a in r.areas if a not in out]
        return out


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.match(r"^v?(\d+(?:\.\d+)*)$", text.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def newer(a: str, b: str) -> bool:
    """a is a later version than b (0.10.0 after 0.9.1)."""
    va, vb = parse_version(a), parse_version(b)
    if va is None or vb is None:
        return False
    n = max(len(va), len(vb))
    return va + (0,) * (n - len(va)) > vb + (0,) * (n - len(vb))


# ---- credentials ------------------------------------------------------------------------

def _run(cmd: list[str], stdin: str | None = None) -> str:
    try:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
                   GIT_ASKPASS="false", SSH_ASKPASS="false")
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=8, env=env)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def token() -> str:
    """A GitHub token from the environment, the gh login, or git's stored credentials."""
    for var in ("SYNTEXOPHOBIC_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    if shutil.which("gh"):
        t = _run(["gh", "auth", "token", "--hostname", "github.com"]).strip()
        if t:
            return t
    if shutil.which("git"):
        out = _run(["git", "credential", "fill"], "protocol=https\nhost=github.com\n\n")
        fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if fields.get("password"):
            return fields["password"]
    return ""


# ---- the check --------------------------------------------------------------------------

def _request(url: str, tok: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "syntexophobic",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return urllib.request.urlopen(req, timeout=20)


def fetch_releases(tok: str) -> list[dict]:
    try:
        with _request(f"{API}/releases?per_page=30", tok) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):        # a private repository answers 404 to strangers
            raise UpdateError("no access to the repository (sign in with `gh auth login`)") from e
        raise UpdateError(f"GitHub answered {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"could not reach GitHub ({getattr(e, 'reason', e)})") from e


def releases(data: list[dict]) -> list[Release]:
    """Published releases with a version tag, newest first."""
    out = []
    for d in data:
        if d.get("draft") or d.get("prerelease") or parse_version(d.get("tag_name", "")) is None:
            continue
        app = next((a for a in d.get("assets", []) if a.get("name", "").endswith(".AppImage")), {})
        out.append(Release(d["tag_name"].lstrip("v"), d["tag_name"], d.get("name") or d["tag_name"],
                           d.get("body") or "", d.get("html_url", ""),
                           app.get("name", ""), app.get("url", ""), app.get("size", 0)))
    out.sort(key=lambda r: parse_version(r.version), reverse=True)
    return out


def check(current: str | None = None, fetch: Callable[[str], list[dict]] = fetch_releases,
          tok: str | None = None) -> Update | None:
    """The releases newer than `current` (this program's version), or None when up to date.
    Raises UpdateError when GitHub could not be asked."""
    if current is None:
        from .about import version
        current = version()
    new = [r for r in releases(fetch(token() if tok is None else tok)) if newer(r.version, current)]
    return Update(current, new) if new else None


# ---- installing -------------------------------------------------------------------------

def running_appimage() -> Path | None:
    """The AppImage file this program runs from (its runtime sets $APPIMAGE)."""
    p = os.environ.get("APPIMAGE")
    return Path(p) if p and Path(p).is_file() else None


def how_to_update() -> str:
    """For a program that is not the AppImage: the command that updates it."""
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        return f"git -C {root} pull"
    return "uv tool upgrade syntexophobic"


def links_to(target: Path, extra: list[Path] = ()) -> list[Path]:
    """Symlinks that start `target`: ~/Applications/Syntexophobic.AppImage and any other link
    next to it or in ~/Applications."""
    target = target.resolve()
    seen, out = set(), []
    places = [LINK, *extra]
    for folder in {target.parent, LINK.parent}:
        if folder.is_dir():
            places += sorted(folder.iterdir())
    for p in places:
        if p.is_symlink() and p not in seen and p.resolve() == target:
            seen.add(p)
            out.append(p)
    return out


def repoint(link: Path, target: Path):
    """Points the symlink at target in one step (a new link renamed over the old one)."""
    rel = os.readlink(link)
    dest = target.name if not os.path.isabs(rel) and target.parent == link.parent.resolve() else str(target)
    tmp = link.with_name(link.name + ".new")
    tmp.unlink(missing_ok=True)
    os.symlink(dest, tmp)
    os.replace(tmp, link)


def download(url: str, dest: Path, tok: str, size: int = 0,
             progress: Callable[[float], None] | None = None):
    """Downloads a release asset to dest (via dest.part, made executable, then renamed)."""
    part = dest.with_name(dest.name + ".part")
    try:
        with _request(url, tok, "application/octet-stream") as r, open(part, "wb") as f:
            total = size or int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(min(got / total, 1.0))
        if size and got != size:
            raise UpdateError(f"download stopped after {got} of {size} bytes")
        part.chmod(0o755)
        os.replace(part, dest)
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"download failed ({getattr(e, 'reason', e)})") from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise


@dataclass
class Installed:
    path: Path                 # the new AppImage
    links: list[Path]          # links now pointing at it
    start: Path                # what to run to start the new version


def install(release: Release, current: Path, tok: str | None = None,
            progress: Callable[[float], None] | None = None,
            fetch: Callable[..., None] = download) -> Installed:
    """Puts the new AppImage next to the running one and moves the links that started the
    running one over to it. The old file stays, so pointing the link back undoes it."""
    if not release.asset_url:
        raise UpdateError(f"release {release.tag} has no AppImage")
    dest = current.resolve().parent / release.asset
    links = links_to(current)
    if dest != current.resolve():
        fetch(release.asset_url, dest, token() if tok is None else tok, release.asset_size, progress)
    for link in links:
        repoint(link, dest)
    return Installed(dest, links, links[0] if links else dest)
