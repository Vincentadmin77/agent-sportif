"""Basketball team-ratings model (NBA, WNBA): points scored by each side.

Dixon-Coles counts rare goals; a basketball score is the sum of ~100
possessions, so each side's points are close to normal. The model:

    home_pts = base + hca + att[home] - dfn[away] + rest effects + e1
    away_pts = base       + att[away] - dfn[home] + rest effects + e2

fitted by time-weighted ridge least squares (half-life HALF_LIFE_DAYS, ridge
shrinks every team towards league average, so early-season ratings lean on
last season). From the two expected scores:

  * margin = home - away ~ Normal(mu_m, sigma_m)  -> winner (incl. overtime),
    point spreads;
  * total = home + away ~ Normal(mu_t, sigma_t)   -> total points;
  * each side's points                              -> team totals.

sigma_m and sigma_t are the residual spreads of the fit (recomputed at every
refit), so the probabilities stay calibrated to the league.

Rest: back-to-back (played the day before) and long rest (3+ days) are two
binary features per side, learned with the ratings.

Backtested in scripts/backtest_nba.py. Not beating bookmakers by itself: a
probability still goes through the usual value checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

HALF_LIFE_DAYS = 80.0
RIDGE = 2.0
# First quarter, fitted on the 2023-24 validation season only (backtest_nba.py):
# Q1 margin = Q1_SLOPE * full-game expected margin + Q1_INTERCEPT, residual Q1_SIGMA;
# Q1 total = Q1_TOTAL_SLOPE * expected total + Q1_TOTAL_INTERCEPT, residual Q1_TOTAL_SIGMA.
Q1_SLOPE, Q1_INTERCEPT, Q1_SIGMA = 0.315, 0.21, 8.39
Q1_TOTAL_SLOPE, Q1_TOTAL_INTERCEPT, Q1_TOTAL_SIGMA = 0.254, -1.36, 8.23


@dataclass(frozen=True)
class Game:
    day: date
    home: str
    away: str
    home_pts: int | None = None
    away_pts: int | None = None
    overtime: bool = False
    q1_home: int | None = None
    q1_away: int | None = None


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def rest_flags(games: list[Game]) -> list[tuple[int, int, int, int]]:
    """(home_b2b, home_rested, away_b2b, away_rested) for each game, from earlier games only."""
    last: dict[str, date] = {}
    out = []
    for g in games:
        flags = []
        for t in (g.home, g.away):
            prev = last.get(t)
            gap = (g.day - prev).days if prev else 99
            flags += [int(gap == 1), int(3 <= gap < 30)]
        out.append(tuple(flags))
        last[g.home] = last[g.away] = g.day
    return out


@dataclass
class Prediction:
    home_pts: float
    away_pts: float
    sigma_margin: float
    sigma_total: float
    sigma_side: float

    @property
    def margin(self) -> float:
        return self.home_pts - self.away_pts

    @property
    def total(self) -> float:
        return self.home_pts + self.away_pts

    def p_home_win(self) -> float:
        # A tie after 48 minutes goes to overtime, roughly a coin flip: the
        # normal model's mass around 0 is split evenly, which P(margin > 0) does.
        return _phi(self.margin / self.sigma_margin)

    def p_home_cover(self, handicap: float) -> float:
        """P(home margin + handicap > 0), e.g. handicap -5.5 = home wins by 6+."""
        return _phi((self.margin + handicap) / self.sigma_margin)

    def p_over(self, line: float) -> float:
        return 1.0 - _phi((line - self.total) / self.sigma_total)

    def q1_probs(self) -> tuple[float, float, float]:
        """(home, tie, away) after the first quarter; a tie is a whole-point margin of 0."""
        mu = Q1_SLOPE * self.margin + Q1_INTERCEPT
        lo, hi = _phi((-0.5 - mu) / Q1_SIGMA), _phi((0.5 - mu) / Q1_SIGMA)
        return 1.0 - hi, hi - lo, lo

    def q1_home_win_no_tie(self) -> float:
        """'Home/Away - 1st Qtr' (stake refunded on a tie): home share of the decisive outcomes."""
        h, _, a = self.q1_probs()
        return h / (h + a)

    def p_q1_over(self, line: float) -> float:
        mu = Q1_TOTAL_SLOPE * self.total + Q1_TOTAL_INTERCEPT
        return 1.0 - _phi((line - mu) / Q1_TOTAL_SIGMA)

    def p_team_over(self, side: str, line: float) -> float:
        mu = self.home_pts if side == "home" else self.away_pts
        return 1.0 - _phi((line - mu) / self.sigma_side)


class RatingsModel:
    def __init__(self, half_life: float = HALF_LIFE_DAYS, ridge: float = RIDGE):
        self.half_life = half_life
        self.ridge = ridge
        self.teams: list[str] = []

    def fit(self, games: list[Game], as_of: date) -> "RatingsModel":
        played = [g for g in games if g.home_pts is not None and g.day < as_of]
        flags = rest_flags(games)
        fl = {id(g): f for g, f in zip(games, flags)}
        self.teams = sorted({g.home for g in played} | {g.away for g in played})
        idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        # Columns: base, hca, att[n], dfn[n], b2b, rested.
        k = 2 + 2 * n + 2
        rows, ys, ws = [], [], []
        for g in played:
            w = 0.5 ** ((as_of - g.day).days / self.half_life)
            hb, hr, ab, ar = fl[id(g)]
            for side in ("home", "away"):
                x = np.zeros(k)
                x[0] = 1.0
                if side == "home":
                    x[1] = 1.0
                    x[2 + idx[g.home]] = 1.0
                    x[2 + n + idx[g.away]] = -1.0
                    x[-2], x[-1] = hb, hr
                    y = g.home_pts
                else:
                    x[2 + idx[g.away]] = 1.0
                    x[2 + n + idx[g.home]] = -1.0
                    x[-2], x[-1] = ab, ar
                    y = g.away_pts
                rows.append(x); ys.append(y); ws.append(w)
        X, y, w = np.array(rows), np.array(ys, float), np.array(ws)
        pen = np.full(k, self.ridge)
        pen[0] = pen[1] = pen[-1] = pen[-2] = 1e-6
        A = X.T @ (X * w[:, None]) + np.diag(pen)
        self.coef = np.linalg.solve(A, X.T @ (w * y))
        self._idx, self._n = idx, n
        # Residual spreads on the recent window, weighted like the fit.
        res = y - X @ self.coef
        rh, ra, wg = res[0::2], res[1::2], w[0::2]
        self.sigma_side = float(math.sqrt(np.average(res ** 2, weights=w)))
        self.sigma_margin = float(math.sqrt(np.average((rh - ra) ** 2, weights=wg)))
        self.sigma_total = float(math.sqrt(np.average((rh + ra) ** 2, weights=wg)))
        return self

    def rating(self, team: str) -> tuple[float, float]:
        i = self._idx.get(team)
        if i is None:
            return 0.0, 0.0
        return float(self.coef[2 + i]), float(self.coef[2 + self._n + i])

    def predict(self, home: str, away: str, flags: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Prediction:
        c = self.coef
        ah, dh = self.rating(home)
        aa, da = self.rating(away)
        hb, hr, ab, ar = flags
        hp = c[0] + c[1] + ah - da + c[-2] * hb + c[-1] * hr
        ap = c[0] + aa - dh + c[-2] * ab + c[-1] * ar
        return Prediction(float(hp), float(ap), self.sigma_margin, self.sigma_total, self.sigma_side)
