"""Half-time markets (owner, 2026-10-27: "il y a énormément de cotes et toutes doivent être étudiées").

The match's expected goals (lam, mu) are split between the two halves: the first half is two independent Poisson
laws of means f * lam and f * mu, the second half (1 - f) * lam and (1 - f) * mu, where f is the league's share
of goals scored before half-time (FIRST_HALF_SHARE, 2023-2026). The two halves are independent, so every bet on
the half-time score, the second half, half-time / full-time or "both halves" is priced from the joint law of the
four numbers (h1, a1, h2, a2), and settled on the same weights. Backtest: docs/17-mi-temps.md.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.stats import poisson

from sportai.engine.value import Outcome

G = 7  # goals per team and per half: 0..6 (P(7 or more in a half) is negligible)
# Share of the goals scored in the first half over the 3 seasons up to 2026-10 (scripts/backtest_halftime.py).
FIRST_HALF_SHARE = {"FR1": 0.436, "EN1": 0.438, "ES1": 0.439, "DE1": 0.456, "IT1": 0.428, "BR1": 0.450}
DEFAULT_SHARE = 0.44

# Validated by the walk-forward backtest on 2 583 matches of 2025-2026 (Brier and log-loss better than the league
# frequency, 95 % interval below 0, no league worse by more than 0.002): docs/17-mi-temps.md. Every other half
# bet is "non validé".
VALIDATED = {
    ("ht_1x2", "home", None), ("ht_1x2", "away", None), ("2h_1x2", "home", None), ("2h_1x2", "away", None),
    ("ht_dc", "1X", None), ("ht_dc", "X2", None), ("2h_dc", "1X", None), ("2h_dc", "X2", None),
    ("ht_team_total_home", "over", 1.5), ("ht_team_total_home", "under", 1.5),
    ("ht_team_total_away", "over", 0.5), ("ht_team_total_away", "under", 0.5),
    ("ht_team_total_away", "over", 1.5), ("ht_team_total_away", "under", 1.5),
    ("2h_team_total_home", "over", 0.5), ("2h_team_total_home", "under", 0.5),
    ("2h_team_total_home", "over", 1.5), ("2h_team_total_home", "under", 1.5),
    ("2h_team_total_away", "over", 0.5), ("2h_team_total_away", "under", 0.5),
    ("htft", "home/home", None), ("htft", "draw/home", None), ("htft", "draw/away", None), ("htft", "away/away", None),
    ("team_both_halves_home", "yes", None), ("team_both_halves_away", "yes", None),
    ("win_both_halves_home", "yes", None),
    ("win_either_half_home", "yes", None), ("win_either_half_away", "yes", None),
}


def validated(market: str, sel: str, line: float | None) -> bool:
    return (market, sel, line) in VALIDATED


def joint(lam: float, mu: float, f: float) -> np.ndarray:
    """P[h1, a1, h2, a2]."""
    k = np.arange(G)
    m1 = np.outer(poisson.pmf(k, f * lam), poisson.pmf(k, f * mu))
    m2 = np.outer(poisson.pmf(k, (1 - f) * lam), poisson.pmf(k, (1 - f) * mu))
    j = np.einsum("ab,cd->abcd", m1, m2)
    return j / j.sum()


def _yn(sel: str) -> bool:
    return {"yes": True, "no": False}[sel]


@lru_cache(maxsize=4096)
def weights(market: str, sel: str, line: float | None) -> tuple[np.ndarray, np.ndarray]:
    """(win, push) weights over (h1, a1, h2, a2). ValueError for an unknown or unpriced bet (quarter lines)."""
    h1, a1, h2, a2 = np.indices((G, G, G, G))
    push = np.zeros(h1.shape, dtype=bool)
    half, _, kind = market.partition("_")
    if half in ("ht", "2h"):
        h, a = (h1, a1) if half == "ht" else (h2, a2)
        g = h + a
        sides = {"home": h > a, "draw": h == a, "away": h < a}
        if kind == "1x2":
            win = sides[sel]
        elif kind == "dc":
            win = {"1X": h >= a, "X2": h <= a, "12": h != a}[sel]
        elif kind == "dnb":
            win, push = sides[sel], h == a
        elif kind in ("totals", "team_total_home", "team_total_away"):
            if line is None or (line * 2) % 1:
                raise ValueError(f"{market} needs a half or whole line")
            x = {"totals": g, "team_total_home": h, "team_total_away": a}[kind]
            d = {"over": x - line, "under": line - x}[sel]
            win, push = d > 0, d == 0
        elif kind == "btts":
            win = ((h > 0) & (a > 0)) == _yn(sel)
        elif kind == "exact_score":
            x, y = (int(v) for v in sel.split(":"))
            win = (h == x) & (a == y)
        elif kind == "exact_goals":
            win = (g >= int(sel[:-1])) if sel.endswith("+") else (g == int(sel))
        elif kind == "odd_even":
            win = (g % 2 == 1) == {"odd": True, "even": False}[sel]
        else:
            raise ValueError(f"unsupported market {market}")
    else:
        ft = {"home": h1 + h2 > a1 + a2, "draw": h1 + h2 == a1 + a2, "away": h1 + h2 < a1 + a2}
        ht = {"home": h1 > a1, "draw": h1 == a1, "away": h1 < a1}
        sh = {"home": h2 > a2, "draw": h2 == a2, "away": h2 < a2}
        g1, g2 = h1 + a1, h2 + a2
        if market == "htft":
            x, _, y = sel.partition("/")
            win = ht[x] & ft[y]
        elif market == "highest_half":
            win = {"1st": g1 > g2, "2nd": g2 > g1, "draw": g1 == g2}[sel]
        elif market == "goal_both_halves":
            win = ((g1 > 0) & (g2 > 0)) == _yn(sel)
        elif market in ("team_both_halves_home", "team_both_halves_away"):
            x, y = (h1, h2) if market.endswith("home") else (a1, a2)
            win = ((x > 0) & (y > 0)) == _yn(sel)
        elif market in ("win_both_halves_home", "win_both_halves_away"):
            s = "home" if market.endswith("home") else "away"
            win = (ht[s] & sh[s]) == _yn(sel)
        elif market in ("win_either_half_home", "win_either_half_away"):
            s = "home" if market.endswith("home") else "away"
            win = (ht[s] | sh[s]) == _yn(sel)
        elif market == "btts_both_halves":
            win = ((h1 > 0) & (a1 > 0) & (h2 > 0) & (a2 > 0)) == _yn(sel)
        else:
            raise ValueError(f"unsupported market {market}")
    w, p = np.array(win, dtype=float), np.array(push, dtype=float)
    w.setflags(write=False)
    p.setflags(write=False)
    return w, p


def outcome(j: np.ndarray, market: str, sel: str, line: float | None = None) -> Outcome:
    w, p = weights(market, sel, line)
    win = min(max(float((j * w).sum()), 0.0), 1.0)
    return Outcome(win=win, push=min(max(float((j * p).sum()), 0.0), 1.0 - win))


def settle(market: str, sel: str, line: float | None, h1: int, a1: int, h2: int, a2: int) -> str:
    """gagne, perdu or rembourse, from the half-time and second-half scores."""
    if max(h1, a1, h2, a2) >= G:  # beyond the grid: recompute on the actual numbers
        h1, a1, h2, a2 = (min(x, G - 1) for x in (h1, a1, h2, a2))
    w, p = weights(market, sel, line)
    return "gagne" if w[h1, a1, h2, a2] else "rembourse" if p[h1, a1, h2, a2] else "perdu"


# Complete groups (every outcome covered), to remove the bookmaker margin; the others use 1 / odds.
GROUPS = {
    "1x2": ("home", "draw", "away"), "dnb": ("home", "away"), "totals": ("over", "under"),
    "team_total_home": ("over", "under"), "team_total_away": ("over", "under"), "btts": ("yes", "no"),
    "odd_even": ("odd", "even"), "highest_half": ("1st", "2nd", "draw"),
    "htft": tuple(f"{x}/{y}" for x in ("home", "draw", "away") for y in ("home", "draw", "away")),
}


def group(market: str) -> tuple[str, ...] | None:
    kind = market.partition("_")[2] if market[:3] in ("ht_", "2h_") else market
    if kind in GROUPS:
        return GROUPS[kind]
    if market.startswith(("goal_both", "team_both", "win_both", "win_either", "btts_both")):
        return ("yes", "no")
    return None


# ------------------------------------------------------------------------------------------- the odds feed

def _num(s: str) -> float:
    return float(s.replace("+", ""))


def _choice(market, table):
    return lambda v: (market, table[v], None) if v in table else None


def _ou(market):
    def f(v):
        side, _, line = v.partition(" ")
        if side not in ("Over", "Under") or not line:
            return None
        x = _num(line)
        return None if (x * 2) % 1 else (market, side.lower(), x)
    return f


def _score(market):
    def f(v):
        h, _, a = v.partition(":")
        return (market, f"{int(h)}:{int(a)}", None) if h.isdigit() and a.isdigit() else None
    return f


def _exact(market):
    def f(v):
        if v.isdigit():
            return market, v, None
        if v.startswith("more ") and v[5:].isdigit():
            return market, f"{v[5:]}+", None
        return None
    return f


def _yes_no(market):
    return _choice(market, {"Yes": "yes", "No": "no"})


def _team_scores(market):
    return lambda v: {"Yes": (market, "over", 0.5), "No": (market, "under", 0.5)}.get(v)


def _side_yes(prefix):
    return lambda v: {"Home": (f"{prefix}_home", "yes", None), "Away": (f"{prefix}_away", "yes", None)}.get(v)


def _htft(v):
    x, _, y = v.partition("/")
    t = {"Home": "home", "Draw": "draw", "Away": "away"}
    return ("htft", f"{t[x]}/{t[y]}", None) if x in t and y in t else None


RES = {"Home": "home", "Draw": "draw", "Away": "away"}
DC = {"Home/Draw": "1X", "Draw/Away": "X2", "Home/Away": "12"}
OE = {"Odd": "odd", "Even": "even"}
# API-Football bet name -> parser of one value into (market, selection, line). Checked on the feed, 2026-10-27.
HALF_BETS = {
    "First Half Winner": _choice("ht_1x2", RES), "Second Half Winner": _choice("2h_1x2", RES),
    "Double Chance - First Half": _choice("ht_dc", DC), "Double Chance - Second Half": _choice("2h_dc", DC),
    "Draw No Bet (1st Half)": _choice("ht_dnb", {"Home": "home", "Away": "away"}),
    "Draw No Bet (2nd Half)": _choice("2h_dnb", {"Home": "home", "Away": "away"}),
    "Goals Over/Under First Half": _ou("ht_totals"), "Goal Line (1st Half)": _ou("ht_totals"),
    "Goals Over/Under - Second Half": _ou("2h_totals"),
    "Home Team Total Goals(1st Half)": _ou("ht_team_total_home"),
    "Away Team Total Goals(1st Half)": _ou("ht_team_total_away"),
    "Home Team Total Goals(2nd Half)": _ou("2h_team_total_home"),
    "Away Team Total Goals(2nd Half)": _ou("2h_team_total_away"),
    "Home Team Score a Goal (1st Half)": _team_scores("ht_team_total_home"),
    "Away Team Score a Goal (1st Half)": _team_scores("ht_team_total_away"),
    "Home Team Score a Goal (2nd Half)": _team_scores("2h_team_total_home"),
    "Away Team Score a Goal (2nd Half)": _team_scores("2h_team_total_away"),
    "Both Teams Score - First Half": _yes_no("ht_btts"), "Both Teams To Score - Second Half": _yes_no("2h_btts"),
    "Correct Score - First Half": _score("ht_exact_score"), "Correct Score - Second Half": _score("2h_exact_score"),
    "Exact Goals Number - First Half": _exact("ht_exact_goals"),
    "Second Half Exact Goals Number": _exact("2h_exact_goals"),
    "Odd/Even - First Half": _choice("ht_odd_even", OE), "Odd/Even - Second Half": _choice("2h_odd_even", OE),
    "HT/FT Double": _htft,
    "Highest Scoring Half": _choice("highest_half", {"1st Half": "1st", "2nd Half": "2nd", "Draw": "draw"}),
    "To Score in Both Halves": _yes_no("goal_both_halves"),
    "Home team will score in both halves": _yes_no("team_both_halves_home"),
    "Away team will score in both halves": _yes_no("team_both_halves_away"),
    "To Score In Both Halves By Teams": _side_yes("team_both_halves"),
    "Win Both Halves": _side_yes("win_both_halves"),
    "Home win both halves": _yes_no("win_both_halves_home"),
    "Away win both halves": _yes_no("win_both_halves_away"),
    "To Win Either Half": _side_yes("win_either_half"),
    "Both Teams To Score in Both Halves": _yes_no("btts_both_halves"),
}


def map_value(bet: str, value) -> tuple[str, str, float | None] | None:
    f = HALF_BETS.get(bet)
    if f is None:
        return None
    try:
        return f(str(value).strip())
    except (ValueError, KeyError):
        return None


# ------------------------------------------------------------------------------------------- French labels

def label(market: str, sel: str, line: float | None, home: str, away: str) -> str:
    team = {"home": home, "away": away}
    half = {"ht": "1re mi-temps", "2h": "2e mi-temps"}.get(market[:2])
    kind = market[3:] if half else market
    ln = "" if line is None else f"{line:g}".replace(".", ",")
    if half:
        if kind == "1x2":
            return f"{half} : nul" if sel == "draw" else f"{half} : {team[sel]} gagne"
        if kind == "dc":
            return f"{half} : " + {"1X": f"{home} ou nul", "X2": f"nul ou {away}", "12": f"{home} ou {away}"}[sel]
        if kind == "dnb":
            return f"{half} : {team[sel]} gagne (remboursé si nul)"
        if kind == "totals":
            return f"{half} : {'plus' if sel == 'over' else 'moins'} de {ln} buts"
        if kind.startswith("team_total"):
            t = home if kind.endswith("home") else away
            return f"{half} : {t} {'plus' if sel == 'over' else 'moins'} de {ln} buts"
        if kind == "btts":
            return f"{half} : les deux équipes marquent : {'oui' if sel == 'yes' else 'non'}"
        if kind == "exact_score":
            return f"{half} : score exact {sel.replace(':', '-')}"
        if kind == "exact_goals":
            return f"{half} : {sel.replace('+', ' buts ou plus') if sel.endswith('+') else sel + ' but(s)'}"
        if kind == "odd_even":
            return f"{half} : nombre de buts {'impair' if sel == 'odd' else 'pair'}"
    if market == "htft":
        x, _, y = sel.partition("/")
        name = {"home": home, "away": away, "draw": "nul"}
        return f"Mi-temps / fin de match : {name[x]} / {name[y]}"
    if market == "highest_half":
        return {"1st": "Plus de buts en 1re mi-temps", "2nd": "Plus de buts en 2e mi-temps",
                "draw": "Autant de buts dans chaque mi-temps"}[sel]
    yn = "" if sel == "yes" else " : non"
    if market == "goal_both_halves":
        return "But dans chaque mi-temps" + yn
    if market.startswith("team_both_halves"):
        return f"{team[market.rsplit('_', 1)[1]]} marque dans chaque mi-temps" + yn
    if market.startswith("win_both_halves"):
        return f"{team[market.rsplit('_', 1)[1]]} gagne les deux mi-temps" + yn
    if market.startswith("win_either_half"):
        return f"{team[market.rsplit('_', 1)[1]]} gagne au moins une mi-temps" + yn
    if market == "btts_both_halves":
        return "Les deux équipes marquent dans chaque mi-temps" + yn
    return f"{market} {sel}"


MARKET_NAME = "Mi-temps"
