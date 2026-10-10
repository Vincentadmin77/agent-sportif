"""Opportunity filter and ranker: 0 to N selections, never a quota.

Every candidate is checked against hard quality gates first. Only candidates
that pass all gates are ranked, and at most one selection per match is kept
because markets on the same match are strongly correlated. Rejected
candidates keep their reasons so the filter itself can be audited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np

from sportai.engine.value import Outcome, expected_value, fair_odds


@dataclass
class SelectionConfig:
    max_selections: int = 4
    min_odds: float = 1.30
    max_odds: float = 4.00
    preferred_min_odds: float = 1.50
    preferred_max_odds: float = 2.00
    min_ev: float = 0.03  # central EV per unit staked
    min_ev_low: float = 0.0  # EV at the pessimistic probability quantile
    min_ev_low_outside_preferred: float = 0.02  # stricter outside the 1.50-2.00 band
    stress_delta: float = 0.02  # absolute probability haircut for the stress test
    min_ev_stress: float = 0.0
    max_market_divergence: float = 0.10  # |model - de-vigged market| beyond this needs review
    min_data_quality: float = 0.6
    max_odds_age_seconds: float = 900.0  # pre-match; live uses its own limit
    # Totals and BTTS are off by default: in the 2020-2026 backtest the model's
    # over 2.5 forecasts were no better than the base rate (docs/04-backtest.md).
    enabled_markets: tuple[str, ...] = ("1x2", "double_chance", "dnb")
    one_per_match: bool = True
    max_hours_ahead: float = 48.0  # only analyse matches kicking off within this window

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    match_id: str
    match_label: str
    market: str
    selection: str
    line: float | None
    odds: float
    bookmaker: str
    odds_observed_at: datetime
    odds_expires_at: datetime | None
    outcome: Outcome  # central model probabilities
    win_prob_samples: np.ndarray  # win probability under parameter uncertainty
    push_prob: float
    market_fair_prob: float | None  # de-vigged market probability, if a full market was observed
    data_quality: float
    data_mode: str  # "real" or "simulation"
    flags: list[str] = field(default_factory=list)


@dataclass
class Evaluated:
    candidate: Candidate
    fair_odds: float
    ev: float
    p_low: float
    ev_low: float
    ev_stress: float
    score: float
    accepted: bool
    reasons: list[str]

    def summary(self) -> dict:
        c = self.candidate
        return {
            "match_id": c.match_id,
            "match": c.match_label,
            "market": c.market,
            "selection": c.selection,
            "line": c.line,
            "odds": c.odds,
            "bookmaker": c.bookmaker,
            "odds_observed_at": c.odds_observed_at.isoformat(),
            "p_win": round(c.outcome.win, 4),
            "p_push": round(c.outcome.push, 4),
            "p_low": round(self.p_low, 4),
            "fair_odds": round(self.fair_odds, 3),
            "ev": round(self.ev, 4),
            "ev_low": round(self.ev_low, 4),
            "ev_stress": round(self.ev_stress, 4),
            "market_fair_prob": None if c.market_fair_prob is None else round(c.market_fair_prob, 4),
            "data_quality": round(c.data_quality, 3),
            "data_mode": c.data_mode,
            "score": round(self.score, 5),
            "accepted": self.accepted,
            "reasons": self.reasons,
        }


@dataclass
class SelectionResult:
    selected: list[Evaluated]
    rejected: list[Evaluated]
    status: str  # "selections" or "no_opportunity"
    config: SelectionConfig


def evaluate(c: Candidate, cfg: SelectionConfig, now: datetime) -> Evaluated:
    reasons: list[str] = []
    ev = expected_value(c.outcome, c.odds)
    fo = fair_odds(c.outcome)
    p_low = float(np.percentile(c.win_prob_samples, 10)) if len(c.win_prob_samples) else c.outcome.win
    ev_low = expected_value(Outcome(win=min(p_low, 1 - c.push_prob), push=c.push_prob), c.odds)
    p_stress = max(0.0, c.outcome.win - cfg.stress_delta)
    ev_stress = expected_value(Outcome(win=p_stress, push=c.push_prob), c.odds)

    if c.market not in cfg.enabled_markets:
        reasons.append(f"market {c.market} disabled")
    if not (cfg.min_odds <= c.odds <= cfg.max_odds):
        reasons.append(f"odds {c.odds} outside [{cfg.min_odds}, {cfg.max_odds}]")
    age = (now - c.odds_observed_at).total_seconds()
    if age > cfg.max_odds_age_seconds:
        reasons.append(f"odds are {age:.0f}s old (limit {cfg.max_odds_age_seconds:.0f}s)")
    if c.odds_expires_at is not None and c.odds_expires_at <= now:
        reasons.append("odds observation expired")
    if ev < cfg.min_ev:
        reasons.append(f"EV {ev:.3f} below {cfg.min_ev}")
    in_band = cfg.preferred_min_odds <= c.odds <= cfg.preferred_max_odds
    need_low = cfg.min_ev_low if in_band else cfg.min_ev_low_outside_preferred
    if ev_low < need_low:
        reasons.append(f"pessimistic EV {ev_low:.3f} below {need_low}")
    if ev_stress < cfg.min_ev_stress:
        reasons.append(f"stress-test EV {ev_stress:.3f} below {cfg.min_ev_stress}")
    if c.market_fair_prob is not None and abs(c.outcome.win - c.market_fair_prob) > cfg.max_market_divergence:
        reasons.append(
            f"model {c.outcome.win:.3f} vs market {c.market_fair_prob:.3f}: divergence too large, likely missing information"
        )
    if c.data_quality < cfg.min_data_quality:
        reasons.append(f"data quality {c.data_quality:.2f} below {cfg.min_data_quality}")
    reasons.extend(c.flags)

    # Rank on the pessimistic edge, discounted by data quality.
    score = ev_low * c.data_quality
    return Evaluated(c, fo, ev, p_low, ev_low, ev_stress, score, not reasons, reasons)


def select(candidates: list[Candidate], cfg: SelectionConfig | None = None, now: datetime | None = None) -> SelectionResult:
    cfg = cfg or SelectionConfig()
    now = now or datetime.now(timezone.utc)
    evaluated = [evaluate(c, cfg, now) for c in candidates]
    passing = sorted((e for e in evaluated if e.accepted), key=lambda e: e.score, reverse=True)

    selected: list[Evaluated] = []
    used_matches: set[str] = set()
    for e in passing:
        if len(selected) >= cfg.max_selections:
            e.accepted = False
            e.reasons.append(f"cap of {cfg.max_selections} selections reached")
            continue
        if cfg.one_per_match and e.candidate.match_id in used_matches:
            e.accepted = False
            e.reasons.append("correlated with a stronger selection on the same match")
            continue
        selected.append(e)
        used_matches.add(e.candidate.match_id)

    rejected = [e for e in evaluated if not e.accepted]
    return SelectionResult(selected, rejected, "selections" if selected else "no_opportunity", cfg)
