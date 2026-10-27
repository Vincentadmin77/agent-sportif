"""Corners, cards and shots bets of the odds feed priced by the count model (engine/football/counts.py, owner
2026-10-27: "toutes les cotes doivent être étudiées"). Stored on the run's fixture as "comptages_cotes"; the bets then
leave the "non analysé" list.

Team averages come from the API-Football cache the daily goals refresh fills (the statistics of every finished match,
docs/18-corners-cartons-tirs.md), replayed as in the backtest. A team with fewer than 3 home (or away) matches known,
or whose last known match is older than STALE_DAYS (statistics not refreshed), gets no price. The bookmaker margin is
removed on complete groups of one bookmaker (median over the bookmakers), otherwise the market's chance is 1 / odds.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median

from sportai.connectors.teams import normalise
from sportai.engine.football import counts

LEAGUE_IDS = {61: "FR1", 39: "EN1", 140: "ES1", 78: "DE1", 135: "IT1", 71: "BR1", 203: "TR1"}
FINISHED = ("FT", "AET", "PEN")
STALE_DAYS = 45
SUMMARY = "comptages_resume.json.gz"
FEED_TYPES = {"Corner Kicks": "corners", "Yellow Cards": "jaunes", "Red Cards": "rouges", "Total Shots": "tirs",
              "Shots on Goal": "tirs_cadres"}


def stat_value(stat: str, side: dict) -> int | None:
    """The count of one statistic from a team's {feed name -> value} (a missing card count is 0)."""
    if stat == "cartons":
        return None if "jaunes" not in side and "rouges" not in side else int(side.get("jaunes") or 0) + int(side.get("rouges") or 0)
    if stat == "jaunes":
        return None if "jaunes" not in side else int(side.get("jaunes") or 0)
    v = side.get(stat)
    return None if v is None else int(v)


def _read(p: Path) -> dict:
    with gzip.open(p, "rt") as f:
        return json.load(f)["data"]


@lru_cache(maxsize=2)
def history(folder: str, day: str) -> dict[str, list[dict]]:
    """Finished regular-season matches of each league with their statistics, oldest first. Reading the cache takes
    about a minute on the shared folder: the result is kept for the day in SUMMARY (one read a day for all passes)."""
    summary = Path(folder) / SUMMARY
    files = sorted(Path(folder).glob("fixtures_ids_*.json.gz"))
    try:
        with gzip.open(summary, "rt") as f:
            saved = json.load(f)
        if saved.get("jour") == day and saved.get("fichiers") == len(files):  # new statistics: read again
            return {c: [{**m, "kickoff": datetime.fromisoformat(m["kickoff"])} for m in ms]
                    for c, ms in saved["ligues"].items()}
    except (OSError, ValueError, KeyError):
        pass
    out: dict[str, dict[int, dict]] = {}
    for p in files:
        try:
            resp = _read(p).get("response", [])
        except (OSError, ValueError, KeyError):
            continue
        for f in resp:
            comp = LEAGUE_IDS.get((f.get("league") or {}).get("id"))
            if (comp is None or f["fixture"]["status"]["short"] not in FINISHED
                    or not str(f["league"].get("round", "")).startswith("Regular Season")):
                continue
            hid = f["teams"]["home"]["id"]
            sides: dict[str, dict] = {"home": {}, "away": {}}
            for block in f.get("statistics") or []:
                side = sides["home" if block["team"]["id"] == hid else "away"]
                for s in block.get("statistics") or []:
                    name = FEED_TYPES.get(s.get("type"))
                    if name:
                        v = s.get("value")
                        side[name] = None if v in (None, "") else int(float(str(v).rstrip("%")))
            if not (sides["home"] and sides["away"]):
                continue
            out.setdefault(comp, {})[f["fixture"]["id"]] = {
                "kickoff": f["fixture"]["date"].replace("Z", "+00:00"),
                "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"], "stats": sides}
    ligues = {c: sorted(ms.values(), key=lambda m: m["kickoff"]) for c, ms in out.items()}
    try:
        tmp = summary.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as f:
            json.dump({"jour": day, "fichiers": len(files), "ligues": ligues}, f)
        tmp.replace(summary)
    except OSError:
        pass
    return {c: [{**m, "kickoff": datetime.fromisoformat(m["kickoff"])} for m in ms] for c, ms in ligues.items()}


