"""API-Football pre-match odds (GET /odds) parsed into OddsQuote, every bookmaker and every bet type.

The feed lists about ten bookmakers (Bet365, Pinnacle, Betfair, 1xBet...) and up to ~180 bet types per match.
Bet types whose outcome follows from the final score are mapped to internal market keys
(sportai.engine.football.markets) and priced by the model. The others (half-time, corners, cards, scorers,
minutes...) cannot be priced from a final-score model: they are kept as a short summary of their best odds,
marked "non analysé", never as quotes.

Conventions of the feed, checked on 2026-10-14 (Feyenoord - Como, 10 bookmakers):
- "Asian Handicap" and "Handicap Result" values carry the home side's handicap on both sides: "Home -1.25" at 5.75
  and "Away -1.25" at 1.14 are the two sides of the same line (home -1.25, away +1.25).
- "Exact Goals Number" ends with "more 6" or "more 7" after the plain numbers below it: "N or more".
- "Home/Away" is draw no bet; odds are strings, some values are integers.
- "update" is when the feed last refreshed the match's odds (about every 3 hours before kick-off).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sportai.connectors.base import OddsQuote

PROVIDER = "api-football"


def _num(s: str) -> float:
    return float(s.replace("+", ""))


def _over_under(market: str):
    def f(v: str):
        side, _, line = v.partition(" ")
        if side not in ("Over", "Under") or not line:
            return None
        return market, side.lower(), _num(line)
    return f


def _choice(market: str, table: dict[str, str]):
    return lambda v: (market, table[v], None) if v in table else None


def _yes_no(market: str):
    return _choice(market, {"Yes": "yes", "No": "no"})


def _handicap(market: str):
    def f(v: str):
        side, _, line = v.partition(" ")
        sel = {"Home": "home", "Away": "away", "Draw": "draw"}.get(side)
        if sel is None or not line or (market == "asian_handicap" and sel == "draw"):
            return None
        return market, sel, _num(line)
    return f


def _result_with(market: str):
    """ "Home/Over 2.5" (result_total) or "Home/Yes" (result_btts)."""
    def f(v: str):
        res, _, rest = v.partition("/")
        r = {"Home": "home", "Draw": "draw", "Away": "away"}.get(res)
        if r is None:
            return None
        if market == "result_btts":
            yn = {"Yes": "yes", "No": "no"}.get(rest)
            return None if yn is None else (market, f"{r}_{yn}", None)
        side, _, line = rest.partition(" ")
        if side not in ("Over", "Under") or not line:
            return None
        return market, f"{r}_{side.lower()}", _num(line)
    return f


def _total_btts(v: str):
    """ "o/yes 2.5"."""
    head, _, line = v.partition(" ")
    side, _, yn = head.partition("/")
    side = {"o": "over", "u": "under"}.get(side)
    if side is None or yn not in ("yes", "no") or not line:
        return None
    return "total_btts", f"{side}_{yn}", _num(line)


def _exact_goals(market: str):
    def f(v: str):
        if v.isdigit():
            return market, v, None
        if v.startswith("more ") and v[5:].isdigit():
            return market, f"{v[5:]}+", None
        return None
    return f


def _exact_score(v: str):
    h, _, a = v.partition(":")
    return ("exact_score", f"{int(h)}:{int(a)}", None) if h.isdigit() and a.isdigit() else None


def _team_scores(side: str):
    return lambda v: {"Yes": (f"team_total_{side}", "over", 0.5), "No": (f"team_total_{side}", "under", 0.5)}.get(v)


def _win_to_nil(v: str):
    return {"Home": ("win_to_nil_home", "yes", None), "Away": ("win_to_nil_away", "yes", None)}.get(v)


ODD_EVEN = {"Odd": "odd", "Even": "even"}
# Bet name in the feed -> parser of one value into (market, selection, line). Names are matched exactly.
BETS = {
    "Match Winner": _choice("1x2", {"Home": "home", "Draw": "draw", "Away": "away"}),
    "Home/Away": _choice("dnb", {"Home": "home", "Away": "away"}),
    "Double Chance": _choice("double_chance", {"Home/Draw": "1X", "Draw/Away": "X2", "Home/Away": "12"}),
    "Both Teams Score": _yes_no("btts"),
    "Goals Over/Under": _over_under("totals"),
    "Goal Line": _over_under("totals"),
    "Total - Home": _over_under("team_total_home"),
    "Total - Away": _over_under("team_total_away"),
    "Home Team Score a Goal": _team_scores("home"),
    "Away Team Score a Goal": _team_scores("away"),
    "Asian Handicap": _handicap("asian_handicap"),
    "Handicap Result": _handicap("euro_handicap"),
    "Result/Total Goals": _result_with("result_total"),
    "Results/Both Teams Score": _result_with("result_btts"),
    "Total Goals/Both Teams To Score": _total_btts,
    "Clean Sheet - Home": _yes_no("clean_sheet_home"),
    "Clean Sheet - Away": _yes_no("clean_sheet_away"),
    "Win to Nil - Home": _yes_no("win_to_nil_home"),
    "Win to Nil - Away": _yes_no("win_to_nil_away"),
    "Win To Nil": _win_to_nil,
    "Odd/Even": _choice("odd_even", ODD_EVEN),
    "Home Odd/Even": _choice("odd_even_home", ODD_EVEN),
    "Away Odd/Even": _choice("odd_even_away", ODD_EVEN),
    "Exact Score": _exact_score,
    "Exact Goals Number": _exact_goals("exact_goals"),
    "Home Team Exact Goals Number": _exact_goals("exact_goals_home"),
    "Away Team Exact Goals Number": _exact_goals("exact_goals_away"),
    "Number Of Goals In Match": _choice("goal_range", {"Under 2 goals": "0-1", "2 or 3 goals": "2-3",
                                                       "Over 3 goals": "4+"}),
    "Scoring Draw": _yes_no("scoring_draw"),
}


def map_value(bet: str, value) -> tuple[str, str, float | None] | None:
    """Internal (market, selection, line) of one value of one bet, None when the model cannot price it."""
    f = BETS.get(bet)
    if f is None:
        return None
    try:
        return f(str(value).strip())
    except (ValueError, KeyError):
        return None


def parse_odds(items: list[dict], teams: dict[str, tuple[str, str, datetime]], validity: timedelta,
               max_examples: int = 5) -> tuple[list[OddsQuote], dict[str, list[dict]]]:
    """Quotes of the priced markets, and per fixture the bets the model does not price ("non analysé").

    teams: fixture id -> (home, away, kick-off), from the fixtures feed (the odds feed has no team names).
    Fixtures not in `teams` are skipped. The summary of a bet not priced keeps its best price per value
    (at most max_examples values, the shortest odds first) and how many bookmakers and values it has."""
    quotes, unpriced = [], {}
    for item in items:
        fid = str((item.get("fixture") or {}).get("id"))
        if fid not in teams:
            continue
        home, away, kickoff = teams[fid]
        observed = _ts(item.get("update") or item["fixture"]["date"])
        other: dict[str, dict] = {}
        for bk in item.get("bookmakers") or []:
            book = bk.get("name") or str(bk.get("id"))
            for bet in bk.get("bets") or []:
                name = bet.get("name") or str(bet.get("id"))
                for v in bet.get("values") or []:
                    try:
                        odd = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    mapped = map_value(name, v.get("value"))
                    if mapped is None:
                        o = other.setdefault(name, {"marche": name, "bookmakers": set(), "valeurs": {}})
                        o["bookmakers"].add(book)
                        best = o["valeurs"].get(str(v.get("value")))
                        if best is None or odd > best["cote"]:
                            o["valeurs"][str(v.get("value"))] = {"valeur": str(v.get("value")), "cote": odd,
                                                                 "bookmaker": book}
                        continue
                    market, selection, line = mapped
                    quotes.append(OddsQuote(
                        provider=PROVIDER, bookmaker=book, provider_event_id=fid, home=home, away=away,
                        kickoff=kickoff, market=market, selection=selection, line=line, odds=odd,
                        observed_at=observed, mode="real", expires_at=observed + validity,
                        validation_status="valid" if odd > 1.0 else "rejected"))
        unpriced[fid] = [{"marche": o["marche"], "statut": "non analysé", "bookmakers": len(o["bookmakers"]),
                          "selections": len(o["valeurs"]),
                          "meilleures_cotes": sorted(o["valeurs"].values(), key=lambda x: x["cote"])[:max_examples]}
                         for o in sorted(other.values(), key=lambda o: o["marche"])]
    return quotes, unpriced


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
