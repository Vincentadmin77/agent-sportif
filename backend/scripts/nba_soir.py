"""NBA test pass: price tonight's games with the ratings model against live odds.

For each game of the Paris day (league 12), reads the bookmakers' odds from
API-Basketball (/odds?game=...) and compares, per market, the model's
probability with the median price across books (and the best price):
winner (Home/Away), Asian handicap, total points, first-quarter winner
(Home/Away - 1st Qtr, refunded on a tie) and first-quarter 1N2.

Writes a JSON report (--out) and prints a short summary. Model state: fitted
on every finished game before the pass. Early in a season the ratings still
lean on last season, so summer roster moves are only partly seen.

Usage: python scripts/nba_soir.py --date 2026-10-28 --out /mnt/project-files/agent-sportif/nba/2026-10-28.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_nba  # noqa: E402
from sportai.engine.basketball.ratings import Game, RatingsModel, rest_flags  # noqa: E402

BASE = "https://v1.basketball.api-sports.io"


def _odds(client, gid):
    data = client.get("/odds", params={"game": gid}).json()
    resp = data.get("response") or []
    return resp[0]["bookmakers"] if resp else []


def _collect(books):
    """{(market, selection_key): [odds...]} with handicaps/lines normalised."""
    out: dict[tuple, list[float]] = {}

    def add(k, o):
        out.setdefault(k, []).append(float(o))

    for bk in books:
        for bet in bk["bets"]:
            n = bet["name"]
            for v in bet["values"]:
                val, o = v["value"], v["odd"]
                if n == "Home/Away":
                    add(("vainqueur", val), o)
                elif n == "Home/Away - 1st Qtr":
                    add(("q1_sans_nul", val), o)
                elif n == "3Way Result - 1st Qtr":
                    add(("q1_1n2", val), o)
                elif n == "Asian Handicap":
                    side, h = val.split(" ", 1)
                    h = float(h)
                    # Values come in pairs "Home X" / "Away X": the away side gets -X.
                    add(("handicap", "Home", h) if side == "Home" else ("handicap", "Away", -h), o)
                elif n == "Over/Under":
                    side, line = val.split(" ", 1)
                    add(("total", side, float(line)), o)
    return out


def price(pred, key):
    m = key[0]
    if m == "vainqueur":
        p = pred.p_home_win()
        return p if key[1] == "Home" else 1 - p
    if m == "q1_sans_nul":
        p = pred.q1_home_win_no_tie()
        return p if key[1] == "Home" else 1 - p
    if m == "q1_1n2":
        h, d, a = pred.q1_probs()
        return {"Home": h, "Draw": d, "Away": a}[key[1]]
    if m == "handicap":
        h = key[2]
        p = pred.p_home_cover(h) if key[1] == "Home" else 1 - pred.p_home_cover(-h)
        return p
    if m == "total":
        p = pred.p_over(key[2])
        return p if key[1] == "Over" else 1 - p
    return None


LABEL = {"vainqueur": "Vainqueur", "q1_sans_nul": "1er quart-temps (remboursé si égalité)",
         "q1_1n2": "1er quart-temps 1N2", "handicap": "Handicap", "total": "Total de points"}
VALIDE = {"vainqueur", "handicap", "q1_sans_nul", "q1_1n2"}
# Same rules as the football tickets: value >= 3 %, chance >= 25 %, odds <= 4.00,
# at least 3 books, and the model may not stray more than 12 points from the
# bookmakers' fair probability (a bigger gap usually means news the model lacks:
# injuries, rest decisions, trades).
MIN_VALUE, MIN_P, MAX_ODDS, MIN_BOOKS, MAX_GAP = 0.03, 0.25, 4.0, 3, 0.12


def _opposite(key):
    m = key[0]
    if m in ("vainqueur", "q1_sans_nul"):
        return [(m, "Away" if key[1] == "Home" else "Home")]
    if m == "handicap":
        return [(m, "Away" if key[1] == "Home" else "Home", -key[2])]
    if m == "total":
        return [(m, "Under" if key[1] == "Over" else "Over", key[2])]
    return [(m, x) for x in ("Home", "Draw", "Away") if x != key[1]]


def fair(odds, key):
    """Bookmakers' probability without their margin, from median odds of all outcomes."""
    others = [odds.get(k) for k in _opposite(key)]
    if any(not o for o in others):
        return None
    inv = 1 / statistics.median(odds[key])
    tot = inv + sum(1 / statistics.median(o) for o in others)
    return inv / tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    client = httpx.Client(base_url=BASE, headers={"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}, timeout=30)
    rows = client.get("/games", params={"league": 12, "season": "2026-2027", "date": a.date,
                                        "timezone": "Europe/Paris"}).json()["response"]
    history = backtest_nba.load()
    day = date.fromisoformat(a.date)
    tonight = [Game(datetime.fromisoformat(g["date"]).astimezone(backtest_nba.US).date(),
                    g["teams"]["home"]["name"], g["teams"]["away"]["name"]) for g in rows]
    allg = sorted(history + tonight, key=lambda g: g.day)
    flags = dict(zip([id(g) for g in allg], rest_flags(allg)))
    model = RatingsModel().fit(history, max(g.day for g in tonight))
    report = []
    for g, raw in zip(tonight, rows):
        pred = model.predict(g.home, g.away, flags[id(g)])
        odds = _collect(_odds(client, raw["id"]))
        sels = []
        for key, os_ in odds.items():
            if len(os_) < 2:
                continue
            p = price(pred, key)
            if p is None:
                continue
            med, best = statistics.median(os_), max(os_)
            f = fair(odds, key)
            ok = (key[0] in VALIDE and p * med - 1 >= MIN_VALUE and p >= MIN_P and med <= MAX_ODDS
                  and len(os_) >= MIN_BOOKS and f is not None and abs(p - f) <= MAX_GAP)
            sels.append({"retenu": ok, "proba_bookmakers": round(f, 3) if f else None,"marche": key[0], "selection": list(key[1:]), "proba": round(p, 3),
                         "cote_mediane": med, "meilleure_cote": best, "nb_bookmakers": len(os_),
                         "value_mediane": round(p * med - 1, 3), "valide": key[0] in VALIDE})
        # Main total line: where over and under median odds are closest.
        report.append({
            "id": raw["id"], "heure_paris": raw["date"][11:16], "domicile": g.home, "exterieur": g.away,
            "repos": dict(zip(["dom_veille", "dom_repose", "ext_veille", "ext_repose"], flags[id(g)])),
            "score_prevu": [round(pred.home_pts, 1), round(pred.away_pts, 1)],
            "proba_victoire_dom": round(pred.p_home_win(), 3),
            "q1": [round(x, 3) for x in pred.q1_probs()],
            "selections": sorted(sels, key=lambda s: -s["value_mediane"]),
        })
    out = {"date": a.date, "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matchs": report}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for m in report:
        print(m["heure_paris"], m["domicile"], "-", m["exterieur"], m["score_prevu"], "P(dom)", m["proba_victoire_dom"], m["repos"])
        for s in [x for x in m["selections"] if x["retenu"]][:4] or m["selections"][:2]:
            print("   ", LABEL[s["marche"]], s["selection"], "p", s["proba"], "cote", s["cote_mediane"], "best", s["meilleure_cote"],
                  "value", s["value_mediane"], "n", s["nb_bookmakers"], "bk", s["proba_bookmakers"], "RETENU" if s["retenu"] else "")


if __name__ == "__main__":
    main()
