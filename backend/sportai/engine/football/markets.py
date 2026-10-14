"""Football markets priced from a final-score probability matrix.

A matrix M[i, j] = P(home scores i, away scores j) prices every market below.
For live use, pass the matrix of *final* scores (current score plus remaining
goals) so the same code prices pre-match and in-play markets.

Each market is a pair of weight matrices over the scores (win, push): P(win) =
sum(M * W_win). The same weights settle a bet on a final score, so pricing and
settlement can never disagree. Markets that need more than the final score
(half-time, first scorer, corners, cards...) are not priced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from sportai.engine.value import Outcome


@dataclass(frozen=True)
class MarketKey:
    market: str  # e.g. "1x2", "totals", "btts", "dnb", "double_chance", "asian_handicap"
    selection: str  # e.g. "home", "over", "yes", "1X", "home_over", "2:1"
    line: float | None = None

    def label(self) -> str:
        return f"{self.market}:{self.selection}" + (f"@{self.line:g}" if self.line is not None else "")


# Markets validated by the 2020-2026 backtest (docs/04-backtest.md); every other market below is priced from the
# same score matrix but "non validé" until a backtest against its odds says otherwise.
VALIDATED_MARKETS = ("1x2", "double_chance", "dnb")
# Every market a final-score matrix prices (owner, 2026-10-14: "Toutes les cotes doivent être analysées").
# Lines are the home side's handicap for "asian_handicap" / "euro_handicap" (the convention of API-Football and of
# most bookmakers' feeds); a quarter line (x.25, x.75) is the usual split bet: half the stake on each nearby line.
SUPPORTED_MARKETS = (
    "1x2", "double_chance", "dnb", "totals", "btts",
    "team_total_home", "team_total_away", "asian_handicap", "euro_handicap",
    "result_total", "result_btts", "total_btts", "clean_sheet_home", "clean_sheet_away",
    "win_to_nil_home", "win_to_nil_away", "odd_even", "odd_even_home", "odd_even_away",
    "exact_score", "exact_goals", "exact_goals_home", "exact_goals_away", "goal_range", "scoring_draw",
)
# Markets with many open-ended selections: no fixed complete group, the margin is removed over whatever one
# bookmaker offers, when its prices cover the whole outcome space (sum of 1/odds at least 1).
OPEN_MARKETS = ("exact_score", "exact_goals", "exact_goals_home", "exact_goals_away")
TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
LINE_MARKETS = ("totals", "team_total_home", "team_total_away", "asian_handicap")


def is_quarter(line: float | None) -> bool:
    return line is not None and (line * 4) % 2 == 1


def outcome(m: np.ndarray, key: MarketKey) -> Outcome:
    win_w, push_w = weights(key, m.shape[0])
    win = min(max(float((m * win_w).sum()), 0.0), 1.0)
    push = min(max(float((m * push_w).sum()), 0.0), 1.0 - win)
    return Outcome(win=win, push=push)


def outcomes(ms: np.ndarray, key: MarketKey) -> tuple[np.ndarray, np.ndarray]:
    """Win and push probabilities of one market over a stack of score matrices (n, g, g), vectorised."""
    win_w, push_w = weights(key, ms.shape[-1])
    return np.tensordot(ms, win_w, axes=([1, 2], [0, 1])), np.tensordot(ms, push_w, axes=([1, 2], [0, 1]))


def settle_score(key: MarketKey, hg: int, ag: int) -> str:
    """Result of a bet on a final score: gagne, perdu, rembourse, or demi_gagne / demi_perdu (quarter lines)."""
    win_w, push_w = weights(key, max(11, hg + 1, ag + 1))
    w, p = float(win_w[hg, ag]), float(push_w[hg, ag])
    if w == 1.0:
        return "gagne"
    if p == 1.0:
        return "rembourse"
    if w == 0.0 and p == 0.0:
        return "perdu"
    return "demi_gagne" if w > 0 else "demi_perdu"


def weights(key: MarketKey, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Matrices W_win, W_push over the scores (i home goals, j away goals). Raises ValueError for a key that is
    not a market of this module."""
    return _weights(key.market, key.selection, None if key.line is None else float(key.line), n)


