"""Live re-evaluation and alert rules.

Runs server-side from the worker. Every alert carries the match state, the
observed price, the model probability, fair odds, EV, the data timestamp, the
reasons and the conditions that invalidate it. A price move alone never
produces a commercial alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sportai.connectors.base import LiveSnapshot, OddsQuote
from sportai.engine.football.live import LiveConfig, LiveState, StaleDataError, check_fresh, final_score_matrix
from sportai.engine.football.markets import MarketKey, outcome
from sportai.engine.value import expected_value, fair_odds


@dataclass
class LiveConfigAlerts:
    min_ev: float = 0.05
    stress_delta: float = 0.03
    max_odds_age_seconds: float = 30.0
    favourite_threshold: float = 0.55  # pre-match win probability to count as favourite
    dominance_xg_gap: float = 0.6


@dataclass
class Alert:
    kind: str  # opportunity, favourite_conceded_first, dominant_but_trailing, stale_data
    match_id: str
    match_label: str
    minute: float
    score: str
    data_updated_at: str
    commercial: bool  # only opportunities that pass every check are commercial
    market: str | None = None
    odds: float | None = None
    bookmaker: str | None = None
    probability: float | None = None
    fair_odds: float | None = None
    ev: float | None = None
    reasons: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)


def evaluate_live(
    match_id: str,
    match_label: str,
    pre_home_rate: float,
    pre_away_rate: float,
    pre_home_win: float,
    pre_away_win: float,
    snap: LiveSnapshot,
    quotes: list[OddsQuote],
    cfg: LiveConfigAlerts | None = None,
    live_cfg: LiveConfig | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    cfg = cfg or LiveConfigAlerts()
    live_cfg = live_cfg or LiveConfig()
    now = now or datetime.now(timezone.utc)
    state = LiveState(snap.minute, snap.home_goals, snap.away_goals, snap.home_red_cards, snap.away_red_cards,
                      snap.home_xg, snap.away_xg, snap.observed_at, snap.provider)
    base = dict(match_id=match_id, match_label=match_label, minute=snap.minute,
                score=f"{snap.home_goals}-{snap.away_goals}", data_updated_at=snap.observed_at.isoformat())
    try:
        check_fresh(state, live_cfg, now)
    except StaleDataError as e:
        return [Alert(kind="stale_data", commercial=False, reasons=[str(e)],
                      invalidation=["recommendations blocked until fresher data arrives"], **base)]

    alerts: list[Alert] = []
    context: list[str] = []
    fav = "home" if pre_home_win >= cfg.favourite_threshold else "away" if pre_away_win >= cfg.favourite_threshold else None
    if fav and snap.home_goals + snap.away_goals == 1:
        conceded = (fav == "home" and snap.away_goals == 1) or (fav == "away" and snap.home_goals == 1)
        if conceded:
            context.append(f"favourite ({fav}) conceded first at {snap.minute:.0f}'")
            alerts.append(Alert(kind="favourite_conceded_first", commercial=False, reasons=context.copy(), **base))
    if snap.home_xg is not None and snap.away_xg is not None:
        gap = snap.home_xg - snap.away_xg
        trailing = snap.home_goals < snap.away_goals if gap > 0 else snap.away_goals < snap.home_goals
        if abs(gap) >= cfg.dominance_xg_gap and trailing:
            side = "home" if gap > 0 else "away"
            msg = f"{side} trails despite xG {snap.home_xg:.2f}-{snap.away_xg:.2f}"
            context.append(msg)
            alerts.append(Alert(kind="dominant_but_trailing", commercial=False, reasons=[msg], **base))

    m = final_score_matrix(pre_home_rate, pre_away_rate, state, live_cfg)
    best: dict[MarketKey, OddsQuote] = {}
    for q in quotes:
        if not q.is_live or q.validation_status != "valid":
            continue
        if (now - q.observed_at).total_seconds() > cfg.max_odds_age_seconds:
            continue
        k = MarketKey(q.market, q.selection, q.line)
        if k not in best or q.odds > best[k].odds:
            best[k] = q
    for k, q in best.items():
        o = outcome(m, k)
        ev = expected_value(o, q.odds)
        stressed = expected_value(type(o)(win=max(0.0, o.win - cfg.stress_delta), push=o.push), q.odds)
        if ev >= cfg.min_ev and stressed >= 0:
            alerts.append(Alert(
                kind="opportunity", commercial=True, market=k.label(), odds=q.odds, bookmaker=q.bookmaker,
                probability=round(o.win, 4), fair_odds=round(fair_odds(o), 3), ev=round(ev, 4),
                reasons=context + [f"live model {o.win:.1%} vs price {q.odds} (fair {fair_odds(o):.2f})"],
                invalidation=[
                    "any goal or red card",
                    f"price falls below {fair_odds(o) * (1 + cfg.min_ev):.2f}",
                    f"data older than {live_cfg.max_staleness_seconds:.0f}s",
                ],
                **base,
            ))
    return alerts
