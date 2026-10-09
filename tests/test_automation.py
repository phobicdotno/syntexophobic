import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from syntexophobic import automation as A


def test_headless_session_designs_machines_and_saves(tmp_path):
    s = A.Session()
    s.new_design(str(tmp_path / "bracket"), "bracket")
    s.set_parameter("width", "70")
    s.add_sketch("base")
    made = s.sketch_draw("base", "rectangle", x0=0, y0=0, x1=60, y1=40)["created"]
    sk = s.get_sketch("base")["toml"]
    assert "[[lines]]" in sk
    first = made[0]
    corner = [l for l in sk.split("[[lines]]") if f'id = "{first}"' in l][0].split('a = "')[1].split('"')[0]
    s.sketch_constrain("base", "fixed", [corner])
    s.sketch_constrain("base", "distance", [made[0]], expr="width")
    r = s.sketch_constrain("base", "distance", [made[1]], value=40)
    assert r["solved"] and r["degrees_of_freedom"] == 0
    s.sketch_draw("base", "circle", cx=15, cy=20, r=3)
    built = s.add_feature("extrude", sketch="base", distance=12)
    assert built["built"] and built["size_mm"] == [70.0, 40.0, 12.0] and len(built["holes"]) == 1
    s.add_feature("fillet", radius=5, edges="|Z")
    s.add_tool(name="6 mm", diameter=6.0)
    job = s.add_operation(type="profile", name="Cut out", tool=1, tabs=3)["job"]
    assert job["operations"][-1]["type"] == "profile"
    s.update_operation(0, bottom=4.0)
    gen = s.generate()
    assert gen["toolpaths"][0]["moves"] > 10
    sim = s.simulate(0.3)
    assert sim["safe"], sim
    files = s.export_gcode()["saved"]
    assert Path(files[0]).read_text().startswith("(Syntexophobic")
    assert s.save_version("Bracket from automation")["saved"]
    assert s.history()["versions"][0]["message"] == "Bracket from automation"
    with pytest.raises(A.CommandError, match="Unknown field"):
        s.add_feature("extrude", sketch="base", depthh=3)
    with pytest.raises(A.CommandError, match="window"):
        s.screenshot()


def test_mcp_server_over_stdio(tmp_path):
    """Claude Code's view: start `syntexophobic mcp`, list tools, call a few."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"))   # no window socket here
    params = StdioServerParameters(command=sys.executable, args=["-m", "syntexophobic", "mcp"], env=env)

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"status", "new_design", "sketch_draw", "add_feature", "simulate", "screenshot"} <= tools
                out = await session.call_tool("new_design", {"folder": str(tmp_path / "p"), "name": "p"})
                assert json.loads(out.content[0].text)["project"].endswith("p")
                await session.call_tool("add_sketch", {"name": "s"})
                out = await session.call_tool("sketch_draw", {"sketch": "s", "shape": "circle",
                                                              "params": {"cx": 0, "cy": 0, "r": 10}})
                assert json.loads(out.content[0].text)["closed_profiles"] == 1
                out = await session.call_tool("add_feature", {"type": "extrude", "fields": {"sketch": "s", "distance": 5}})
                res = json.loads(out.content[0].text)
                assert res["built"] and abs(res["volume_mm3"] - 3.14159265 * 100 * 5) < 0.1
                return tools

    tools = asyncio.run(go())
    assert len(tools) >= 30