@lru_cache(maxsize=16384)
def _weights(market: str, sel: str, line: float | None, n: int) -> tuple[np.ndarray, np.ndarray]:
    i, j = np.indices((n, n))
    goals = i + j
    if market in LINE_MARKETS:
        if line is None:
            raise ValueError(f"{market} market needs a line")
        if is_quarter(line):  # split bet: half the stake on each of the two nearest lines
            a, b = _weights(market, sel, line - 0.25, n), _weights(market, sel, line + 0.25, n)
            return _frozen((a[0] + b[0]) / 2), _frozen((a[1] + b[1]) / 2)
        if market == "asian_handicap":
            x = {"home": i - j + line, "away": j - i - line}[sel]
        else:
            g = {"totals": goals, "team_total_home": i, "team_total_away": j}[market]
            x = {"over": g - line, "under": line - g}[sel]
        return _frozen(x > 0), _frozen(x == 0)
    push = np.zeros((n, n), dtype=bool)
    home, draw, away = i > j, i == j, i < j
    sides = {"home": home, "draw": draw, "away": away}
    both = (i > 0) & (j > 0)
    if market == "1x2":
        win = sides[sel]
    elif market == "double_chance":
        win = {"1X": home | draw, "X2": draw | away, "12": home | away}[sel]
    elif market == "dnb":
        win, push = {"home": home, "away": away}[sel], draw
    elif market == "btts":
        win = both == {"yes": True, "no": False}[sel]
    elif market == "euro_handicap":
        if line is None or not line.is_integer():
            raise ValueError("euro_handicap needs a whole line")
        d = i - j + line
        win = {"home": d > 0, "draw": d == 0, "away": d < 0}[sel]
    elif market in ("result_total", "total_btts"):
        if line is None or line.is_integer():
            raise ValueError(f"{market} needs a half line")
        a, b = sel.split("_")
        over = {"over": goals > line, "under": goals < line}
        win = (sides[a] & over[b]) if market == "result_total" else (over[a] & (both == {"yes": True, "no": False}[b]))
    elif market == "result_btts":
        a, b = sel.split("_")
        win = sides[a] & (both == {"yes": True, "no": False}[b])
    elif market in ("clean_sheet_home", "clean_sheet_away"):
        win = ((j if market.endswith("home") else i) == 0) == {"yes": True, "no": False}[sel]
    elif market in ("win_to_nil_home", "win_to_nil_away"):
        nil = (home & (j == 0)) if market.endswith("home") else (away & (i == 0))
        win = nil == {"yes": True, "no": False}[sel]
    elif market in ("odd_even", "odd_even_home", "odd_even_away"):
        g = {"odd_even": goals, "odd_even_home": i, "odd_even_away": j}[market]
        win = (g % 2 == 1) == {"odd": True, "even": False}[sel]
    elif market == "exact_score":
        hg, ag = (int(x) for x in sel.split(":"))
        win = (i == hg) & (j == ag)
    elif market in ("exact_goals", "exact_goals_home", "exact_goals_away"):
        g = {"exact_goals": goals, "exact_goals_home": i, "exact_goals_away": j}[market]
        win = (g >= int(sel[:-1])) if sel.endswith("+") else (g == int(sel))
    elif market == "goal_range":  # "0-1", "2-3", "4+"
        lo, _, hi = sel.partition("-")
        win = (goals >= int(lo.rstrip("+"))) & ((goals <= int(hi)) if hi else True)
    elif market == "scoring_draw":
        win = (draw & (i > 0)) == {"yes": True, "no": False}[sel]
    else:
        raise ValueError(f"unsupported market {market}")
    return _frozen(win), _frozen(push)


def _frozen(a) -> np.ndarray:
    a = np.array(a, dtype=float)
    a.setflags(write=False)
    return a


def all_keys() -> list[MarketKey]:
    keys = [MarketKey("1x2", s) for s in ("home", "draw", "away")]
    keys += [MarketKey("double_chance", s) for s in ("1X", "X2", "12")]
    keys += [MarketKey("dnb", s) for s in ("home", "away")]
    keys += [MarketKey("btts", s) for s in ("yes", "no")]
    keys += [MarketKey("totals", s, line) for line in TOTAL_LINES for s in ("over", "under")]
    return keys


# Selections that share a complete market, used to remove the bookmaker margin.
_GROUPS = {
    "1x2": ("home", "draw", "away"), "dnb": ("home", "away"), "btts": ("yes", "no"),
    "totals": ("over", "under"), "team_total_home": ("over", "under"), "team_total_away": ("over", "under"),
    "asian_handicap": ("home", "away"), "euro_handicap": ("home", "draw", "away"),
    "result_total": tuple(f"{r}_{s}" for r in ("home", "draw", "away") for s in ("over", "under")),
    "result_btts": tuple(f"{r}_{s}" for r in ("home", "draw", "away") for s in ("yes", "no")),
    "total_btts": tuple(f"{a}_{b}" for a in ("over", "under") for b in ("yes", "no")),
    "clean_sheet_home": ("yes", "no"), "clean_sheet_away": ("yes", "no"),
    "win_to_nil_home": ("yes", "no"), "win_to_nil_away": ("yes", "no"),
    "odd_even": ("odd", "even"), "odd_even_home": ("odd", "even"), "odd_even_away": ("odd", "even"),
    "goal_range": ("0-1", "2-3", "4+"), "scoring_draw": ("yes", "no"),
}


def market_group(key: MarketKey) -> list[MarketKey]:
    sides = _GROUPS.get(key.market)
    if not sides:
        return []  # double chance overlaps; open markets are handled by the caller
    return [MarketKey(key.market, s, key.line) for s in sides]


def likely_score(m: np.ndarray) -> tuple[int, int, float]:
    """The estimated final score: the most likely outcome (home win, draw, away win) first, then the most likely
    exact score inside it. Taking the single most likely exact score instead calls a draw far too often: 1-1 is
    often the modal score even when one side is clearly favourite (owner, 2026-10-11; backtest in docs/04-backtest.md).
    A draw is only estimated when the draw is truly the most likely of the three outcomes."""
    i, j = np.indices(m.shape)
    masks = (i > j, i == j, i < j)
    best = max(range(3), key=lambda k: (float(m[masks[k]].sum()), k != 1))  # on an exact tie, not the draw
    inside = np.where(masks[best], m, -1.0)
    hg, ag = divmod(int(np.argmax(inside)), m.shape[1])
    return int(hg), int(ag), round(float(m[hg, ag]), 4)
