"""Tennis player ratings: Elo by surface (ATP and WTA rated separately by the caller).

Each player has an overall rating and one per surface (hard, clay, grass; indoor hard counts as hard). A match is
predicted from a blend of the two (SURFACE_WEIGHT), the usual way surface Elo is used. The update step shrinks
with experience (K = K0 / (games + OFFSET) ** SHAPE, the FiveThirtyEight form), so a newcomer moves fast and an
established player slowly.

From the match-win chance to the other markets:
  * set-level: p_set solved from p_match (best of 3 or 5) by bisection, then the exact distribution of set scores
    (2-0, 2-1, ... or 3-0 ... 3-2) -> "nombre de sets", "score exact en sets";
  * games: each player's chance of holding serve from p_set by a simple symmetric model (HOLD_BASE), then the games of
    a set by simulation-free recursion -> total games and games handicap.

Backtested in scripts/backtest_tennis.py once results are available. Not beating bookmakers by itself.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

START = 1500.0
K0, OFFSET, SHAPE = 250.0, 5.0, 0.4
SURFACE_WEIGHT = 0.5
SURFACES = ("hard", "clay", "grass")


def surface_of(name: str | None) -> str:
    s = (name or "").lower()
    if "clay" in s or "terre" in s:
        return "clay"
    if "grass" in s or "gazon" in s:
        return "grass"
    return "hard"


def expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


@dataclass
class Player:
    rating: float = START
    games: int = 0
    surf: dict = field(default_factory=lambda: defaultdict(lambda: START))
    surf_games: dict = field(default_factory=lambda: defaultdict(int))


class SurfaceElo:
    def __init__(self):
        self.players: dict[str, Player] = defaultdict(Player)

    def known(self, pid: str, min_matches: int = 10) -> bool:
        return self.players[pid].games >= min_matches

    def p_win(self, a: str, b: str, surface: str) -> float:
        pa, pb = self.players[a], self.players[b]
        overall = expected(pa.rating, pb.rating)
        surf = expected(pa.surf[surface], pb.surf[surface])
        return (1 - SURFACE_WEIGHT) * overall + SURFACE_WEIGHT * surf

    def update(self, winner: str, loser: str, surface: str) -> None:
        w, l = self.players[winner], self.players[loser]
        e = expected(w.rating, l.rating)
        kw, kl = K0 / (w.games + OFFSET) ** SHAPE, K0 / (l.games + OFFSET) ** SHAPE
        w.rating += kw * (1 - e)
        l.rating -= kl * (1 - e)
        es = expected(w.surf[surface], l.surf[surface])
        kws = K0 / (w.surf_games[surface] + OFFSET) ** SHAPE
        kls = K0 / (l.surf_games[surface] + OFFSET) ** SHAPE
        w.surf[surface] += kws * (1 - es)
        l.surf[surface] -= kls * (1 - es)
        w.games += 1
        l.games += 1
        w.surf_games[surface] += 1
        l.surf_games[surface] += 1


# ---------------------------------------------------------------- sets

def match_from_set(p: float, best_of: int) -> float:
    need = best_of // 2 + 1
    return sum(math.comb(need - 1 + k, k) * p ** need * (1 - p) ** k for k in range(need))


def set_from_match(pm: float, best_of: int) -> float:
    lo, hi = 1e-6, 1 - 1e-6
    for _ in range(60):
        mid = (lo + hi) / 2
        if match_from_set(mid, best_of) < pm:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def set_scores(pm: float, best_of: int) -> dict[str, float]:
    """{"2-0": p, "2-1": p, "1-2": p, "0-2": p} (or 3-x) for player A."""
    p = set_from_match(pm, best_of)
    need = best_of // 2 + 1
    out = {}
    for k in range(need):
        c = math.comb(need - 1 + k, k)
        out[f"{need}-{k}"] = c * p ** need * (1 - p) ** k
        out[f"{k}-{need}"] = c * (1 - p) ** need * p ** k
    return out


# ---------------------------------------------------------------- games

# Fitted on the 2024-07 -> 2026-10 backtest (Brier of the bookmakers' total-games lines): with the real hold rates
# (~0.80 / 0.64) and the Elo set chance, matches came out ~1.5 games too long, because real matches are more
# one-sided than independent sets suggest. For the games only, the set chance is sharpened (logit * SET_SHARPEN)
# and the hold level lowered; the winner keeps the plain Elo chance.
HOLD_BASE = {"ATP": 0.72, "WTA": 0.58}
SET_SHARPEN = {"ATP": 1.5, "WTA": 2.0}


@lru_cache(maxsize=4096)
def _set_games(ha: float, hb: float) -> tuple[tuple[tuple[int, int], float], ...]:
    """Distribution of a set's final score (games of A, games of B), A serving first, tie-break at 6-6 (A wins it
    with chance 0.5 + (ha - hb))."""
    dist: dict[tuple[int, int], float] = defaultdict(float)
    states = {(0, 0): 1.0}
    while states:
        nxt: dict[tuple[int, int], float] = defaultdict(float)
        for (a, b), pr in states.items():
            if (a >= 6 or b >= 6) and abs(a - b) >= 2 or a == 7 or b == 7:
                dist[(a, b)] += pr
                continue
            if a == 6 and b == 6:
                ptb = min(0.95, max(0.05, 0.5 + (ha - hb)))
                dist[(7, 6)] += pr * ptb
                dist[(6, 7)] += pr * (1 - ptb)
                continue
            a_serves = (a + b) % 2 == 0
            pa = ha if a_serves else 1 - hb
            nxt[(a + 1, b)] += pr * pa
            nxt[(a, b + 1)] += pr * (1 - pa)
        states = nxt
    return tuple(sorted(dist.items()))


def holds_from_set(p_set: float, tour: str, base: float | None = None) -> tuple[float, float]:
    """Hold chances (A, B) around `base` (default: the tour's average) whose set-win chance for A is p_set
    (bisection on the gap). The serve model (serve.py) gives the base of a given pair of players."""
    base = base or HOLD_BASE.get(tour, 0.75)
    lo, hi = -0.3, 0.3
    for _ in range(40):
        d = (lo + hi) / 2
        ha, hb = min(0.99, base + d), max(0.01, base - d)
        ps = sum(pr for (a, b), pr in _set_games(round(ha, 3), round(hb, 3)) if a > b)
        if ps < p_set:
            lo = d
        else:
            hi = d
    d = (lo + hi) / 2
    return min(0.99, base + d), max(0.01, base - d)


def games_distribution(pm: float, best_of: int, tour: str, base: float | None = None) -> dict[tuple[int, int], float]:
    """Total games of A and of B over the match (sets played until one reaches best_of // 2 + 1)."""
    p_set = min(max(set_from_match(pm, best_of), 1e-4), 1 - 1e-4)
    k = SET_SHARPEN.get(tour, 1.0)
    p_set = 1 / (1 + (p_set / (1 - p_set)) ** -k)
    ha, hb = holds_from_set(p_set, tour, round(base, 3) if base else None)
    sd = _set_games(round(ha, 3), round(hb, 3))
    need = best_of // 2 + 1
    states = {(0, 0, 0, 0): 1.0}  # sets A, sets B, games A, games B
    out: dict[tuple[int, int], float] = defaultdict(float)
    while states:
        nxt: dict = defaultdict(float)
        for (sa, sb, ga, gb), pr in states.items():
            if sa == need or sb == need:
                out[(ga, gb)] += pr
                continue
            for (a, b), q in sd:
                nxt[(sa + (a > b), sb + (b > a), ga + a, gb + b)] += pr * q
        states = nxt
    return dict(out)


def p_total_games_over(pm: float, best_of: int, tour: str, line: float) -> float:
    return sum(p for (a, b), p in games_distribution(pm, best_of, tour).items() if a + b > line)


def p_games_handicap(pm: float, best_of: int, tour: str, handicap: float) -> float:
    """P(games of A + handicap > games of B)."""
    return sum(p for (a, b), p in games_distribution(pm, best_of, tour).items() if a + handicap > b)
