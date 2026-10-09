"""Football markets priced from a final-score probability matrix.

A matrix M[i, j] = P(home scores i, away scores j) prices every market below.
For live use, pass the matrix of *final* scores (current score plus remaining
goals) so the same code prices pre-match and in-play markets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sportai.engine.value import Outcome


@dataclass(frozen=True)
class MarketKey:
    market: str  # e.g. "1x2", "totals", "btts", "dnb", "double_chance"
    selection: str  # e.g. "home", "over", "yes", "1X"
    line: float | None = None

    def label(self) -> str:
        return f"{self.market}:{self.selection}" + (f"@{self.line:g}" if self.line is not None else "")


SUPPORTED_MARKETS = ("1x2", "double_chance", "dnb", "totals", "btts")
TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def outcome(m: np.ndarray, key: MarketKey) -> Outcome:
    n = m.shape[0]
    i, j = np.indices((n, n))
    home = float(m[i > j].sum())
    draw = float(m[i == j].sum())
    away = float(m[i < j].sum())

    if key.market == "1x2":
        return Outcome(win={"home": home, "draw": draw, "away": away}[key.selection])
    if key.market == "double_chance":
        return Outcome(win={"1X": home + draw, "X2": draw + away, "12": home + away}[key.selection])
    if key.market == "dnb":
        win = {"home": home, "away": away}[key.selection]
        return Outcome(win=win, push=draw)
    if key.market == "btts":
        yes = float(m[1:, 1:].sum())
        return Outcome(win=yes if key.selection == "yes" else 1.0 - yes)
    if key.market == "totals":
        if key.line is None:
            raise ValueError("totals market needs a line")
        return _total(m, i + j, key.selection, key.line)
    raise ValueError(f"unsupported market {key.market}")


def _total(m: np.ndarray, goals: np.ndarray, side: str, line: float) -> Outcome:
    # Whole lines (2.0, 3.0) refund on exactly that many goals; half lines never do.
    # Quarter lines are split bets and are deliberately not supported here.
    if (line * 4) % 2 == 1:
        raise ValueError("quarter lines are not supported")
    over = float(m[goals > line].sum())
    push = float(m[goals == line].sum()) if float(line).is_integer() else 0.0
    under = 1.0 - over - push
    return Outcome(win=over if side == "over" else under, push=push)


def all_keys() -> list[MarketKey]:
    keys = [MarketKey("1x2", s) for s in ("home", "draw", "away")]
    keys += [MarketKey("double_chance", s) for s in ("1X", "X2", "12")]
    keys += [MarketKey("dnb", s) for s in ("home", "away")]
    keys += [MarketKey("btts", s) for s in ("yes", "no")]
    keys += [MarketKey("totals", s, line) for line in TOTAL_LINES for s in ("over", "under")]
    return keys


# Selections that share a complete market, used to remove the bookmaker margin.
def market_group(key: MarketKey) -> list[MarketKey]:
    if key.market == "1x2":
        return [MarketKey("1x2", s) for s in ("home", "draw", "away")]
    if key.market in ("btts", "totals", "dnb"):
        sides = {"btts": ("yes", "no"), "totals": ("over", "under"), "dnb": ("home", "away")}[key.market]
        return [MarketKey(key.market, s, key.line) for s in sides]
    return []  # double chance overlaps; no clean complete market
