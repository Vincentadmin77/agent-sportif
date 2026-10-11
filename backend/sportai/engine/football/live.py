"""In-play model: final-score distribution conditional on the match state.

Remaining goals for each side are Poisson with the pre-match rate scaled by
the time left, adjusted for red cards, and (optionally) updated with the
in-play expected goals observed so far through a gamma-Poisson update. When
no in-play xG is supplied, the pre-match rate is used unchanged: shots or xG
are never invented to fill a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from scipy.stats import poisson

LIVE_MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class LiveState:
    minute: float
    home_goals: int
    away_goals: int
    home_red_cards: int = 0
    away_red_cards: int = 0
    home_xg: float | None = None  # in-play xG so far, only from a real feed
    away_xg: float | None = None
    observed_at: datetime | None = None
    source: str | None = None


@dataclass
class LiveConfig:
    match_length: float = 95.0  # 90 + typical stoppage time
    # Red-card multipliers on the remaining scoring rate, estimated on 424 matches
    # (top 5 leagues, 2024-25 and 2025-26): 0.49 [0.41-0.58] and 1.60 [1.47-1.74].
    # See docs/12-cartons-rouges.md. Previous uncalibrated values: 0.67 / 1.25.
    red_card_own_factor: float = 0.49  # scoring rate of the side down a player
    red_card_opp_factor: float = 1.60  # scoring rate of its opponent
    prior_strength_minutes: float = 60.0  # weight of the pre-match rate, in minutes of play
    max_goals_remaining: int = 10
    max_staleness_seconds: float = 60.0


class StaleDataError(RuntimeError):
    """Raised when the live state is too old to support a recommendation."""


def check_fresh(state: LiveState, config: LiveConfig, now: datetime | None = None) -> float:
    if state.observed_at is None:
        raise StaleDataError("live state has no observation timestamp")
    now = now or datetime.now(timezone.utc)
    age = (now - state.observed_at).total_seconds()
    if age > config.max_staleness_seconds:
        raise StaleDataError(
            f"live data is {age:.0f}s old (limit {config.max_staleness_seconds:.0f}s), "
            f"last update {state.observed_at.isoformat()}"
        )
    return age


def remaining_rates(
    pre_home: float, pre_away: float, state: LiveState, config: LiveConfig | None = None
) -> tuple[float, float]:
    """Expected goals still to come for each side."""
    config = config or LiveConfig()
    elapsed = min(max(state.minute, 0.0), config.match_length)
    left = (config.match_length - elapsed) / config.match_length

    rate_h = pre_home / config.match_length  # goals per minute
    rate_a = pre_away / config.match_length
    if state.home_xg is not None and state.away_xg is not None and elapsed > 0:
        rate_h = _gamma_poisson(rate_h, state.home_xg, elapsed, config.prior_strength_minutes)
        rate_a = _gamma_poisson(rate_a, state.away_xg, elapsed, config.prior_strength_minutes)

    lam = rate_h * config.match_length * left
    mu = rate_a * config.match_length * left
    red_diff = state.home_red_cards - state.away_red_cards
    if red_diff > 0:
        lam *= config.red_card_own_factor**red_diff
        mu *= config.red_card_opp_factor**red_diff
    elif red_diff < 0:
        mu *= config.red_card_own_factor ** (-red_diff)
        lam *= config.red_card_opp_factor ** (-red_diff)
    return lam, mu


def final_score_matrix(
    pre_home: float, pre_away: float, state: LiveState, config: LiveConfig | None = None
) -> np.ndarray:
    """P(final home = i, final away = j), with the current score folded in."""
    config = config or LiveConfig()
    lam, mu = remaining_rates(pre_home, pre_away, state, config)
    g = np.arange(config.max_goals_remaining + 1)
    rem = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    rem /= rem.sum()
    size = config.max_goals_remaining + 1 + max(state.home_goals, state.away_goals)
    m = np.zeros((size, size))
    h, a = state.home_goals, state.away_goals
    m[h : h + len(g), a : a + len(g)] = rem
    return m


def _gamma_poisson(prior_rate: float, observed: float, elapsed: float, strength: float) -> float:
    # Prior Gamma(alpha = rate*strength, beta = strength); observing `observed`
    # expected goals over `elapsed` minutes gives posterior mean below.
    return (prior_rate * strength + observed) / (strength + elapsed)
