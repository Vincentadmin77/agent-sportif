"""Serve and return level of each player, from the points won in past matches (match statistics).

Each player has a share of service points won (SPW) and of return points won (RPW), exponentially weighted over
past matches and shrunk toward the tour mean. For A serving to B, the chance of winning a point is the usual
combination  p = tour SPW + (SPW(A) - tour SPW) - (RPW(B) - tour RPW), and a game is held with the exact
"from deuce" formula. What this adds to the Elo is the LEVEL of the holds: two big servers hold a lot, play
tie-breaks and long sets (more games), two strong returners break each other often. The gap between the two
players stays the Elo's (elo.holds_from_set with base = the average hold of the pair).
"""

from __future__ import annotations

from collections import defaultdict

DECAY = 0.95
PRIOR_POINTS = 150.0


def hold(p: float) -> float:
    """Chance of holding a service game when each point on serve is won with chance p."""
    q = 1 - p
    return p ** 4 * (1 + 4 * q + 10 * q ** 2) + 20 * p ** 3 * q ** 3 * p ** 2 / (1 - 2 * p * q)


class _Rate:
    __slots__ = ("won", "total")

    def __init__(self):
        self.won = self.total = 0.0

    def add(self, w: float, t: float) -> None:
        self.won = self.won * DECAY + w
        self.total = self.total * DECAY + t

    def value(self, prior: float) -> float:
        return (self.won + prior * PRIOR_POINTS) / (self.total + PRIOR_POINTS)


class ServeModel:
    def __init__(self):
        self.spw: dict[str, _Rate] = defaultdict(_Rate)
        self.rpw: dict[str, _Rate] = defaultdict(_Rate)
        self.tour_won = defaultdict(float)
        self.tour_total = defaultdict(float)

    def tour_spw(self, surface: str) -> float:
        t = self.tour_total[surface]
        return self.tour_won[surface] / t if t > 5000 else 0.63

    def p_point(self, a: str, b: str, surface: str) -> float:
        m = self.tour_spw(surface)
        p = m + (self.spw[a].value(m) - m) - (self.rpw[b].value(1 - m) - (1 - m))
        return min(0.9, max(0.3, p))

    def base_hold(self, a: str, b: str, surface: str) -> float:
        return (hold(self.p_point(a, b, surface)) + hold(self.p_point(b, a, surface))) / 2

    def update(self, a: str, b: str, surface: str, serve_a: tuple[int, int], serve_b: tuple[int, int]) -> None:
        """serve_x = (service points won, service points played) of player x."""
        (wa, ta), (wb, tb) = serve_a, serve_b
        if ta <= 0 or tb <= 0:
            return
        self.spw[a].add(wa, ta)
        self.spw[b].add(wb, tb)
        self.rpw[a].add(tb - wb, tb)
        self.rpw[b].add(ta - wa, ta)
        self.tour_won[surface] += wa + wb
        self.tour_total[surface] += ta + tb