@lru_cache(maxsize=16)
def model(folder: str, day: str, comp: str, stat: str) -> tuple[counts.CountModel, dict[str, datetime]] | None:
    """The count model of a league replayed on every known match (league averages and dispersion refreshed weekly on
    the 3 previous years, as in the backtest), and each team's last known match."""
    ms = []
    for m in history(folder, day).get(comp, []):
        h, a = stat_value(stat, m["stats"]["home"]), stat_value(stat, m["stats"]["away"])
        if h is not None and a is not None:
            ms.append({**m, "h": h, "a": a})
    if len(ms) < 200:
        return None
    cm, week, last = counts.CountModel(), None, {}
    days = [m["kickoff"].date().toordinal() for m in ms]
    lo = 0
    for i, m in enumerate(ms):
        d = days[i]
        wk = d - d % 7
        if wk != week:
            while days[lo] < d - 3 * 365:
                lo += 1
            cm.refit(ms[lo:i])
            week = wk
        cm.add(m)
        last[m["home"]] = last[m["away"]] = m["kickoff"]
    while days[lo] < days[-1] - 3 * 365:
        lo += 1
    cm.refit(ms[lo:])
    return cm, last


def _team(name: str, known) -> str | None:
    if name in known:
        return name
    n = normalise(name)
    return next((k for k in known if normalise(k) == n), None)


def price_counts(item: dict, home: str, away: str, competition: str, now: datetime,
                 folder: str) -> list[dict]:
    rows_by: dict[tuple, dict] = {}
    by_book: dict[tuple, dict[str, float]] = {}
    for bk in item.get("bookmakers") or []:
        book = bk.get("name") or str(bk.get("id"))
        for bet in bk.get("bets") or []:
            for v in bet.get("values") or []:
                mapped = counts.map_value(bet.get("name") or "", v.get("value"))
                if mapped is None:
                    continue
                try:
                    odd = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if odd <= 1.0:
                    continue
                stat, kind, sel, line = mapped
                by_book.setdefault((book, stat, kind, line), {})[sel] = odd
                b = rows_by.get(mapped)
                if b is None or odd > b["cote"]:
                    rows_by[mapped] = {"cote": odd, "bookmaker": book}
    day = now.astimezone(timezone.utc).date().isoformat()
    rows = []
    for (stat, kind, sel, line), b in rows_by.items():
        got = model(folder, day, competition, stat)
        if got is None:
            continue
        cm, last = got
        h, a = _team(home, cm.hf), _team(away, cm.af)
        if (h is None or a is None or any(now - last.get(t, now - timedelta(days=999)) > timedelta(days=STALE_DAYS)
                                          for t in (h, a))):
            continue
        mean, raw = cm.expected(h, a), cm.raw(h, a)
        if mean is None:
            continue
        p = counts.win_prob(kind, sel, line, *mean, cm.r, cm.r_total, cm.total_mean(sum(raw)))
        sides = counts.GROUPS.get(kind, ("over", "under"))
        fair = [(1 / odds[sel]) / sum(1 / odds[s] for s in sides)
                for (book, st, k, ln), odds in by_book.items()
                if st == stat and k == kind and ln == line and all(s in odds for s in sides)]
        pm = median(fair) if fair else 1 / b["cote"]
        rows.append({"stat": stat, "type": kind, "selection": sel, "ligne": line, "marche": counts.NAMES[stat],
                     "pari": counts.label(stat, kind, sel, line, home, away), "cote": b["cote"],
                     "bookmaker": b["bookmaker"], "proba": round(p, 4), "proba_marche": round(pm, 4),
                     "proba_marche_brute": not fair, "valeur": round(p * b["cote"] - 1, 4),
                     "moyennes": [round(mean[0], 2), round(mean[1], 2)],
                     "valide": counts.validated(stat, kind, sel, line)})
    return sorted(rows, key=lambda r: -r["valeur"])


def settle_count(leg: dict, doc: dict) -> str | None:
    """A corners / cards / shots bet, from the final statistics the live follow-up wrote on the page."""
    c = leg["comptage"]
    st = (doc.get("direct") or {}).get("stats") or {}
    if doc.get("statut") != "termine":
        return None
    sides = [{k: (v[i] if isinstance(v, list) and len(v) == 2 else None) for k, v in st.items()} for i in (0, 1)]
    h, a = stat_value(c["stat"], sides[0]), stat_value(c["stat"], sides[1])
    if h is None or a is None:
        return None
    return counts.settle(c["type"], c["selection"], c["ligne"], h, a)
