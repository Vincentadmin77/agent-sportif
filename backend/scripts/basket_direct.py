"""Le M in play, basketball (owner 2026-11-08 13:24Z: « lance-le, il devrait déjà être fait »), for the record only
("à blanc", admin only, never in the official ratio), same in-play rule as tennis and football:
  odds 1.50-2.30, bookmakers' chance without margin >= 0.60, at most ONE position per game,
  never before the first quarter is over, odds updated less than 5 minutes ago.

Live scores and quarters: API-Basketball (the day's games, ids = basket-<id> of the registry). Live odds: The Odds API
(basketball_nba, region eu, markets h2h / spreads / totals: 3 credits a call), margin removed per bookmaker on each
two-way pair, median over bookmakers; the odds taken = best price, capped at 1.08 x the median (outliers).
Each cycle also settles the positions of finished games (overtime counts, as on every NBA market).

Output: one doc for the page collection basket_m_direct/<jour>:
  {jour, regle, matchs: {<game id>: {match, coup_envoi, position, cle, cote, proba_bookmakers, nb_sites, score_au_moment,
   quart, heure, resultat, score_final}}, ratio, maj}

Usage: basket_direct.py cycle --jour D --doc CUR.json --out OUT.json       (one pass)
       basket_direct.py boucle --jour D --doc CUR.json --out OUT.json --minutes 50   (passes every 4 min; exits early
       with code 0 after a new position or result so the caller can write the doc)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ODDS_MIN, ODDS_MAX, MIN_CHANCE = 1.50, 2.30, 0.60
MAX_AGE = timedelta(minutes=5)
OUTLIER = 1.08
LOOP_S = 240
REGLE = ("Le M en direct (basket) : une seule position par match, après le 1er quart-temps, cote de 1,50 à 2,30, "
         "au moins 60 % de chances pour le marché (marge retirée).")
LIVE = {"Q2", "Q3", "Q4", "OT", "BT", "HT"}  # first quarter over
DONE = {"FT", "AOT"}
CANCEL = {"POST", "CANC", "SUSP", "AWD", "ABD"}


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def odds_events() -> list[dict]:
    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        return []
    q = urllib.parse.urlencode({"apiKey": key, "regions": "eu", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"})
    with urllib.request.urlopen(f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds?{q}", timeout=30) as r:
        left = r.headers.get("x-requests-remaining")
        data = json.loads(r.read())
    if left:
        try:
            Path("/mnt/project-files/agent-sportif/state/odds_credits.txt").open("a").write(
                f"{left} credits left (M direct basket {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ})\n")
        except OSError:
            pass
    return data


def options(event: dict, now: datetime) -> list[dict]:
    """Every two-way option with its median no-margin chance and best (capped) odds, from fresh quotes only."""
    home, away = event["home_team"], event["away_team"]
    chances, prices = {}, {}
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") not in ("h2h", "spreads", "totals"):
                continue
            upd = m.get("last_update") or b.get("last_update")
            try:
                if now - datetime.fromisoformat(upd.replace("Z", "+00:00")) > MAX_AGE:
                    continue
            except (AttributeError, ValueError):
                continue
            outs = m.get("outcomes") or []
            if len(outs) != 2:
                continue
            if m["key"] == "spreads" and (outs[0].get("point") is None or outs[0]["point"] != -outs[1].get("point", 0)):
                continue
            if m["key"] == "totals" and outs[0].get("point") != outs[1].get("point"):
                continue
            inv = [1 / o["price"] for o in outs if o.get("price", 0) > 1]
            if len(inv) != 2:
                continue
            s = sum(inv)
            for o, i in zip(outs, inv):
                k = (m["key"], o["name"], o.get("point"))
                chances.setdefault(k, []).append(i / s)
                prices.setdefault(k, []).append(o["price"])
    out = []
    for k, ch in chances.items():
        med = statistics.median(prices[k])
        best = max(p for p in prices[k] if p <= med * OUTLIER)
        out.append({"cle": list(k), "chance": round(statistics.median(ch), 4), "cote": round(best, 2), "nb_sites": len(ch),
                    "position": label(k, home, away)})
    return out


def _pt(x: float) -> str:
    return f"{x:+g}".replace(".", ",")


def label(k, home, away) -> str:
    mk, name, pt = k
    if mk == "h2h":
        return f"Victoire {name}"
    if mk == "spreads":
        return f"{name} {_pt(pt)} points (handicap)"
    return f"{'Plus' if name == 'Over' else 'Moins'} de {pt:g} points dans le match".replace(".", ",")


def settle(cle, home: str, hs: int, as_: int) -> str:
    mk, name, pt = cle
    if mk == "h2h":
        mine, other = (hs, as_) if name == home else (as_, hs)
        return "gagne" if mine > other else "perdu"
    if mk == "spreads":
        mine, other = (hs, as_) if name == home else (as_, hs)
        d = mine + pt - other
        return "gagne" if d > 0 else "perdu" if d < 0 else "rembourse"
    t = hs + as_
    d = (t - pt) if name == "Over" else (pt - t)
    return "gagne" if d > 0 else "perdu" if d < 0 else "rembourse"


def _score(g) -> tuple[int | None, int | None]:
    s = g.get("scores") or {}
    return (s.get("home") or {}).get("total"), (s.get("away") or {}).get("total")


def ratio(doc: dict) -> dict:
    r = [m.get("resultat") for m in doc["matchs"].values() if m.get("position")]
    return {"gagnees": r.count("gagne"), "perdues": r.count("perdu"), "remboursees": r.count("rembourse"),
            "jouees": r.count("gagne") + r.count("perdu"), "positions": len(r)}


def cycle(jour: str, doc: dict, now: datetime | None = None, games=None, events=None) -> bool:
    """One pass; True when a new position or a result was written."""
    now = now or datetime.now(timezone.utc)
    if games is None:
        from scripts.agent_pass import _basket_api
        games = _basket_api().games_on(["NBA"], date.fromisoformat(jour), ttl=0)
    doc.setdefault("jour", jour)
    doc["regle"] = REGLE
    ms = doc.setdefault("matchs", {})
    changed = False
    live = []
    for g in games:
        gid, st = str(g["id"]), (g.get("status") or {}).get("short")
        home, away = g["teams"]["home"]["name"], g["teams"]["away"]["name"]
        row = ms.setdefault(gid, {"match": f"{home} - {away}", "coup_envoi": g.get("date")})
        hs, as_ = _score(g)
        if row.get("position") and not row.get("resultat"):
            if st in DONE and hs is not None and as_ is not None:
                row["resultat"], row["score_final"] = settle(row["cle"], home, hs, as_), f"{hs}-{as_}"
                changed = True
            elif st in CANCEL:
                row["resultat"] = "rembourse"
                changed = True
        if not row.get("position") and st in LIVE and hs is not None:
            live.append((g, row, home, away, hs, as_, st))
    if live:
        events = odds_events() if events is None else events
        by_teams = {(e["home_team"], e["away_team"]): e for e in events}
        for g, row, home, away, hs, as_, st in live:
            e = by_teams.get((home, away))
            if not e:
                continue
            cands = [o for o in options(e, now) if ODDS_MIN <= o["cote"] <= ODDS_MAX and o["chance"] >= MIN_CHANCE
                     and o["nb_sites"] >= 2]
            if not cands:
                continue
            o = max(cands, key=lambda o: (o["chance"], o["nb_sites"]))
            row.update({"position": o["position"], "cle": o["cle"], "cote": o["cote"], "proba_bookmakers": o["chance"],
                        "nb_sites": o["nb_sites"], "score_au_moment": f"{hs}-{as_}", "quart": st,
                        "heure": now.isoformat(timespec="minutes"), "resultat": None})
            changed = True
            print("M direct basket:", row["match"], row["score_au_moment"], st, o["position"], "@", o["cote"], flush=True)
            try:  # phone alert to the owner (admin only), once per position; never breaks the loop
                from sportai.services.alerte_m import alerter
                alerter("basket", f"{jour}|{g['id']}|{o['position']}", row["match"], o["position"], o["cote"],
                        f"{st}, {hs}-{as_}", heure=now, now=now)
            except Exception as ex:  # noqa: BLE001
                print("alerte M:", ex, flush=True)
    doc["ratio"] = ratio(doc)
    doc["maj"] = now.isoformat(timespec="seconds")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("cycle", "boucle", "registre"))
    ap.add_argument("--registre", help="registre: the page's registre/<jour> to patch with the M's live positions")
    ap.add_argument("--jour", required=True)
    ap.add_argument("--doc")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=50)
    a = ap.parse_args()
    doc = {}
    if a.doc and Path(a.doc).exists():
        d = json.loads(Path(a.doc).read_text())
        doc = d.get("data", d)
    if a.cmd == "registre":  # copy the live positions into the day's registry (avis + fiches), like tennis
        from sportai.services import registre as R
        d = json.loads(Path(a.registre).read_text())
        reg = d.get("data", d) if isinstance(d.get("data"), dict) else d
        n = 0
        for gid, row in (doc.get("matchs") or {}).items():
            a_ = (reg.get("avis") or {}).get(f"basket-{gid}")
            if a_ is not None and row.get("position"):
                R._basket_live_m(reg, a_, f"basket-{gid}", row, a_.get("coup_envoi"), "basket_direct")
                n += 1
        Path(a.out).write_text(json.dumps(reg, ensure_ascii=False))
        print(f"{n} position(s) en direct copiées dans le registre")
        return
    end = time.time() + a.minutes * 60
    while True:
        try:
            changed = cycle(a.jour, doc)
        except Exception as ex:  # noqa: BLE001
            print("cycle:", ex, flush=True)
            changed = False
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        if a.cmd == "cycle" or changed or time.time() + LOOP_S > end:
            print(json.dumps(doc["ratio"] if "ratio" in doc else {}, ensure_ascii=False))
            return
        time.sleep(LOOP_S)


if __name__ == "__main__":
    main()
