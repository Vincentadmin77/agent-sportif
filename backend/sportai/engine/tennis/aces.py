"""Aces per player in a tennis match (market "1x2 Aces": who serves more aces, or a tie).

Each player has a serving rate (aces per game played in the match) and a receiving factor (aces conceded per game,
relative to the tour). Both are exponentially weighted averages over past matches, shrunk toward the tour mean, and
every observation is divided by the surface factor first (grass and indoor hard give more aces than clay), so a
player's rate does not depend on where he played lately.

  expected aces of A = rate(A) * concede(B) * surface factor * expected games of the match

The match length comes from the Elo game distribution (elo.games_distribution). Counts are negative binomial
(aces are over-dispersed: a big server can have 5 or 25), DISPERSION fitted on the backtest.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

DECAY = 0.95  # weight of the previous matches at each new match (~20 matches memory)
PRIOR_GAMES = 60.0  # strength of the shrink toward the tour mean, in games
DISPERSION = 8.0  # negative binomial k: variance = mu + mu^2 / k


@dataclass
class _Rate:
    num: float = 0.0
    den: float = 0.0

    def add(self, n: float, d: float) -> None:
        self.num = self.num * DECAY + n
        self.den = self.den * DECAY + d

    def value(self, prior: float) -> float:
        return (self.num + prior * PRIOR_GAMES) / (self.den + PRIOR_GAMES)


class AcesModel:
    def __init__(self):
        self.serve: dict[str, _Rate] = defaultdict(_Rate)
        self.concede: dict[str, _Rate] = defaultdict(_Rate)
        self.tour_aces = defaultdict(float)  # per (tour, surface) and per (tour, None)
        self.tour_games = defaultdict(float)

    def _tour_rate(self, tour: str, surface: str | None) -> float:
        g = self.tour_games[(tour, surface)]
        return self.tour_aces[(tour, surface)] / g if g > 200 else 0.5 if tour == "ATP" else 0.25

    def surface_factor(self, tour: str, surface: str) -> float:
        return self._tour_rate(tour, surface) / self._tour_rate(tour, None)

    def expected(self, a: str, b: str, tour: str, surface: str, games: float) -> tuple[float, float]:
        sf, mean = self.surface_factor(tour, surface), self._tour_rate(tour, None)
        ea = self.serve[a].value(mean) * self.concede[b].value(1.0) * sf * games
        eb = self.serve[b].value(mean) * self.concede[a].value(1.0) * sf * games
        return ea, eb

    def update(self, a: str, b: str, tour: str, surface: str, aces_a: int, aces_b: int, games: int) -> None:
        if games <= 0:
            return
        sf, mean = self.surface_factor(tour, surface), self._tour_rate(tour, None)
        # Receiving factor first measured against the server's own level, then the server against the receiver's.
        ra, rb = self.serve[a].value(mean), self.serve[b].value(mean)
        self.concede[b].add(aces_a / (sf * ra), games)
        self.concede[a].add(aces_b / (sf * rb), games)
        self.serve[a].add(aces_a / (sf * self.concede[b].value(1.0)), games)
        self.serve[b].add(aces_b / (sf * self.concede[a].value(1.0)), games)
        for key in ((tour, surface), (tour, None)):
            self.tour_aces[key] += aces_a + aces_b
            self.tour_games[key] += 2 * games  # each game counts once for each player's rate


def _nb_pmf(mu: float, k: float, n_max: int = 80) -> list[float]:
    p = k / (k + mu)
    out, logc = [], 0.0
    for n in range(n_max + 1):
        if n:
            logc += math.log(n - 1 + k) - math.log(n)
        out.append(math.exp(logc + k * math.log(p) + n * math.log(1 - p)))
    return out


def p_more_aces(ea: float, eb: float, k: float = DISPERSION) -> tuple[float, float, float]:
    """(P(A more aces), P(tie), P(B more aces))."""
    pa, pb = _nb_pmf(max(ea, 0.05), k), _nb_pmf(max(eb, 0.05), k)
    cb = [0.0]
    for x in pb:
        cb.append(cb[-1] + x)
    win = sum(pa[i] * cb[i] for i in range(len(pa)))
    tie = sum(pa[i] * pb[i] for i in range(len(pa)))
    return win, tie, max(0.0, 1 - win - tie)
