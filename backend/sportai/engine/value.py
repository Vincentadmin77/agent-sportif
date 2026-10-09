"""Betting value arithmetic: implied probabilities, margin removal, fair odds, EV.

Everything here is pure and deterministic. Probabilities are model outputs;
odds are observed bookmaker prices. The two are never mixed up: a fair odd
computed from a probability is never presented as an available price.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq


@dataclass(frozen=True)
class Outcome:
    """Probabilities of the three possible settlements of one bet.

    Markets like draw-no-bet or whole-line totals can be refunded (push), so a
    single probability is not enough to price them.
    """

    win: float
    push: float = 0.0

    @property
    def lose(self) -> float:
        return max(0.0, 1.0 - self.win - self.push)

    def __post_init__(self) -> None:
        if not (0.0 <= self.win <= 1.0 and 0.0 <= self.push <= 1.0):
            raise ValueError("probabilities must lie in [0, 1]")
        if self.win + self.push > 1.0 + 1e-9:
            raise ValueError("win + push must not exceed 1")


def implied_probability(odds: float) -> float:
    """Raw implied probability P = 1 / O (includes the bookmaker margin)."""
    _check_odds(odds)
    return 1.0 / odds


def overround(odds: list[float]) -> float:
    """Bookmaker margin of a complete market: sum(1/O) - 1."""
    return float(sum(implied_probability(o) for o in odds) - 1.0)


def expected_value(outcome: Outcome, odds: float) -> float:
    """Net expected profit per unit staked.

    For a plain binary bet this is p*O - 1. With a push the stake is returned,
    so EV = p_win*(O-1) - p_lose.
    """
    _check_odds(odds)
    return outcome.win * (odds - 1.0) - outcome.lose


def fair_odds(outcome: Outcome) -> float:
    """Odds at which EV is exactly zero. For a binary bet this is 1/p."""
    if outcome.win <= 0:
        return float("inf")
    return 1.0 + outcome.lose / outcome.win


def devig_proportional(odds: list[float]) -> list[float]:
    """Remove the margin by normalising implied probabilities."""
    raw = np.array([implied_probability(o) for o in odds])
    return list(raw / raw.sum())


def devig_power(odds: list[float]) -> list[float]:
    """Power method: find k such that sum((1/O)^k) = 1.

    Allocates more margin to longshots than the proportional method.
    """
    raw = np.array([implied_probability(o) for o in odds])
    if abs(raw.sum() - 1.0) < 1e-12:
        return list(raw)
    k = brentq(lambda k: np.sum(raw**k) - 1.0, 0.01, 100.0)
    return list(raw**k)


def devig_shin(odds: list[float]) -> list[float]:
    """Shin's method, which models the margin as protection against insiders.

    Solves for the insider share z, then p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)),
    where q_i = 1/O_i and S = sum q_i.
    """
    q = np.array([implied_probability(o) for o in odds])
    s = q.sum()
    if s <= 1.0 + 1e-12:
        return list(q / s)

    def probs(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4 * (1 - z) * q**2 / s) - z) / (2 * (1 - z))

    z = brentq(lambda z: probs(z).sum() - 1.0, 0.0, 0.999)
    return list(probs(z))


DEVIG_METHODS = {
    "proportional": devig_proportional,
    "power": devig_power,
    "shin": devig_shin,
}


def _check_odds(odds: float) -> None:
    if not np.isfinite(odds) or odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {odds}")
