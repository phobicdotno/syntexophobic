"""The .cccp project file: one file holding a whole project folder, git history included.

A .cccp is a zip archive (like .docx or .odt). Its first entry is `mimetype`, stored
uncompressed, so `file` and other tools can tell it apart from any zip; after that comes
every file of the project folder: job.toml, model.toml, sketches/, imports/ and the
`.git` folder with every saved version and variant. Unzip it and you have an ordinary
project folder that `git log` reads.

`final/` holds the finished part for programs that only want the result (phobiCCCP opens
a .cccp this way): `part.stl` (the part as it will be machined, mm, Z up),
`drawing.svg` (every sketch, one group each, mm; its bottom-left corner is 10 mm left of and
below the part, so placing the STL at (10, 10) on the page lines the two up) and `info.json`
(name, size, the version it was made from). It is rewritten on every saved version and kept out of the history.

The app works on an unpacked copy (in the cache folder) and packs it back into the .cccp
on every saved version and when the project is closed, so the .cccp always carries the
full history plus whatever was not saved as a version yet.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path

SUFFIX = ".cccp"
FINAL = "final"                 # the finished part, for programs that only want the result
FINAL_MARGIN = 10.0             # mm from drawing.svg's bottom-left corner to the part's
MIMETYPE = "application/x-syntexophobic-project"
# generated output, never part of a project file
SKIP = {".nc", ".gcode", ".tap"}


class FormatError(ValueError):
    pass


def is_cccp(path: str | Path) -> bool:
    return Path(path).suffix.lower() == SUFFIX


def work_folder(path: str | Path) -> Path:
    """Where the project in `path` is unpacked: one folder per .cccp, in the cache."""
    from .automation import STATE
    path = Path(path).expanduser().resolve()
    tag = hashlib.sha1(str(path).encode()).hexdigest()[:10]
    return STATE / "open" / f"{path.stem}-{tag}"


def pack(folder: str | Path, path: str | Path) -> Path:
    """Write the project folder into the .cccp at `path` (replacing it in one step)."""
    folder, path = Path(folder), Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            rel_root = Path(root).relative_to(folder)
            if rel_root != Path("."):
                z.writestr(rel_root.as_posix() + "/", "")      # empty folders matter to git
            for name in sorted(files):
                p = Path(root) / name
                if p.suffix.lower() in SKIP or p.is_symlink():
                    continue
                z.write(p, (rel_root / name).as_posix())
    os.replace(tmp, path)
    return path


def unpack(path: str | Path, folder: str | Path | None = None) -> Path:
    """Unpack a .cccp into `folder` (its work folder by default), replacing what is there."""
    path = Path(path).expanduser()
    folder = Path(folder) if folder else work_folder(path)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise FormatError(f"{path.name} is not a Syntexophobic project file") from e
    with z:
        names = z.namelist()
        if not names or names[0] != "mimetype" or z.read("mimetype").decode(errors="replace") != MIMETYPE:
            raise FormatError(f"{path.name} is not a Syntexophobic project file")
        root = folder.resolve()
        for n in names:                                   # no ../ or absolute paths
            if not (root / n).resolve().is_relative_to(root):
                raise FormatError(f"{path.name} has a file outside the project: {n}")
        remove(folder)
        folder.mkdir(parents=True)
        z.extractall(folder, [n for n in names if n != "mimetype"])
    return folder


def remove(folder: str | Path) -> None:
    """Delete a folder, including git's read-only object files (which Windows refuses to delete)."""
    def writable(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if Path(folder).exists():
        shutil.rmtree(folder, onexc=writable)
