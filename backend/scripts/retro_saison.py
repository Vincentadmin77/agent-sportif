"""Season retrospective (owner, 2026-10-27: "une rétrospective des matchs depuis le début de saison sans regarder le
score et trouver ce que les agents auraient mis").

Walk-forward replay of the current selection rules (sportai/services/pronostic.py, docs/16-une-cote-par-match.md) on
every day of the season so far, with only what was known before kick-off:
- the Dixon-Coles model is refitted each day on the results of the three last seasons played BEFORE that day
  (08:00 Paris, the first pass), the same fit as production (services/prematch.run_prematch);
- the odds are football-data.co.uk's closing odds (the last price before kick-off, close to the 45 min pass), its
  pre-closing odds when the closing ones are missing; the result columns of the file are dropped on reading;
- the rules: a match's bet as in legs_from_db + build_picks (validated markets, goal and trial markets with their own
  gates, then pick_ok), the day's best single (Confiance élevée, odds 1.60 or more) and the combos of 2 as in
  build_combos (the selected one = two Confiance élevée bets other than the best single).
Only then are the bets settled on the final scores (API-Football cache). Ratios only, no money (owner, 2026-10-27).

Not reproduced (no historical data): the review's daily corrections (none existed before 11 October, ratio 1 here),
the enriched goals model (Dixon-Coles prices the goals, its fallback in production), the rotation risk, line-ups,
markets football-data does not publish (double chance, draw no bet, both teams score, team goals, scorers...), and
the Champions League / Europa League (no free historical odds). The prices are football-data's average over its
bookmakers ("Avg"), not the French bookmakers' best price.

Usage: python scripts/retro_saison.py --af DIR --odds DIR --to 2026-10-26 --out retro.json
  --af: API-Football fixture cache (fixtures_league<id>_season<y>.json.gz)
  --odds: football-data.co.uk CSV files (E0.csv, F1.csv, SP1.csv, D1.csv, I1.csv, T1.csv, BRA.csv)
"""

from __future__ import annotations

import argparse
import csv
import difflib
import gzip
import json
import sys

import numpy as np
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.connectors.teams import normalise  # noqa: E402
from sportai.engine.football import dixon_coles as dc  # noqa: E402
from sportai.engine.football.markets import MarketKey, is_quarter, outcome, settle_score  # noqa: E402
from sportai.engine.value import devig_shin, expected_value  # noqa: E402
from sportai.services import pronostic as P  # noqa: E402
from sportai.services.dashboard import bet_label, goal_label  # noqa: E402
from sportai.services.prematch import LOCAL_TZ, journee  # noqa: E402
from sportai.services.revue import correct_leg  # noqa: E402

LEAGUES = {  # code: (API-Football id, football-data file, calendar-year season)
    "FR1": (61, "F1.csv", False), "EN1": (39, "E0.csv", False), "ES1": (140, "SP1.csv", False),
    "DE1": (78, "D1.csv", False), "IT1": (135, "I1.csv", False), "TR1": (203, "T1.csv", False),
    "BR1": (71, "BRA.csv", True),
}
NAMES = {"FR1": "Ligue 1", "EN1": "Premier League", "ES1": "Liga", "DE1": "Bundesliga", "IT1": "Serie A",
         "TR1": "Süper Lig", "BR1": "Brasileirão"}
SEASON = {False: 2026, True: 2026}  # --season Y: the Y-(Y+1) European season and the Y Brazilian one
FIRST_PASS = time(8, 0)
RAW: list[dict] = []
XG_W: float | None = None  # --xg W: fit on W x goals + (1 - W) x expected goals from shots (past matches only)
XG_FIT_BEFORE = date(2022, 7, 1)  # the shot values are fitted on 2020-22, before every replayed season
SHOTS: Path | None = None  # --shots DIR: football-data files of past seasons, <code>_<anything>.csv, shots columns read
EARLY = False  # --early: the odds of a few days before (football-data pre-closing), closing ones as fallback
PRICE = "Avg"  # --price Max: the best price over the bookmakers, an optimistic check
BOOKS = ("B365", "BW", "PS", "WH", "BFD", "BV", "PP", "SKB", "1XB", "LB", "IW", "VC")  # bookmakers, no exchange


