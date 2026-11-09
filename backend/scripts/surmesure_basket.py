"""« Sur-mesure » basket (owner 2026-11-09 07:22Z, via the coordinator), à blanc, beside the current test selection
(which is not touched): same-match combos built like the football ones.

Rule: when a clear favourite has a small odds, pair it on the same game with a total of points (« Celtics gagnent et
moins de 230,5 points »). Combined odds 1.60-2.00, at most 5 positions a night, one per game, the surest first,
frozen 45 min before tip-off. Own ratio, compared with the current test for a few days; the owner decides after.

Prices and chances come only from the bookmakers (API-Basketball odds feed, the one the test already uses):
- favourite: best odds at most FAV_MAX, margin-free chance (median of the sites) at least FAV_CHANCE;
- total: any half-point line, margin-free chance per site, median;
- the combo is not quoted in the feed: its odds = favourite odds x total odds (what a « même match » combo pays when
  the two legs are treated as independent; a site's own same-game price can differ), its chance = the product of the
  two market chances. Flagged cote_type « produit des deux cotes ». Combo chance at least MIN_CHANCE.

Player combos (passes + rebonds, points + rebonds): kept only if a French site quotes them. Each pass asks The Odds
API (region fr) for those markets and records what it finds under « joueurs_fr »; no player position is made
without a French price.

    python scripts/surmesure_basket.py passage --jour D --basket BASKET_PRONOSTIC.json --doc DOC.json --out DOC.json
    python scripts/surmesure_basket.py regler --doc DOC.json --out DOC.json

DOC is the page's surmesure_basket/<jour> document (absent the first time).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASKET = "https://v1.basketball.api-sports.io/"
MAX_PLACES = 5
ODDS_MIN, ODDS_MAX = 1.60, 2.00
FAV_MAX = 1.45          # « petite cote »
FAV_CHANCE = 0.68       # « favori net » for the market
MIN_LEG = 0.55          # each total leg at least this sure
MIN_CHANCE = 0.50       # the combo, like the football combos
OUTLIER = 1.08
FREEZE = timedelta(minutes=45)
PLAYER_MARKETS = ("player_points_rebounds", "player_rebounds_assists", "player_points_assists",
                  "player_points_rebounds_assists")
REGLE = ("Sur-mesure basket, à blanc, à côté de la sélection test : quand un favori net a une petite cote (1,45 au "
         "plus, au moins 68 % de chances pour le marché), on l'associe sur le même match à un total de points. "
         "Cote totale de 1,60 à 2,00, au moins 50 % de chances pour le marché, une position par match, 5 au plus, "
         "les plus sûres d'abord, figées 45 min avant le début. Combinés joueurs seulement avec une cote française.")


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _get(path: str, **q) -> dict:
    req = urllib.request.Request(BASKET + path + "?" + urllib.parse.urlencode(q),
                                 headers={"x-apisports-key": os.environ["API_FOOTBALL_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _pick(books: dict[str, float], opp: dict[str, float]) -> tuple[float, float, int] | None:
    """(best sane odds, median margin-free chance, number of sites) of one outcome."""
    rows = [(o, (1 / o) / (1 / o + 1 / opp[b])) for b, o in books.items() if b in opp]
    if not rows:
        return None
    med = statistics.median(o for o, _ in rows)
    return max(o for o, _ in rows if o <= med * OUTLIER), statistics.median(c for _, c in rows), len(rows)


def candidates(bookmakers: list[dict], home: str, away: str) -> list[dict]:
    """Every « favourite + total » combo of one game in the odds/chance window."""
    from sportai.services.basket import parse_odds
    q = parse_odds({"bookmakers": bookmakers})
    wh, wa = q.get(("winner", "home", None)) or {}, q.get(("winner", "away", None)) or {}
    out = []
    for side, team, mine, other in (("home", home, wh, wa), ("away", away, wa, wh)):
        fav = _pick(mine, other)
        if not fav or fav[0] > FAV_MAX or fav[1] < FAV_CHANCE:
            continue
        for (market, sel, line), books in q.items():
            if market != "total":
                continue
            tot = _pick(books, q.get(("total", "under" if sel == "over" else "over", line)) or {})
            if not tot or tot[1] < MIN_LEG:
                continue
            cote, chance = fav[0] * tot[0], fav[1] * tot[1]
            if not (ODDS_MIN <= cote <= ODDS_MAX) or chance < MIN_CHANCE:
                continue
            words = "plus" if sel == "over" else "moins"
            out.append({"cle": f"sm_{side}_{sel}_{line:g}", "pari": f"{team} gagne et {words} de {_fmt(line)} points",
                        "cote": round(cote, 2), "proba_marche": round(chance, 4),
                        "jambes": [{"pari": f"Victoire {team}", "cote": round(fav[0], 2),
                                    "proba_marche": round(fav[1], 4), "nb_sites": fav[2]},
                                   {"pari": f"{words.capitalize()} de {_fmt(line)} points dans le match",
                                    "cote": round(tot[0], 2), "proba_marche": round(tot[1], 4), "nb_sites": tot[2]}]})
    return out


def settle(cle: str, h: int, a: int) -> str:
    _, side, sel, line = cle.split("_")
    won = (h > a) if side == "home" else (a > h)
    tot = (h + a > float(line)) if sel == "over" else (h + a < float(line))
    return "gagne" if won and tot else "perdu"


def joueurs_fr(games: list[dict], now: datetime) -> dict:
    """Which French sites quote NBA player combos for tonight's games (The Odds API, region fr). An empty answer
    costs no credit."""
    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        return {"verifie_a": now.isoformat(timespec="minutes"), "erreur": "pas de clé"}
    base = "https://api.the-odds-api.com/v4/sports/basketball_nba/"
    try:
        with urllib.request.urlopen(base + "events?" + urllib.parse.urlencode({"apiKey": key}), timeout=30) as r:
            events = json.load(r)
    except Exception as ex:  # noqa: BLE001
        return {"verifie_a": now.isoformat(timespec="minutes"), "erreur": str(ex)[:120]}
    names = {(g["match"].split(" - ", 1)[0], g["match"].split(" - ", 1)[1]) for g in games}
    found = {}
    for e in events:
        if (e["home_team"], e["away_team"]) not in names:
            continue
        q = urllib.parse.urlencode({"apiKey": key, "regions": "fr", "markets": ",".join(PLAYER_MARKETS),
                                    "oddsFormat": "decimal"})
        try:
            with urllib.request.urlopen(f"{base}events/{e['id']}/odds?{q}", timeout=30) as r:
                d = json.load(r)
        except Exception:  # noqa: BLE001
            continue
        for b in d.get("bookmakers") or []:
            for m in b.get("markets") or []:
                found.setdefault(f"{e['home_team']} - {e['away_team']}", {}).setdefault(m["key"], []).append(b["key"])
    return {"verifie_a": now.isoformat(timespec="minutes"), "marches": PLAYER_MARKETS, "trouve": found,
            "disponible": bool(found)}


def passage(day: str, basket_doc: dict, doc: dict, now: datetime) -> dict:
    doc = {"jour": day, "positions": [], **doc}
    doc["regle"] = REGLE
    kept = [p for p in doc["positions"]
            if p.get("fige") or now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE]
    for p in kept:
        p["fige"] = True
    taken = {p["match_id"] for p in kept}
    games, pool, scanned = list((basket_doc.get("paris_retenus") or {}).values()), [], []
    for g in games:
        mid, ko = str(g["match_id"]), g["coup_envoi"].replace("Z", "+00:00")
        if mid in taken or now >= datetime.fromisoformat(ko) - FREEZE:
            continue
        resp = _get("odds", game=mid).get("response")
        scanned.append(g["match"])
        if not resp:
            continue
        home, away = g["match"].split(" - ", 1)
        cands = candidates(resp[0].get("bookmakers") or [], home, away)
        if cands:
            best = max(cands, key=lambda c: c["proba_marche"])
            pool.append({"match": g["match"], "match_id": mid, "competition": g.get("competition"),
                         "coup_envoi": ko, **best})
    pool.sort(key=lambda b: -b["proba_marche"])
    fresh = []
    for b in pool[: max(0, MAX_PLACES - len(kept))]:
        j1, j2 = b["jambes"]
        fresh.append({"sport": "basket", **b, "cote_type": "produit des deux cotes", "resultat": None, "fige": False,
                      "choisi_a": now.isoformat(timespec="minutes"),
                      "explication": (f"Favori net : le marché donne {round(j1['proba_marche'] * 100)} % à "
                                      f"« {j1['pari']} » (cote {_fmt(j1['cote'])}), et "
                                      f"{round(j2['proba_marche'] * 100)} % à « {j2['pari']} » (cote "
                                      f"{_fmt(j2['cote'])}). Ensemble : {round(b['proba_marche'] * 100)} %, cote "
                                      f"{_fmt(b['cote'])} (produit des deux cotes).")})
    doc["positions"] = sorted(kept + fresh, key=lambda p: p["coup_envoi"])
    doc["matchs_etudies"] = sorted(set(doc.get("matchs_etudies") or []) | set(scanned))
    doc["joueurs_fr"] = joueurs_fr(games, now)
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def regler(doc: dict, now: datetime) -> dict:
    for p in doc.get("positions") or []:
        if now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE:
            p["fige"] = True
        if p.get("resultat"):
            continue
        g = (_get("games", id=p["match_id"]).get("response") or [None])[0]
        if not g:
            continue
        st = g["status"]["short"]
        if st in ("FT", "AOT"):  # overtime counts, as on every NBA market
            h, a = g["scores"]["home"]["total"], g["scores"]["away"]["total"]
            p["score"], p["resultat"] = f"{h}-{a}", settle(p["cle"], h, a)
        elif st in ("POST", "CANC", "ABD", "AWD"):
            p["resultat"], p["score"] = "rembourse", st
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def _ratio(doc: dict) -> dict:
    ps = doc.get("positions") or []
    g = sum(p.get("resultat") == "gagne" for p in ps)
    l = sum(p.get("resultat") == "perdu" for p in ps)
    doc["ratio"] = {"gagnees": g, "perdues": l, "jouees": g + l,
                    "remboursees": sum(p.get("resultat") == "rembourse" for p in ps), "positions": len(ps)}
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("passage", "regler"))
    ap.add_argument("--jour")
    ap.add_argument("--basket", help="the page's basket_pronostic/<jour> document")
    ap.add_argument("--doc")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    doc = json.loads(Path(a.doc).read_text()) if a.doc and Path(a.doc).exists() else {}
    doc = doc.get("data", doc)
    for k in ("version", "id", "updated_at", "created_at"):
        doc.pop(k, None)
    if a.cmd == "passage":
        b = json.loads(Path(a.basket).read_text())
        doc = passage(a.jour, b.get("data", b), doc, now)
    doc = regler(doc, now)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for p in doc["positions"]:
        print(p["match"], "|", p["pari"], p["cote"], p["proba_marche"], p.get("resultat"))
    print("ratio", doc["ratio"], "| joueurs FR:", (doc.get("joueurs_fr") or {}).get("disponible"))


if __name__ == "__main__":
    main()
