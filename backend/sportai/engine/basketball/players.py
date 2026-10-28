"""Basketball player-stat model: points, 3-pointers, rebounds, assists and their sums.

For each player, from his earlier games only:

  * expected minutes = exponentially weighted mean of his recent minutes
    (half-life MIN_HALF_LIFE games);
  * per-minute rate of each stat = exponentially weighted (half-life
    RATE_HALF_LIFE games, weighted by minutes) and shrunk towards the
    league's per-minute rate with PRIOR_MINUTES pseudo-minutes;
  * expected stat = minutes x rate;
  * distribution: negative binomial with variance = mean x (1 + mean / k),
    k per stat fitted on the 2023-24 validation season (DISPERSION).

Who plays is the big unknown: the API gives no injury list, so the pass only
prices players who played one of their team's last two games, and a late
absence voids the bet anyway at most books. Backtested in
scripts/backtest_nba_players.py. Not beating bookmakers by itself.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

STATS = ("pts", "fg3", "reb", "ast")
COMBOS = {"pts": ("pts",), "fg3": ("fg3",), "reb": ("reb",), "ast": ("ast",),
          "pts_ast": ("pts", "ast"), "pra": ("pts", "reb", "ast"), "reb_ast": ("reb", "ast")}
MIN_HALF_LIFE = 5.0
RATE_HALF_LIFE = 12.0
PRIOR_MINUTES = 60.0
# Negative-binomial size k per market (higher = closer to Poisson); set by the validation season.
DISPERSION = {"pts": 12.0, "fg3": 20.0, "reb": 20.0, "ast": 12.0, "pts_ast": 12.0, "pra": 16.0, "reb_ast": 20.0}


@dataclass
class Line:
    minutes: float
    pts: float
    fg3: float
    reb: float
    ast: float


def parse_minutes(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        m, sec = s.split(":")
        return int(m) + int(sec) / 60
    except ValueError:
        return 0.0


def nb_cdf(x: int, mean: float, k: float) -> float:
    """P(X <= x) for a negative binomial with that mean and size k."""
    if x < 0:
        return 0.0
    if mean <= 0:
        return 1.0
    p = k / (k + mean)
    prob = p ** k
    tot = prob
    for i in range(1, x + 1):
        prob *= (i - 1 + k) / i * (1 - p)
        tot += prob
    return min(1.0, tot)


@dataclass
class PlayerState:
    w_min: float = 0.0
    s_min: float = 0.0
    w_rate: float = 0.0
    s_stat: dict = field(default_factory=lambda: defaultdict(float))
    s_wmin: float = 0.0
    games: int = 0


class PlayerModel:
    """Online: call expected() before a game, then update() with the box score."""

    def __init__(self):
        self.state: dict[str, PlayerState] = defaultdict(PlayerState)
        self.league_min = 0.0
        self.league_stat = defaultdict(float)

    def league_rate(self, s: str) -> float:
        return self.league_stat[s] / self.league_min if self.league_min else {"pts": .5, "fg3": .05, "reb": .19, "ast": .11}[s]

    def known(self, pid: str) -> bool:
        return self.state[pid].games >= 3

    def expected(self, pid: str, minutes: float | None = None) -> dict[str, float]:
        st = self.state[pid]
        mins = minutes if minutes is not None else (st.s_min / st.w_min if st.w_min else 0.0)
        out = {}
        for s in STATS:
            rate = (st.s_stat[s] + PRIOR_MINUTES * self.league_rate(s)) / (st.s_wmin + PRIOR_MINUTES)
            out[s] = mins * rate
        for c, parts in COMBOS.items():
            out[c] = sum(out[p] for p in parts)
        out["minutes"] = mins
        return out

    def p_over(self, mean: float, market: str, line: float) -> float:
        return 1.0 - nb_cdf(math.floor(line), mean, DISPERSION[market])

    def update(self, pid: str, line: Line) -> None:
        st = self.state[pid]
        dm = 0.5 ** (1 / MIN_HALF_LIFE)
        dr = 0.5 ** (1 / RATE_HALF_LIFE)
        st.w_min = st.w_min * dm + 1
        st.s_min = st.s_min * dm + line.minutes
        st.s_wmin = st.s_wmin * dr + line.minutes
        for s in STATS:
            st.s_stat[s] = st.s_stat[s] * dr + getattr(line, s)
        st.games += 1
        self.league_min += line.minutes
        for s in STATS:
            self.league_stat[s] += getattr(line, s)
