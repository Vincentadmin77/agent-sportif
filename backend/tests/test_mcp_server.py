import json
import sys
from pathlib import Path

import anyio
import pytest

from sportai.mcp import engine_server as srv


def test_tools_are_registered():
    names = {t.name for t in anyio.run(srv.server.list_tools)}
    assert {"price_match", "price_live", "bet_value", "remove_margin", "market_probability", "recent_runs", "track_record"} <= names


def test_tool_outputs():
    m = srv.price_match(1.6, 1.0)
    assert abs(m["1x2:home"]["p_win"] + m["1x2:draw"]["p_win"] + m["1x2:away"]["p_win"] - 1) < 1e-3
    v = srv.bet_value(0.70, 1.35)
    assert v["ev"] < 0  # high probability, negative value
    assert abs(sum(srv.remove_margin([1.9, 3.5, 4.2])["probabilities"]) - 1) < 1e-3
    live = srv.price_live(2.0, 0.8, 90 + 5, 0, 1)
    assert live["1x2:away"]["p_win"] == 1.0
    with pytest.raises(ValueError):
        srv.price_match(0, 1)


def test_stdio_roundtrip():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "sportai.mcp.engine_server"],
                                   cwd=str(Path(__file__).resolve().parents[1]))

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("bet_value", {"p_win": 0.55, "odds": 2.0})
                return res

    res = anyio.run(go)
    text = res.content[0].text
    assert json.loads(text)["ev"] == pytest.approx(0.1)
