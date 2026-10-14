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

from sportai.engine.football.markets import SUPPORTED_MARKETS, VALIDATED_MARKETS, is_quarter
from sportai.engine.value import Outcome, expected_value, fair_odds

# Markets priced by the model but not validated by a backtest (owner, 2026-10-14: every odd is analysed and may be
# selected): they pass stricter gates and every selection says "non validé".
TRIAL_MARKETS = tuple(m for m in SUPPORTED_MARKETS if m not in VALIDATED_MARKETS)


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
    # API-Football refreshes its pre-match odds about every 3 hours ("update" of each match): a 15-minute limit would
    # reject all of them. Its prices are judged by their own refresh time, up to 4 hours.
    max_odds_age_by_provider: dict = field(default_factory=lambda: {"api-football": 4 * 3600.0})
    # Totals and BTTS are off by default: in the 2020-2026 backtest the model's
    # over 2.5 forecasts were no better than the base rate (docs/04-backtest.md).
    enabled_markets: tuple[str, ...] = ("1x2", "double_chance", "dnb")
    one_per_match: bool = True
    # Shown in the goals block of each match with their own "not validated" warning.
    info_markets: tuple[str, ...] = ("totals", "btts")
    # Every other market the score model prices may be selected "à l'essai", labelled "non validé", with stricter
    # gates: more value, a pessimistic value clearly positive, a complete market to compare with, no quarter line
    # (half won / half lost is not followed by the page's settlement).
    trial_markets: tuple[str, ...] = TRIAL_MARKETS
    trial_min_ev: float = 0.06
    trial_min_ev_low: float = 0.03
    # Pre-match analysis only covers matches of the current day (Paris time) whose kick-off is at most this far
    # away: the passes run 3 h and 45 min before kick-off, when line-ups and odds are current (owner's rule).
    max_hours_ahead: float = 3.25

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
    max_odds_age: float | None = None  # overrides SelectionConfig.max_odds_age_seconds (slow feeds)


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
    validated: bool = True  # False: market not validated by a backtest, selected "à l'essai"

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
            "valide": self.validated,
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

    validated = c.market in cfg.enabled_markets
    trial = not validated and c.market in cfg.trial_markets
    if not validated and not trial:
        reasons.append(f"market {c.market} disabled")
    if trial and c.market_fair_prob is None:
        reasons.append(f"market {c.market} not validated: no complete market to compare the model with")
    if trial and is_quarter(c.line):
        reasons.append("quarter line (half won / half lost) not followed by the settlement")
    if not (cfg.min_odds <= c.odds <= cfg.max_odds):
        reasons.append(f"odds {c.odds} outside [{cfg.min_odds}, {cfg.max_odds}]")
    age = (now - c.odds_observed_at).total_seconds()
    max_age = c.max_odds_age or cfg.max_odds_age_seconds
    if age > max_age:
        reasons.append(f"odds are {age:.0f}s old (limit {max_age:.0f}s)")
    if c.odds_expires_at is not None and c.odds_expires_at <= now:
        reasons.append("odds observation expired")
    min_ev = cfg.trial_min_ev if trial else cfg.min_ev
    if ev < min_ev:
        reasons.append(f"EV {ev:.3f} below {min_ev}" + (" (market not validated)" if trial else ""))
    in_band = cfg.preferred_min_odds <= c.odds <= cfg.preferred_max_odds
    need_low = cfg.min_ev_low if in_band else cfg.min_ev_low_outside_preferred
    if trial:
        need_low = max(need_low, cfg.trial_min_ev_low)
    if ev_low < need_low:
        reasons.append(f"pessimistic EV {ev_low:.3f} below {need_low}")
    if ev_stress < cfg.min_ev_stress:
        reasons.append(f"stress-test EV {ev_stress:.3f} below {cfg.min_ev_stress}")
    # A de-vigged two-way price estimates the chance of winning when the bet is not refunded.
    p_cmp = c.outcome.win / (1 - c.push_prob) if c.push_prob > 0 else c.outcome.win
    if c.market_fair_prob is not None and abs(p_cmp - c.market_fair_prob) > cfg.max_market_divergence:
        reasons.append(
            f"model {p_cmp:.3f} vs market {c.market_fair_prob:.3f}: divergence too large, likely missing information"
        )
    if c.data_quality < cfg.min_data_quality:
        reasons.append(f"data quality {c.data_quality:.2f} below {cfg.min_data_quality}")
    reasons.extend(c.flags)

    # Rank on the pessimistic edge, discounted by data quality.
    score = ev_low * c.data_quality
    return Evaluated(c, fo, ev, p_low, ev_low, ev_stress, score, not reasons, reasons, validated)


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
