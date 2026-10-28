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
# Stars (expected points 18+, about 3 players a team): steadier scorers, larger k chosen on 2023-24 for them alone.
STAR_MEAN = {"pts": 18.0, "pts_ast": 22.0, "pra": 27.0}
STAR_DISPERSION = {"pts": 20.0, "pts_ast": 20.0, "pra": 26.0}


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
    # Long memory of the player's own production (half-life LONG_HALF_LIFE games): the prior of his rate, so a
    # star is pulled towards his own level, not the league's.
    l_stat: dict = field(default_factory=lambda: defaultdict(float))
    l_wmin: float = 0.0


LONG_HALF_LIFE = 60.0
OWN_PRIOR = True  # False: shrink to the league's rate only (the first version)
# The own-level prior helps stars and hurts bench players (backtest): it applies from this long-run scoring level.
OWN_PRIOR_MIN_PTS = 15.0
# Opponent: each stat scaled by (allowed by the opponent / league average) ** OPP_WEIGHT; home side by HOME_FACTOR.
OPP_HALF_LIFE = 15.0
OPP_WEIGHT = 0.5
HOME_FACTOR = 1.0


class TeamDefense:
    """What each team concedes per game (points, 3-pointers, rebounds, assists of its opponents), recent games weighted."""

    def __init__(self):
        self.w: dict = defaultdict(float)
        self.s: dict = defaultdict(lambda: defaultdict(float))
        self.lg = defaultdict(float)
        self.ln = 0

    def factor(self, team) -> dict[str, float]:
        if not self.ln or self.w[team] < 3:
            return {s: 1.0 for s in STATS}
        return {s: ((self.s[team][s] / self.w[team]) / (self.lg[s] / self.ln)) ** OPP_WEIGHT if self.lg[s] else 1.0
                for s in STATS}

    def update(self, team, conceded: dict[str, float]) -> None:
        d = 0.5 ** (1 / OPP_HALF_LIFE)
        self.w[team] = self.w[team] * d + 1
        for s in STATS:
            self.s[team][s] = self.s[team][s] * d + conceded[s]
            self.lg[s] += conceded[s]
        self.ln += 1


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

    def expected(self, pid: str, minutes: float | None = None, opp: dict[str, float] | None = None,
                 home: bool | None = None) -> dict[str, float]:
        st = self.state[pid]
        mins = minutes if minutes is not None else (st.s_min / st.w_min if st.w_min else 0.0)
        out = {}
        long_pts = mins * (st.l_stat["pts"] / st.l_wmin if st.l_wmin else 0.0)
        own = OWN_PRIOR and long_pts >= OWN_PRIOR_MIN_PTS
        for s in STATS:
            # Prior: for a scorer, his own long-run rate (itself shrunk to the league's); else the league's rate.
            prior = ((st.l_stat[s] + PRIOR_MINUTES * self.league_rate(s)) / (st.l_wmin + PRIOR_MINUTES) if own
                     else self.league_rate(s))
            rate = (st.s_stat[s] + PRIOR_MINUTES * prior) / (st.s_wmin + PRIOR_MINUTES)
            f = (opp or {}).get(s, 1.0) * (HOME_FACTOR if home else 1 / HOME_FACTOR if home is False else 1.0)
            out[s] = mins * rate * f
        for c, parts in COMBOS.items():
            out[c] = sum(out[p] for p in parts)
        out["minutes"] = mins
        return out

    def p_over(self, mean: float, market: str, line: float) -> float:
        k = STAR_DISPERSION[market] if mean >= STAR_MEAN.get(market, 1e9) else DISPERSION[market]
        return 1.0 - nb_cdf(math.floor(line), mean, k)

    def update(self, pid: str, line: Line) -> None:
        st = self.state[pid]
        dm = 0.5 ** (1 / MIN_HALF_LIFE)
        dr = 0.5 ** (1 / RATE_HALF_LIFE)
        st.w_min = st.w_min * dm + 1
        st.s_min = st.s_min * dm + line.minutes
        st.s_wmin = st.s_wmin * dr + line.minutes
        dl = 0.5 ** (1 / LONG_HALF_LIFE)
        st.l_wmin = st.l_wmin * dl + line.minutes
        for s in STATS:
            st.s_stat[s] = st.s_stat[s] * dr + getattr(line, s)
            st.l_stat[s] = st.l_stat[s] * dl + getattr(line, s)
        st.games += 1
        self.league_min += line.minutes
        for s in STATS:
            self.league_stat[s] += getattr(line, s)
