"""Count markets of a match: corners, cards, shots (owner, 2026-10-27: "toutes les cotes doivent être étudiées").

Each team's expected count is its own average "for" (home matches for the home side, away matches for the away
side) times the opponent's average "against" (its away / home matches), over the league average; averages over the
last LAST matches of each kind, shrunk towards the league average (SHRINK matches of prior). The count of each team
follows a negative binomial law of that mean, the two teams independent; the dispersion r is fitted on the past
residuals of the league. Backtest: scripts/backtest_counts.py, docs/18-corners-cartons-tirs.md.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime

import numpy as np
from scipy.stats import nbinom

LAST = 19
SHRINK = 5.0
MAX_N = 60  # counts 0..59 per team

_side = {"home": lambda h, a: h > a, "draw": lambda h, a: h == a, "away": lambda h, a: h < a}


def _markets(total_lines, team_lines):
    out = [(f"total:over@{x:g}", (lambda x: lambda h, a: h + a > x)(x)) for x in total_lines]
    out += [(f"total:under@{x:g}", (lambda x: lambda h, a: h + a < x)(x)) for x in total_lines]
    for t in ("home", "away"):
        for x in team_lines:
            out.append((f"{t}:over@{x:g}", (lambda t, x: lambda h, a: (h if t == "home" else a) > x)(t, x)))
            out.append((f"{t}:under@{x:g}", (lambda t, x: lambda h, a: (h if t == "home" else a) < x)(t, x)))
    out += [(f"1x2:{s}", _side[s]) for s in ("home", "draw", "away")]
    return out


# feed: the API-Football statistic types summed for the count (a missing value means 0 for cards).
STATS = {
    "corners": {"feed": ("Corner Kicks",), "markets": _markets((7.5, 8.5, 9.5, 10.5, 11.5, 12.5), (2.5, 3.5, 4.5, 5.5, 6.5))},
    "cartons": {"feed": ("Yellow Cards", "Red Cards"), "markets": _markets((2.5, 3.5, 4.5, 5.5, 6.5), (0.5, 1.5, 2.5, 3.5))},
    "jaunes": {"feed": ("Yellow Cards",), "markets": _markets((2.5, 3.5, 4.5, 5.5, 6.5), (0.5, 1.5, 2.5, 3.5))},
    "tirs": {"feed": ("Total Shots",), "markets": _markets((20.5, 22.5, 24.5, 26.5, 28.5), (8.5, 10.5, 12.5, 14.5))},
    "tirs_cadres": {"feed": ("Shots on Goal",), "markets": _markets((6.5, 7.5, 8.5, 9.5, 10.5), (2.5, 3.5, 4.5, 5.5))},
    "fautes": {"feed": ("Fouls",), "markets": _markets((20.5, 22.5, 24.5, 26.5), (9.5, 10.5, 11.5, 12.5))},
}


def pmf(mean: float, r: float) -> np.ndarray:
    k = np.arange(MAX_N)
    p = nbinom.pmf(k, r, r / (r + max(mean, 1e-6)))
    return p / p.sum()


def matrix(mh: float, ma: float, r: float) -> np.ndarray:
    """P[home count, away count]."""
    return np.outer(pmf(mh, r), pmf(ma, r))


def win_prob(kind: str, sel: str, line: float | None, mh: float, ma: float, r: float, r_total: float | None = None,
             total_mean: float | None = None) -> float:
    """The chance of one bet: kind "total" (the match), "home" / "away" (a team's count), "1x2" (who has more)."""
    k = np.arange(MAX_N)
    if kind == "1x2":
        m = matrix(mh, ma, r)
        h, a = np.indices(m.shape)
        return float(m[_side[sel](h, a)].sum())
    if kind == "total":
        law = pmf(total_mean if total_mean is not None else mh + ma, r_total) if r_total else np.convolve(
            pmf(mh, r), pmf(ma, r))[:MAX_N]
    else:
        law = pmf(mh if kind == "home" else ma, r)
    return float(law[k > line].sum() if sel == "over" else law[k < line].sum())


def probs_for(stat: str, mh: float, ma: float, r: float, r_total: float | None = None,
              total_mean: float | None = None) -> list[float]:
    """Totals from their own negative binomial (the two teams' counts are not independent: a team that dominates
    takes corners the other does not), team counts and "who has more" from the two teams' laws."""
    out = []
    for name, _ in STATS[stat]["markets"]:
        kind, sel, line = parse(name)
        out.append(win_prob(kind, sel, line, mh, ma, r, r_total, total_mean))
    return out


def parse(name: str) -> tuple[str, str, float | None]:
    """"total:over@9.5" -> ("total", "over", 9.5)."""
    kind, _, rest = name.partition(":")
    sel, _, x = rest.partition("@")
    return kind, sel, float(x) if x else None


def key(kind: str, sel: str, line: float | None) -> str:
    return f"{kind}:{sel}" + ("" if line is None else f"@{line:g}")


# Backtest of 2026-10-27 (docs/18-corners-cartons-tirs.md, 2,614 matches of 2025 and 2026): every tested bet beat
# the league frequency except these; a line the backtest did not test is "non validé" too.
NOT_VALIDATED = {
    "corners": {"total:over@7.5", "total:under@7.5", "total:over@9.5", "total:under@9.5", "total:over@11.5",
                "total:under@11.5", "1x2:draw"},
    "cartons": {"1x2:home", "1x2:draw"},
    "jaunes": {"away:over@3.5", "away:under@3.5", "1x2:home", "1x2:draw"},
    "tirs": {"total:over@20.5", "total:under@20.5", "total:over@22.5", "total:under@22.5", "1x2:draw"},
    "fautes": set(),  # backtest of 2026-10-28: every tested fouls bet beats the league frequency
    "tirs_cadres": {"total:over@8.5", "total:under@8.5", "total:over@9.5", "total:under@9.5", "total:over@10.5",
                    "total:under@10.5"},
}


def validated(stat: str, kind: str, sel: str, line: float | None) -> bool:
    k = key(kind, sel, line)
    return (stat in NOT_VALIDATED and k not in NOT_VALIDATED[stat]
            and k in {n for n, _ in STATS[stat]["markets"]})


# The odds feed's bets (API-Football names) -> (statistic, kind). Values: "Over 9.5" / "Under 9.5", "Home" / "Draw"
# / "Away"; whole and quarter lines are left out (refunds).
FEED = {
    "Corners Over Under": ("corners", "total"), "Home Corners Over/Under": ("corners", "home"),
    "Away Corners Over/Under": ("corners", "away"), "Corners 1x2": ("corners", "1x2"),
    "Cards Over/Under": ("cartons", "total"), "Home Team Total Cards": ("cartons", "home"),
    "Away Team Total Cards": ("cartons", "away"),
    "Yellow Over/Under": ("jaunes", "total"), "Home Team Yellow Cards": ("jaunes", "home"),
    "Away Team Yellow Cards": ("jaunes", "away"),
    "Total Shots": ("tirs", "total"), "Shots.1x2": ("tirs", "1x2"),
    "Total ShotOnGoal": ("tirs_cadres", "total"), "ShotOnTarget 1x2": ("tirs_cadres", "1x2"),
    "Fouls. Total": ("fautes", "total"), "Fouls. Home Total": ("fautes", "home"), "Fouls. Away Total": ("fautes", "away"),
    "Fouls. 1x2": ("fautes", "1x2"),
}
NAMES = {"corners": "Corners", "cartons": "Cartons", "jaunes": "Cartons jaunes", "tirs": "Tirs",
         "tirs_cadres": "Tirs cadrés", "fautes": "Fautes"}
GROUPS = {"1x2": ("home", "draw", "away")}


def map_value(bet: str, value) -> tuple[str, str, str, float | None] | None:
    """A feed value -> (statistic, kind, selection, line), or None."""
    f = FEED.get(bet)
    if f is None or value is None:
        return None
    stat, kind = f
    v = str(value).strip()
    if kind == "1x2":
        sel = {"Home": "home", "Draw": "draw", "Away": "away"}.get(v)
        return (stat, kind, sel, None) if sel else None
    word, _, x = v.partition(" ")
    sel = {"Over": "over", "Under": "under"}.get(word)
    try:
        line = float(x)
    except ValueError:
        return None
    if sel is None or line % 1 != 0.5:
        return None
    return stat, kind, sel, line


def label(stat: str, kind: str, sel: str, line: float | None, home: str, away: str) -> str:
    name = NAMES[stat]
    if kind == "1x2":
        if sel == "draw":
            return f"{name} : autant pour les deux équipes"
        return f"Plus de {name.lower()} : {home if sel == 'home' else away}"
    x = f"{line:g}".replace(".", ",")
    word = "plus" if sel == "over" else "moins"
    if kind == "total":
        return f"{name} : {word} de {x} dans le match"
    return f"{name} {home if kind == 'home' else away} : {word} de {x}"


def settle(kind: str, sel: str, line: float | None, h: int, a: int) -> str:
    if kind == "1x2":
        return "gagne" if _side[sel](h, a) else "perdu"
    n = h + a if kind == "total" else h if kind == "home" else a
    return "gagne" if (n > line if sel == "over" else n < line) else "perdu"


# The referee (owner 2026-10-28, "est-ce que la Top confiance tient compte de l'arbitre ?"): both teams' expected
# cards and the total are multiplied by the referee's cards per match in the league, pulled towards the league
# average by REFEREE_SHRINK matches, over that average. Backtest scripts/backtest_referee.py on the same 2,614
# matches: better on 14 of the 29 cards bets, worse on none (docs/18-corners-cartons-tirs.md). Extended 2026-10-28
# ("l'arbitre doit toujours être pris en compte") to the statistics where it also does better and never worse: fouls
# (7 of 27) and corners (4 of 35); shots are worse with it (6 of 29), shots on target unchanged: left out.
REFEREE_SHRINK = 20.0
REFEREE_STATS = ("cartons", "jaunes", "fautes", "corners")


def referee_factor(cards: float, matches: int, league_mean: float) -> float:
    if not league_mean:
        return 1.0
    return (cards + REFEREE_SHRINK * league_mean) / (matches + REFEREE_SHRINK) / league_mean


class CountModel:
    """Walk-forward team averages of one count statistic for one league."""

    def __init__(self):
        self.hf = defaultdict(lambda: deque(maxlen=LAST))  # home team, at home: (for, against)
        self.af = defaultdict(lambda: deque(maxlen=LAST))  # away team, away
        self.lh = self.la = None  # league averages per match (home side, away side)
        self.r = self.r_total = 10.0
        self.beta = self.beta_h = self.beta_a = 0.5
        self._res: list[tuple[float, float]] = []  # (predicted mean, observed) for the dispersion
        self._tot: list[tuple[float, float]] = []

    def refit(self, past: list[dict]) -> None:
        if past:
            self.lh = float(np.mean([m["h"] for m in past]))
            self.la = float(np.mean([m["a"] for m in past]))
        self.beta = self._slope(self._tot[-2000:])
        self.beta_h = self._slope(self._res[-4000:][0::2])  # home side, then away side
        self.beta_a = self._slope(self._res[-4000:][1::2])
        res = self._res[-4000:]
        shrunk = [((self.lh + self.beta_h * (x - self.lh)) if k % 2 == 0 else (self.la + self.beta_a * (x - self.la)), y)
                  for k, (x, y) in enumerate(res)] if self.lh is not None else res
        self.r = self._disp(shrunk, self.r)
        self.r_total = self._disp([(self.total_mean(p), y) for p, y in self._tot[-2000:]], self.r_total)

    def _slope(self, res) -> float:
        """How much of the predicted total's spread is real: observed total regressed on the predicted one."""
        if len(res) < 200:
            return 0.5
        x = np.array([r[0] for r in res])
        y = np.array([r[1] for r in res])
        v = float(x.var())
        return float(np.clip(np.cov(x, y, bias=True)[0, 1] / v, 0.0, 1.0)) if v > 0 else 0.0

    def total_mean(self, pred_total: float) -> float:
        base = (self.lh or 0) + (self.la or 0)
        return base + self.beta * (pred_total - base)

    @staticmethod
    def _disp(res, default: float) -> float:
        if len(res) < 200:
            return default
        mu = np.array([x[0] for x in res])
        y = np.array([x[1] for x in res])
        excess = float(((y - mu) ** 2 - mu).sum())
        return float(np.clip((mu ** 2).sum() / excess, 2.0, 500.0)) if excess > 0 else 500.0

    def _avg(self, q, idx: int, prior: float) -> float:
        vals = [x[idx] for x in q]
        return (sum(vals) + SHRINK * prior) / (len(vals) + SHRINK)

    def expected(self, home: str, away: str, kickoff: datetime | None = None) -> tuple[float, float] | None:
        if self.lh is None or len(self.hf[home]) < 3 or len(self.af[away]) < 3:
            return None
        att_h, def_h = self._avg(self.hf[home], 0, self.lh), self._avg(self.hf[home], 1, self.la)
        att_a, def_a = self._avg(self.af[away], 0, self.la), self._avg(self.af[away], 1, self.lh)
        mh, ma = att_h * def_a / self.lh, att_a * def_h / self.la
        return self.lh + self.beta_h * (mh - self.lh), self.la + self.beta_a * (ma - self.la)

    def raw(self, home: str, away: str) -> tuple[float, float] | None:
        """The unshrunk means, which the slopes are fitted on."""
        if self.lh is None or len(self.hf[home]) < 3 or len(self.af[away]) < 3:
            return None
        att_h, def_h = self._avg(self.hf[home], 0, self.lh), self._avg(self.hf[home], 1, self.la)
        att_a, def_a = self._avg(self.af[away], 0, self.la), self._avg(self.af[away], 1, self.lh)
        return att_h * def_a / self.lh, att_a * def_h / self.la

    def add(self, m: dict) -> None:
        e = self.raw(m["home"], m["away"])
        if e is not None:
            self._res += [(e[0], m["h"]), (e[1], m["a"])]
            self._tot.append((e[0] + e[1], m["h"] + m["a"]))
        self.hf[m["home"]].append((m["h"], m["a"]))
        self.af[m["away"]].append((m["a"], m["h"]))
