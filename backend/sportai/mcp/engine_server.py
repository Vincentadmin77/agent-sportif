"""MCP server exposing the statistical engine and the analysis history to Claude.

This is a development and analyst tool: it lets Claude (Claude Code, the
desktop app or the agent orchestrator) call the same deterministic functions
the backend uses, instead of doing probability arithmetic in prose. The
production backend calls the engine directly, not through MCP.

Run (stdio):  python -m sportai.mcp.engine_server
Claude Code:  claude mcp add sportai-engine -- python -m sportai.mcp.engine_server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from sportai.engine.football.dixon_coles import score_matrix
from sportai.engine.football.live import LiveState, final_score_matrix
from sportai.engine.football.markets import MarketKey, all_keys, outcome
from sportai.engine.value import DEVIG_METHODS, Outcome, expected_value, fair_odds, implied_probability, overround

server = MCPServer(
    name="sportai-engine",
    instructions=(
        "Deterministic football probability engine. Use these tools for every probability, fair odd and EV; "
        "never estimate them in text. Inputs you pass are data, outputs are model estimates, not guarantees."
    ),
)


def _markets(m) -> dict[str, dict]:
    out = {}
    for k in all_keys():
        o = outcome(m, k)
        out[k.label()] = {"p_win": round(o.win, 4), "p_push": round(o.push, 4), "fair_odds": round(fair_odds(o), 3)}
    return out


@server.tool()
def price_match(home_expected_goals: float, away_expected_goals: float, rho: float = -0.05) -> dict:
    """Probabilities and fair odds for all supported markets (1X2, double chance, draw no bet,
    totals 0.5-4.5, both teams to score) from expected goals, using the Dixon-Coles score model."""
    if not (0 < home_expected_goals < 8 and 0 < away_expected_goals < 8):
        raise ValueError("expected goals must be in (0, 8)")
    return _markets(score_matrix(home_expected_goals, away_expected_goals, rho))


@server.tool()
def price_live(pre_home_expected_goals: float, pre_away_expected_goals: float, minute: float, home_goals: int,
               away_goals: int, home_red_cards: int = 0, away_red_cards: int = 0,
               home_xg_so_far: float | None = None, away_xg_so_far: float | None = None) -> dict:
    """In-play probabilities for final-result markets given the current match state. Pass xG so far only if it
    comes from a real feed; omit it otherwise."""
    state = LiveState(minute, home_goals, away_goals, home_red_cards, away_red_cards, home_xg_so_far, away_xg_so_far)
    return _markets(final_score_matrix(pre_home_expected_goals, pre_away_expected_goals, state))


@server.tool()
def bet_value(p_win: float, odds: float, p_push: float = 0.0, stress_delta: float = 0.02) -> dict:
    """Expected value per unit staked, fair odds, implied probability, and EV after lowering p_win by
    stress_delta. A high probability does not imply positive EV."""
    o = Outcome(win=p_win, push=p_push)
    stressed = Outcome(win=max(0.0, p_win - stress_delta), push=p_push)
    return {"ev": round(expected_value(o, odds), 4), "ev_stressed": round(expected_value(stressed, odds), 4),
            "fair_odds": round(fair_odds(o), 4), "implied_probability": round(implied_probability(odds), 4)}


@server.tool()
def remove_margin(odds: list[float], method: str = "shin") -> dict:
    """Bookmaker margin of a complete market and margin-free probabilities (methods: shin, power, proportional)."""
    if method not in DEVIG_METHODS:
        raise ValueError(f"method must be one of {sorted(DEVIG_METHODS)}")
    return {"overround": round(overround(odds), 4),
            "probabilities": [round(p, 4) for p in DEVIG_METHODS[method](odds)]}


@server.tool()
def market_probability(home_expected_goals: float, away_expected_goals: float, market: str, selection: str,
                       line: float | None = None, rho: float = -0.05) -> dict:
    """Probability of one market selection, e.g. market='totals', selection='over', line=2.5."""
    o = outcome(score_matrix(home_expected_goals, away_expected_goals, rho), MarketKey(market, selection, line))
    return {"p_win": round(o.win, 4), "p_push": round(o.push, 4), "fair_odds": round(fair_odds(o), 3)}


@server.tool()
def recent_runs(limit: int = 10) -> list[dict]:
    """Latest analysis runs with status, outcome (selections / no_opportunity) and data mode."""
    from sportai.db.models import AnalysisRun
    from sportai.db.session import session_factory
    db = session_factory()()
    try:
        rows = db.scalars(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(min(limit, 50)))
        return [{"id": r.id, "competition": r.competition, "mode": r.mode, "status": r.status, "outcome": r.outcome,
                 "started_at": r.started_at.isoformat(), "detail": r.status_detail} for r in rows]
    finally:
        db.close()


@server.tool()
def track_record(kind: str | None = None) -> dict:
    """Published, settled, real selections: count, hit rate, ROI with 95% interval and per-market breakdown."""
    from sportai.db.session import session_factory
    from sportai.services.publication import performance
    db = session_factory()()
    try:
        return performance(db, kind)
    finally:
        db.close()


if __name__ == "__main__":
    server.run("stdio")
