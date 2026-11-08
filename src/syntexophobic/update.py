"""Update check against the GitHub repository's releases: is there a version newer than
this one, what changed in it (the release notes are the CHANGELOG section of that
version), and, when this is the AppImage, download the new one and point the
~/Applications link at it; when this is the macOS app, download the new app and put it
in the running one's place; when this is the installed Windows build, download the new
installer and run it once the program has closed.

The repository is private, so the check needs a GitHub login: SYNTEXOPHOBIC_GITHUB_TOKEN,
GH_TOKEN or GITHUB_TOKEN, else `gh auth token`, else the token git already stores for
github.com (`git credential fill`, never prompting). Without one, or offline, the check
finds nothing and says nothing."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO = "phobicdotno/syntexophobic"
API = f"https://api.github.com/repos/{REPO}"
LINK = Path.home() / "Applications" / "Syntexophobic.AppImage"   # what the menu entry starts
# where the macOS updater keeps the app it replaced, so it can be put back
PREVIOUS = Path.home() / "Library" / "Caches" / "syntexophobic" / "previous"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    name: str
    notes: str                 # markdown: the CHANGELOG section of this version
    page: str                  # the release on github.com
    asset: str = ""            # file name of this platform's build: the AppImage, or the zipped macOS app
    asset_url: str = ""        # API url of that file (needs the token, as the repo is private)
    asset_size: int = 0
    blocks_url: str = ""       # API url of <AppImage>.blocks, for downloading only what changed


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

def _request(url: str, tok: str, accept: str = "application/vnd.github+json", **headers):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "syntexophobic",
                                               "X-GitHub-Api-Version": "2022-11-28", **headers})
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


def asset_suffix(system: str = "", machine: str = "") -> str:
    """How this platform's build ends: Syntexophobic-0.6.3-x86_64.AppImage on Linux,
    Syntexophobic-0.6.3-macOS-arm64.zip on a Mac (the app, zipped with ditto),
    Syntexophobic-0.7.2-windows-x64.msi on Windows (the installer; the release also has the
    same build zipped, for unpacking by hand)."""
    system = system or sys.platform
    if system == "darwin":
        return f"-macOS-{machine or platform.machine()}.zip"
    if system == "win32":
        return "-windows-x64.msi"
    return ".AppImage"


def releases(data: list[dict], suffix: str | None = None) -> list[Release]:
    """Published releases with a version tag, newest first; the asset is this platform's build."""
    suffix = asset_suffix() if suffix is None else suffix
    out = []
    for d in data:
        if d.get("draft") or d.get("prerelease") or parse_version(d.get("tag_name", "")) is None:
            continue
        assets = d.get("assets", [])
        app = next((a for a in assets if a.get("name", "").endswith(suffix)), {})
        blocks = next((a for a in assets if app and a.get("name") == app["name"] + ".blocks"), {})
        out.append(Release(d["tag_name"].lstrip("v"), d["tag_name"], d.get("name") or d["tag_name"],
                           d.get("body") or "", d.get("html_url", ""),
                           app.get("name", ""), app.get("url", ""), app.get("size", 0), blocks.get("url", "")))
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


def running_app() -> Path | None:
    """The macOS app bundle this program runs from (its launcher sets $SYNTEXOPHOBIC_APP)."""
    p = os.environ.get("SYNTEXOPHOBIC_APP")
    return Path(p) if p and p.endswith(".app") and Path(p).is_dir() else None


def running_windows_build() -> Path | None:
    """The Windows build's folder (Syntexophobic.exe sets $SYNTEXOPHOBIC_WINDOWS_APP). It is
    not updated in place: Windows will not replace the files of a running program."""
    p = os.environ.get("SYNTEXOPHOBIC_WINDOWS_APP")
    return Path(p) if p and Path(p).is_dir() else None


