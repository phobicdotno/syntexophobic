"""The user's tool library, shared by every project: ~/.config/syntexophobic/tools.toml.

Created with a few common router bits the first time it is used. Tools are copied into a
job (with the job's next free number), so editing a job never changes the library.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, replace
from pathlib import Path

from .job import Tool, _value

STARTER = [
    Tool(0, '1/4" flat endmill, 2 flute', 6.35, 2, "flat"),
    Tool(0, '1/8" flat endmill, 2 flute', 3.175, 2, "flat"),
    Tool(0, '1/16" flat endmill, 2 flute', 1.5875, 2, "flat"),
    Tool(0, '1/8" ball endmill, 2 flute', 3.175, 2, "ball"),
    Tool(0, "6 mm flat endmill, 2 flute", 6.0, 2, "flat"),
    Tool(0, "3 mm flat endmill, 2 flute", 3.0, 2, "flat"),
    Tool(0, "3 mm ball endmill, 2 flute", 3.0, 2, "ball"),
    Tool(0, "5 mm drill", 5.0, 2, "flat"),
]


def path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "syntexophobic" / "tools.toml"


def load() -> list[Tool]:
    p = path()
    if not p.exists():
        save(STARTER)
    data = tomllib.loads(p.read_text())
    known = set(asdict(Tool()).keys())
    return [Tool(**{k: v for k, v in t.items() if k in known}) for t in data.get("tools", [])]


def save(tools: list[Tool]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = ["# Syntexophobic tool library: copied into jobs with File > Tools from library", ""]
    for t in tools:
        out.append("[[tools]]")
        out += [f"{k} = {_value(v)}" for k, v in asdict(t).items() if k != "id"]
        out.append("")
    p.write_text("\n".join(out))


def add(tool: Tool) -> None:
    tools = load()
    tools.append(replace(tool, id=0))
    save(tools)


def into_job(job, tool: Tool) -> Tool:
    """A copy of a library tool with the job's next free tool number."""
    new = replace(tool, id=max([t.id for t in job.tools] or [0]) + 1)
    job.tools.append(new)
    return new