def load_results(af: Path, league_id: int) -> list[dict]:
    """Finished matches of the last seasons: kick-off, teams, final score (the fit's history and the settlement)."""
    out = []
    for f in sorted(af.glob(f"fixtures_league{league_id}_season*.json.gz")):
        if "asof" in f.name:
            continue
        for x in json.load(gzip.open(f))["data"]["response"]:
            if x["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
                continue
            ft = x["score"]["fulltime"]
            if ft["home"] is None:
                continue
            out.append({"ko": datetime.fromisoformat(x["fixture"]["date"]), "home": x["teams"]["home"]["name"],
                        "away": x["teams"]["away"]["name"], "hg": int(ft["home"]), "ag": int(ft["away"]),
                        "season": x["league"]["season"]})
    return sorted(out, key=lambda m: m["ko"])


def _f(row: dict, key: str) -> float | None:
    try:
        v = float(row.get(key) or "")
        return v if v > 1.0 else None
    except ValueError:
        return None


def load_odds(path: Path, brazil: bool) -> list[dict]:
    """Pre-match prices of each match. The result columns are never kept."""
    out = []
    with path.open(encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if brazil and row.get("Season") != str(SEASON[True]):
                continue
            try:
                d = datetime.strptime(row["Date"], "%d/%m/%Y" if len(row["Date"]) == 10 else "%d/%m/%y").date()
            except (KeyError, ValueError):
                continue
            home, away = (row.get("Home"), row.get("Away")) if brazil else (row.get("HomeTeam"), row.get("AwayTeam"))
            m = {"date": d, "home": home, "away": away, "books": {}, "closing": True}
            for closing in ((False, True) if EARLY else (True, False)):
                c = "C" if closing else ""
                books = {}
                for b in BOOKS + ("Avg", "Max"):
                    h, dr, a = (_f(row, f"{b}{c}{s}") for s in "HDA")
                    entry = {}
                    if h and dr and a:
                        entry["1x2"] = (h, dr, a)
                    o, u = _f(row, f"{'P' if b == 'PS' else b}{c}>2.5"), _f(row, f"{'P' if b == 'PS' else b}{c}<2.5")
                    if o and u:
                        entry["totals"] = (o, u)
                    ah, aa = _f(row, f"{'P' if b == 'PS' else b}{c}AHH"), _f(row, f"{'P' if b == 'PS' else b}{c}AHA")
                    if ah and aa:
                        entry["ah"] = (ah, aa)
                    if entry:
                        books[b] = entry
                if "Avg" in books and "1x2" in books["Avg"]:
                    m["books"], m["closing"] = books, closing
                    line = row.get(f"AH{'C' if closing else ''}h")
                    try:
                        m["ah_line"] = float(line) if line not in (None, "") else None
                    except ValueError:
                        m["ah_line"] = None
                    break
            if m["books"]:
                out.append(m)
    return out


def load_shots(shots_dir: Path, fname: str) -> list[dict]:
    """Shots and shots on target of each past match (football-data HS/AS/HST/AST)."""
    out = []
    for path in sorted(shots_dir.glob(fname.replace(".csv", "_*.csv"))):
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                try:
                    d = datetime.strptime(row["Date"], "%d/%m/%Y" if len(row["Date"]) == 10 else "%d/%m/%y").date()
                    hs, as_, hst, ast = (float(row[k]) for k in ("HS", "AS", "HST", "AST"))
                except (KeyError, ValueError):
                    continue
                out.append({"date": d, "home": row["HomeTeam"], "away": row["AwayTeam"], "hs": hs, "as": as_,
                            "hst": hst, "ast": ast})
    return out


def xg_proxy(results: list[dict], shots: dict[int, dict], before: date) -> tuple[float, float]:
    """Goals per shot on target and per other shot, fitted on the matches before `before` only."""
    x, y = [], []
    for i, s in shots.items():
        m = results[i]
        if m["ko"].date() < before:
            x += [(s["hst"], s["hs"] - s["hst"]), (s["ast"], s["as"] - s["ast"])]
            y += [m["hg"], m["ag"]]
    a, b = np.linalg.lstsq(np.array(x), np.array(y, dtype=float), rcond=None)[0]
    return float(a), float(b)


def match_odds(results: list[dict], odds: list[dict]) -> tuple[dict[int, dict], list[str]]:
    """Each season match (by index in results) -> its prices: same date (±1 day) and the closest names."""
    by_date = defaultdict(list)
    for o in odds:
        by_date[o["date"]].append(o)
    found, misses = {}, []
    for i, m in enumerate(results):
        d = journee(m["ko"])
        cands = by_date[d] + by_date[d - timedelta(days=1)] + by_date[d + timedelta(days=1)] + by_date[m["ko"].date()]
        best, score = None, 0.0
        for o in cands:
            s = (difflib.SequenceMatcher(None, normalise(m["home"]), normalise(o["home"])).ratio()
                 + difflib.SequenceMatcher(None, normalise(m["away"]), normalise(o["away"])).ratio())
            if s > score:
                best, score = o, s
        if best and score >= 1.0:
            found[i] = best
        else:
            misses.append(f"{m['ko']:%Y-%m-%d} {m['home']} - {m['away']}")
    return found, misses


def consensus(books: dict, kind: str) -> list[float] | None:
    """Margin-free chances of a market: Shin per bookmaker, then averaged (prematch._market_consensus)."""
    acc = [devig_shin(list(e[kind])) for b, e in books.items() if b not in ("Avg", "Max") and kind in e]
    if not acc:
        acc = [devig_shin(list(books["Avg"][kind]))] if kind in books.get("Avg", {}) else []
    if not acc:
        return None
    return [sum(p[k] for p in acc) / len(acc) for k in range(len(acc[0]))]


def legs_for(m: dict, o: dict, sm, comp: str) -> list[dict]:
    """The bets of a match as legs_from_db builds them, on the markets football-data publishes."""
    price = o["books"].get(PRICE) or o["books"]["Avg"]
    home, away = m["home"], m["away"]
    ko = m["ko"]
    mid = f"{journee(ko):%Y-%m-%d}|{home}|{away}"
    base = {"match_id": mid, "match": f"{home} - {away}", "competition": NAMES[comp], "coup_envoi": ko.isoformat()}
    rows = []  # (market, selection, line, odds, fair)
    fair = consensus(o["books"], "1x2")
    for s, odd, p in zip(("home", "draw", "away"), price["1x2"], fair or (None,) * 3):
        rows.append(("1x2", s, None, odd, p))
    if "totals" in price:
        fair = consensus(o["books"], "totals")
        for s, odd, p in zip(("over", "under"), price["totals"], fair or (None,) * 2):
            rows.append(("totals", s, 2.5, odd, p))
    if "ah" in price and o.get("ah_line") is not None:
        fair = consensus(o["books"], "ah")
        for s, odd, p in zip(("home", "away"), price["ah"], fair or (None,) * 2):
            rows.append(("asian_handicap", s, o["ah_line"], odd, p))
    for market, sel, line, odds, pm in rows:  # every priced bet, before any rule (--dump: the material to learn from)
        out = outcome(sm, MarketKey(market, sel, line))
        RAW.append({"jour": f"{journee(ko):%Y-%m-%d}", "comp": comp, "match": f"{home} - {away}", "marche": market,
                    "sel": sel, "ligne": line, "cote": odds, "p": round(out.win, 4), "push": round(out.push, 4),
                    "pm": None if pm is None else round(pm, 4),
                    "res": settle_score(MarketKey(market, sel, line), m["hg"], m["ag"])})
    legs, trials, goal_trials = [], [], []
    div = 0.10
    for market, sel, line, odds, pm in rows:
        if odds < P.COTE_MIN:
            continue
        out = outcome(sm, MarketKey(market, sel, line))
        ev = expected_value(out, odds)
        fix = correct_leg(market, sel, line, out.win, out.push, odds, pm, None)
        p_win, p_cmp, fam = fix["proba"], fix["p_cmp"], fix["famille"]
        if market == "1x2":
            pari, valide = bet_label(market, sel, home, away), True
        elif market == "totals":
            pari, valide = goal_label(market, sel, line), False
            if is_quarter(line) or ev <= P.GOAL_MIN_VALUE or pm is None or min(p_cmp, pm) < P.GOAL_MIN_PROBA \
                    or odds > P.TRIAL_MAX_ODDS or abs(p_cmp - pm) > div:
                continue
        else:
            pari, valide = bet_label(market, sel, home, away, line), False
            if is_quarter(line) or ev < P.TRIAL_MIN_VALUE or pm is None or p_win < P.TRIAL_MIN_PROBA \
                    or odds > P.TRIAL_MAX_ODDS or abs(p_cmp - pm) > div:
                continue
        leg = {**base, "pari": pari, "marche": market, "cote": odds, "proba": round(p_win, 4),
               "proba_marche": None if pm is None else round(pm, 4), "valeur": round(ev, 4), "valide": valide,
               "_divergence_max": div, "_p_cmp": round(p_cmp, 4), "famille": fam,
               "_key": (market, sel, line), "_score": (m["hg"], m["ag"])}
        if valide:
            legs.append(leg)
        else:
            leg["validation"] = "non validé"
            (goal_trials if market == "totals" else trials).append(leg)
    legs += sorted(trials, key=lambda l: -l["valeur"])[:P.TRIAL_PER_MATCH]
    legs += sorted(goal_trials, key=lambda l: -l["valeur"])[:P.GOAL_PER_MATCH]
    return legs


def settle(leg: dict) -> str:
    market, sel, line = leg["_key"]
    return settle_score(MarketKey(market, sel, line), *leg["_score"])


def day_picks(legs: list[dict]) -> tuple[list[dict], dict | None, list[dict]]:
    """build_picks + build_combos with the odds unchanged through the day (see the module notes)."""
    by_match = defaultdict(list)
    for l in legs:
        by_match[l["match_id"]].append(l)
    picks = []
    for mid, ls in by_match.items():
        ok = [l for l in ls if P._correct(l) and P.pick_ok(l)]
        if ok:
            best = max(ok, key=lambda l: (P.blended(l), l["cote"]))
            picks.append({**P._pick(best), "_key": best["_key"], "_score": best["_score"]})
    elig = [p for p in picks if p["cote"] >= P.SELECTION_MIN_ODDS and p["confiance"] >= P.SELECTION_MIN_CONF]
    top = max(elig, key=lambda p: (p["confiance"], p["cote"])) if elig else None
    free = sorted((p for p in picks if not top or p["match_id"] != top["match_id"]),
                  key=lambda p: (p["confiance"], p["cote"]), reverse=True)
    combos = []
    while len(free) >= 2:
        a, b = free.pop(0), free.pop(0)
        combos.append({"jambes": [a, b], "cote_totale": round(a["cote"] * b["cote"], 2),
                       "proba_totale": round(a["confiance"] * b["confiance"], 4), "retenu": False})
    for c in combos:
        if all(l["confiance"] >= P.SELECTION_MIN_CONF for l in c["jambes"]):
            c["retenu"] = True
            break
    return picks, top, combos


def combo_result(c: dict) -> str:
    rs = [settle(l) for l in c["jambes"]]
    if "perdu" in rs:
        return "perdu"
    return "rembourse" if all(r == "rembourse" for r in rs) else "gagne"


def run(af: Path, odds_dir: Path, last_day: date, leagues: list[str]) -> dict:
    days: dict[date, list[dict]] = defaultdict(list)
    coverage = {}
    for comp in leagues:
        lid, fname, cal = LEAGUES[comp]
        results = load_results(af, lid)
        season = [i for i, m in enumerate(results) if m["season"] == SEASON[cal] and journee(m["ko"]) <= last_day]
        path = odds_dir / fname
        odds = load_odds(path, cal) if path.exists() else []
        found, misses = match_odds([results[i] for i in season], odds)
        found = {season[k]: v for k, v in found.items()}
        cov = {"matchs": len(season), "avec_cotes": len(found), "sans_cotes": misses[:50], "analyses": 0,
               "equipe_inconnue": 0, "cotes_cloture": sum(1 for v in found.values() if v["closing"])}
        canon = [dc.MatchResult(m["ko"].date(), normalise(m["home"]), normalise(m["away"]), m["hg"], m["ag"])
                 for m in results]
        if XG_W is not None and SHOTS:
            shots, _ = match_odds(results, load_shots(SHOTS, fname))
            a, b = xg_proxy(results, shots, XG_FIT_BEFORE)
            cov["xg_par_tir_cadre"], cov["xg_par_autre_tir"], cov["avec_tirs"] = round(a, 3), round(b, 3), len(shots)
            for i, s in shots.items():
                m = results[i]
                xh, xa = a * s["hst"] + b * (s["hs"] - s["hst"]), a * s["ast"] + b * (s["as"] - s["ast"])
                canon[i] = dc.MatchResult(m["ko"].date(), canon[i].home, canon[i].away,
                                          XG_W * m["hg"] + (1 - XG_W) * xh, XG_W * m["ag"] + (1 - XG_W) * xa)
        by_day = defaultdict(list)
        for i in season:
            if i in found:
                by_day[journee(results[i]["ko"])].append(i)
        for d in sorted(by_day):
            cutoff = datetime.combine(d, FIRST_PASS, LOCAL_TZ).astimezone(timezone.utc)
            train = [c for c, m in zip(canon, results) if m["ko"] < cutoff and (d - m["ko"].date()).days <= 3 * 366]
            model = dc.fit(train, reference_date=d, with_uncertainty=False)
            for i in by_day[d]:
                m = results[i]
                h, a = normalise(m["home"]), normalise(m["away"])
                if not (model.knows(h) and model.knows(a)):
                    cov["equipe_inconnue"] += 1
                    continue
                cov["analyses"] += 1
                days[d] += legs_for(m, found[i], model.score_matrix(h, a), comp)
            print(f"{comp} {d}: {len(by_day[d])} matchs", file=sys.stderr)
        coverage[comp] = cov
    report = {"jours": [], "couverture": coverage}
    for d in sorted(days):
        picks, top, combos = day_picks(days[d])
        jd = {"jour": d.isoformat(), "paris_retenus": [], "top": None, "combines": []}
        for p in sorted(picks, key=lambda p: p["coup_envoi"]):
            r = settle(p)
            jd["paris_retenus"].append({k: p[k] for k in ("match", "competition", "pari", "cote", "confiance", "niveau")}
                                       | {"score": "%d - %d" % p["_score"], "resultat": r})
        if top:
            r = settle(top)
            jd["top"] = {k: top[k] for k in ("match", "competition", "pari", "cote", "confiance")} | {
                "score": "%d - %d" % top["_score"], "resultat": r}
        for c in combos:
            r = combo_result(c)
            jd["combines"].append({"retenu": c["retenu"], "cote_totale": c["cote_totale"], "resultat": r,
                                   "jambes": [{k: l[k] for k in ("match", "pari", "cote", "confiance")} | {
                                       "score": "%d - %d" % l["_score"], "resultat": settle(l)} for l in c["jambes"]]})
        report["jours"].append(jd)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--af", type=Path, required=True)
    ap.add_argument("--odds", type=Path, required=True)
    ap.add_argument("--to", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--price", choices=("Avg", "Max"), default="Avg")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--early", action="store_true")
    ap.add_argument("--xg", type=float)
    ap.add_argument("--shots", type=Path)
    ap.add_argument("--dump", type=Path, help="every priced bet with its result, to study the rules")
    a = ap.parse_args()
    global PRICE, EARLY, XG_W, SHOTS
    PRICE, EARLY, XG_W, SHOTS = a.price, a.early, a.xg, a.shots
    SEASON.update({False: a.season, True: a.season})
    a.out.write_text(json.dumps(run(a.af, a.odds, a.to, a.leagues), indent=1, ensure_ascii=False, default=str))
    if a.dump:
        a.dump.write_text(json.dumps(RAW, ensure_ascii=False))


if __name__ == "__main__":
    main()