def _installed_folder() -> str:
    """Where the Windows installer put Syntexophobic (it writes this registry value)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Syntexophobic") as key:
            return str(winreg.QueryValueEx(key, "InstallFolder")[0])
    except (ImportError, OSError):
        return ""


def installed_windows_build(installed: Callable[[], str] | None = None) -> Path | None:
    """The Windows build's folder when the installer put it there (not unpacked from the zip):
    only that copy is updated by running the new installer."""
    folder = running_windows_build()
    where = (installed or _installed_folder)()
    if folder is None or not where:
        return None
    same = os.path.normcase(os.path.normpath(where)) == os.path.normcase(os.path.normpath(str(folder)))
    return folder if same else None


def running_build() -> Path | None:
    """What an update replaces: the AppImage, the macOS app or the installed Windows build's
    folder; None when run from source or from the unpacked Windows zip."""
    return running_appimage() or running_app() or installed_windows_build()


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


def download_range(url: str, tok: str, start: int, end: int) -> bytes:
    """Bytes [start, end) of a release asset."""
    try:
        with _request(url, tok, "application/octet-stream", Range=f"bytes={start}-{end - 1}") as r:
            data = r.read()
            if r.status != 206 or len(data) != end - start:
                raise UpdateError(f"GitHub sent {len(data)} bytes, not the {end - start} asked for")
            return data
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"download failed ({getattr(e, 'reason', e)})") from e


def patch(release: Release, current: Path, dest: Path, tok: str,
          progress: Callable[[float, str], None] | None = None,
          get: Callable[[str, str, int, int], bytes] = download_range,
          read: Callable[[str, str], bytes] | None = None) -> int:
    """Builds the new AppImage at dest from the blocks the running one already has and
    downloads only the rest (see delta.py). Returns the bytes downloaded. Raises UpdateError
    (or ValueError) when that does not work out; the caller then downloads the whole file."""
    from . import delta
    if not release.blocks_url:
        raise UpdateError("the release has no block index")
    say = progress or (lambda f, what: None)
    raw = (read or _read)(release.blocks_url, tok)
    idx = delta.Index.from_bytes(raw)
    if release.asset_size and idx.size != release.asset_size:
        raise UpdateError("the block index is for another file")
    found = delta.find(current, idx, lambda f: say(f, "Comparing"))
    spans = delta.ranges(found, idx.block, idx.size)
    need = sum(b - a for a, b in spans)
    if need > idx.size * 0.4 or len(spans) > 400:
        raise UpdateError("too much has changed for a partial download")
    parts, got = {}, 0
    for a, b in spans:
        parts[a] = get(release.asset_url, tok, a, b)
        got += b - a
        say(got / max(need, 1), "Downloading")
    part = dest.with_name(dest.name + ".part")
    try:
        delta.assemble(current, idx, found, parts, part)
        part.chmod(0o755)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return need + len(raw)


def _read(url: str, tok: str) -> bytes:
    try:
        with _request(url, tok, "application/octet-stream") as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"download failed ({getattr(e, 'reason', e)})") from e


@dataclass
class Installed:
    path: Path                 # the new AppImage
    links: list[Path]          # links now pointing at it
    start: Path                # what to run to start the new version
    downloaded: int = 0        # bytes fetched: only the changed blocks when the patch worked


def install(release: Release, current: Path, tok: str | None = None,
            progress: Callable[[float, str], None] | None = None,
            fetch: Callable[..., None] = download, patch: Callable[..., int] | None = patch) -> Installed:
    """Puts the new AppImage next to the running one and moves the links that started the
    running one over to it. The old file stays, so pointing the link back undoes it.
    Downloads only what changed when the release has a block index, else (or when that
    fails) the whole AppImage. For the macOS app, see install_app; for Windows, install_msi."""
    if current.suffix == ".app":
        return install_app(release, current, tok, progress, fetch)
    if release.asset.endswith(".msi"):
        return install_msi(release, current, tok, progress, fetch)
    if not release.asset_url:
        raise UpdateError(f"release {release.tag} has no AppImage")
    tok = token() if tok is None else tok
    dest = current.resolve().parent / release.asset
    links = links_to(current)
    got = 0
    if dest != current.resolve():
        try:
            if patch is None or not release.blocks_url:
                raise UpdateError("no block index")
            got = patch(release, current.resolve(), dest, tok, progress)
        except (UpdateError, ValueError, OSError):
            fetch(release.asset_url, dest, tok, release.asset_size,
                  progress and (lambda f: progress(f, "Downloading")))
            got = release.asset_size
    for link in links:
        repoint(link, dest)
    return Installed(dest, links, links[0] if links else dest, got)


def install_msi(release: Release, current: Path, tok: str | None = None,
                progress: Callable[[float, str], None] | None = None,
                fetch: Callable[..., None] = download, stage: Path | None = None) -> Installed:
    """Windows: downloads the new installer. Restarting runs it (see run_installer): Windows
    will not let it replace the files while this program runs."""
    if not release.asset_url:
        raise UpdateError(f"release {release.tag} has no Windows installer")
    tok = token() if tok is None else tok
    stage = Path(tempfile.gettempdir()) / "syntexophobic-update" if stage is None else stage
    stage.mkdir(parents=True, exist_ok=True)
    msi = stage / release.asset
    fetch(release.asset_url, msi, tok, release.asset_size,
          progress and (lambda f: progress(f, "Downloading")))
    return Installed(msi, [], msi, release.asset_size)


def installer_script(pid: int) -> str:
    """The batch file that updates the Windows build: waits for this program (process pid) to
    end, runs the installer with only its progress bar, then starts Syntexophobic again (the
    old one if the install was cancelled). The paths come in environment variables: cmd.exe
    reads a batch file in the OEM code page, which would garble C:\\Users\\Bjørn."""
    return ("@echo off\r\n"
            ":wait\r\n"
            f'tasklist /fi "PID eq {pid}" /nh 2>nul | find "{pid}" >nul '
            "&& (ping -n 2 127.0.0.1 >nul & goto wait)\r\n"
            "ping -n 2 127.0.0.1 >nul\r\n"          # and Syntexophobic.exe, which waited for it
            'start "" /wait msiexec /i "%SYNTEXOPHOBIC_MSI%" /passive /norestart\r\n'
            'start "" "%SYNTEXOPHOBIC_APP%\\Syntexophobic.exe"\r\n')


def run_installer(msi: Path, folder: Path, popen: Callable[..., object] = subprocess.Popen):
    """Starts the update (installer_script) in the background, once this program has closed."""
    bat = msi.with_suffix(".cmd")
    bat.write_text(installer_script(os.getpid()), encoding="ascii", newline="")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = {**os.environ, "SYNTEXOPHOBIC_MSI": str(msi), "SYNTEXOPHOBIC_APP": str(folder)}
    popen(["cmd.exe", "/c", str(bat)], creationflags=flags, close_fds=True, env=env)


def unzip(archive: Path, dest: Path):
    """Unpacks a zipped app with its symlinks and permissions: ditto on a Mac, else unzip."""
    if shutil.which("ditto"):
        cmd = ["ditto", "-x", "-k", str(archive), str(dest)]
    elif shutil.which("unzip"):
        cmd = ["unzip", "-q", str(archive), "-d", str(dest)]
    else:
        raise UpdateError("neither ditto nor unzip is installed")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise UpdateError(f"could not unpack the download ({r.stderr.strip() or r.returncode})")


def install_app(release: Release, current: Path, tok: str | None = None,
                progress: Callable[[float, str], None] | None = None,
                fetch: Callable[..., None] = download, unpack: Callable[[Path, Path], None] = unzip,
                previous: Path | None = None) -> Installed:
    """macOS: downloads the zipped app, unpacks it next to the running one and swaps the two,
    so the Dock, Launchpad and Spotlight start the new version. The running app keeps
    running from where it moved to: PREVIOUS/Syntexophobic-<old version>.app, which also
    lets it be put back."""
    if not release.asset_url:
        raise UpdateError(f"release {release.tag} has no macOS app")
    current = current.resolve()
    if "/AppTranslocation/" in current.as_posix():
        raise UpdateError("macOS runs this copy from a read-only place: move Syntexophobic.app to "
                          "Applications in Finder, start it from there, then update")
    tok = token() if tok is None else tok
    from .about import version
    previous = PREVIOUS if previous is None else previous
    stage = Path(tempfile.mkdtemp(prefix=".syntexophobic-update-", dir=current.parent))
    try:
        archive = stage / release.asset
        fetch(release.asset_url, archive, tok, release.asset_size,
              progress and (lambda f: progress(f, "Downloading")))
        if progress:
            progress(1.0, "Unpacking")
        unpack(archive, stage / "new")
        new = next((stage / "new").glob("*.app"), None)
        if new is None:
            raise UpdateError(f"{release.asset} holds no app")
        old = stage / current.name
        os.rename(current, old)                       # same folder, so both renames are instant
        try:
            os.rename(new, current)
        except OSError:
            os.rename(old, current)
            raise
        try:                                          # the new app is in place: keeping the old is extra
            previous.mkdir(parents=True, exist_ok=True)
            kept = previous / f"{current.stem}-{version()}.app"
            shutil.rmtree(kept, ignore_errors=True)
            shutil.move(old, kept)
        except OSError as e:
            from .faults import log
            log.warning("could not keep the old app in %s: %s", previous, e)
            if old.exists():                          # it is still running: never delete it
                os.rename(old, current.with_name(f".{current.stem}-{version()}.app"))
    except OSError as e:
        raise UpdateError(f"could not replace {current} ({e})") from e
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return Installed(current, [], current, release.asset_size)
