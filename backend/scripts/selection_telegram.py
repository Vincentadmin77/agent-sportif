"""A blank, tipster-style evening selection (owner 2026-11-05 07:11Z, decision card): at most 5 positions per evening,
only on matches that hold together, mostly same-match « équipe ou nul + buts » combos (also both teams score, over
1.5 first half, draw no bet, team + goals), total odds 1.60-2.00, high market chance. No straight wins. Own ratio,
never mixed with the model, the M or the specialists; compared with the Telegram tipster at the Monday bilan.

Prices and chances come only from the bookmakers' own quotes, never from our model:
- a quoted market: best odds of any bookmaker; market chance = each bookmaker's margin-free chance, median;
- « équipe ou nul + buts » is not quoted in the feed: it is the sum of two quoted outcomes of the same bookmaker
  (« équipe/plus de X » + « nul/plus de X »), so its price is the one obtained by covering both outcomes at that
  bookmaker (dutching): 1 / (1/o1 + 1/o2). Flagged cote_type « couverture de deux issues ».

    python scripts/selection_telegram.py passage --jour 2026-11-05 --ligues 3 --doc DOC.json --out DOC.json
    python scripts/selection_telegram.py regler --doc DOC.json --out DOC.json

DOC is the page's selection_telegram/<jour> document (absent the first time). Positions already frozen (kickoff
-45 min passed at a pass) are never changed. API_FOOTBALL_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://v3.football.api-sports.io/"
MAX_PLACES = 5
ODDS_MIN, ODDS_MAX = 1.60, 2.00
MIN_CHANCE = 0.55  # both teams score, first-half goals, draw no bet
MIN_CHANCE_COMBO = 0.50  # same-match combos (team or draw + goals, team + goals)
FREEZE = timedelta(minutes=45)
REGLE = ("Méthode à part (ni le modèle, ni le M) : au plus 5 positions par soir sur les matchs les plus cohérents, "
         "d'abord des combinés sur un même match (équipe ou nul + buts, équipe + buts) à au moins 50 % de chance "
         "selon le marché, sinon les deux équipes marquent, plus de 1,5 but en 1re mi-temps ou remboursé si nul à "
         "au moins 55 %, cote 1,60-2,00, pas de victoire sèche, équipes instables écartées d'après leurs 5 derniers "
         "matchs. À blanc, ratio à part.")


def _get(path: str, **q) -> dict:
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(q),
                                 headers={"x-apisports-key": os.environ["API_FOOTBALL_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


# ---- candidate markets: key -> (label, settle) -------------------------------------------------------------------
def _outcomes(bk: dict) -> dict:
    """One bookmaker's quotes as {(bet_id, value): odds}."""
    out = {}
    for b in bk.get("bets") or []:
        for v in b.get("values") or []:
            o = _num(v.get("odd"))
            if o:
                out[(b["id"], str(v.get("value")))] = o
    return out


def _fair(q: dict, keys: list) -> dict | None:
    """Margin-free chances of a complete set of outcomes of one bookmaker (None when one is missing)."""
    if not all(k in q for k in keys):
        return None
    s = sum(1 / q[k] for k in keys)
    return {k: (1 / q[k]) / s for k in keys}


def _candidates(q: dict, home: str, away: str) -> list[dict]:
    """All tipster-style options one bookmaker prices: {cle, pari, cote, chance} (chance may be None)."""
    c = []
    # both teams score, draw no bet, first-half goals
    f = _fair(q, [(8, "Yes"), (8, "No")])
    if f:
        c.append({"cle": "btts", "pari": "Les deux équipes marquent", "cote": q[(8, "Yes")], "chance": f[(8, "Yes")]})
    f = _fair(q, [(1, "Home"), (1, "Draw"), (1, "Away")])  # draw no bet: the chance to WIN (a draw only refunds)
    for side, team in (("Home", home), ("Away", away)):
        if (2, side) in q:
            c.append({"cle": f"dnb_{side[0].lower()}", "pari": f"{team} (remboursé si nul)", "cote": q[(2, side)],
                      "chance": f[(1, side)] if f else None})
    f = _fair(q, [(6, "Over 1.5"), (6, "Under 1.5")])
    if f:
        c.append({"cle": "ht_o1.5", "pari": "Plus de 1,5 but en 1re mi-temps", "cote": q[(6, "Over 1.5")],
                  "chance": f[(6, "Over 1.5")]})
    # result / total goals, per line; team-or-draw + goals by covering two outcomes of the same bookmaker
    for line in ("1.5", "2.5", "3.5", "4.5"):
        keys = [(25, f"{r}/{s} {line}") for r in ("Home", "Draw", "Away") for s in ("Over", "Under")]
        f = _fair(q, keys)
        for side in ("Over", "Under"):
            if line == "4.5" and side == "Over" or line == "1.5" and side == "Under":
                continue
            word = "plus" if side == "Over" else "moins"
            goals = f"{word} de {_fmt(float(line))} buts"
            for r, team in (("Home", home), ("Away", away)):
                k = (25, f"{r}/{side} {line}")
                if k in q:
                    c.append({"cle": f"{r[0].lower()}_{side[0].lower()}{line}", "pari": f"Victoire {team} et {goals}",
                              "cote": q[k], "chance": f[k] if f else None})
                kd = (25, f"Draw/{side} {line}")
                if k in q and kd in q:
                    cote = 1 / (1 / q[k] + 1 / q[kd])
                    c.append({"cle": f"{r[0].lower()}x_{side[0].lower()}{line}", "pari": f"{team} ou nul et {goals}",
                              "cote": round(cote, 3), "chance": (f[k] + f[kd]) if f else None,
                              "cote_type": "couverture de deux issues"})
    return c


def _settle(cle: str, h: int, a: int, hh: int | None, ha: int | None) -> str | None:
    t = h + a
    if cle == "btts":
        return "gagne" if h and a else "perdu"
    if cle in ("dnb_h", "dnb_a"):
        if h == a:
            return "rembourse"
        return "gagne" if (h > a) == (cle == "dnb_h") else "perdu"
    if cle == "ht_o1.5":
        return None if hh is None else ("gagne" if hh + ha >= 2 else "perdu")
    res, goals = cle.split("_")
    side, line = goals[0], float(goals[1:])
    ok_goals = t > line if side == "o" else t < line
    ok_res = {"h": h > a, "a": a > h, "hx": h >= a, "ax": a >= h}[res]
    return "gagne" if ok_goals and ok_res else "perdu"


def _form(team_id: int, before: str) -> str:
    """Last 5 results of a team before the day, newest first, as « V N D » letters."""
    rows = _get("fixtures", team=team_id, last=5, status="FT").get("response") or []
    out = []
    for r in rows:
        if r["fixture"]["date"][:10] >= before:
            continue
        g = r["goals"]
        mine, other = (g["home"], g["away"]) if r["teams"]["home"]["id"] == team_id else (g["away"], g["home"])
        out.append("V" if mine > other else "N" if mine == other else "D")
    return " ".join(out)


def _backed(cle: str) -> str | None:
    """Which team a candidate backs (h or a) and how: « win », « or draw », « draw no bet »; None for goals only."""
    if cle.startswith("dnb_"):
        return cle[4] + ":dnb"
    res = cle.split("_")[0]
    return {"h": "h:win", "a": "a:win", "hx": "h:draw", "ax": "a:draw"}.get(res)


def _form_ok(cle: str, fh: str, fa: str) -> bool:
    """Skip unstable teams (owner 2026-11-05): a win needs 3 wins in the last 5, « ou nul » at most 2 defeats,
    « remboursé si nul » 2 wins and at most 2 defeats."""
    b = _backed(cle)
    if not b:
        return True
    form = (fh if b[0] == "h" else fa).split()
    if len(form) < 5:
        return False
    w, l = form.count("V"), form.count("D")
    return {"win": w >= 3, "draw": l <= 2, "dnb": w >= 2 and l <= 2}[b[2:]]


def _is_combo(cle: str) -> bool:
    return cle not in ("btts", "ht_o1.5") and not cle.startswith("dnb_")


def passage(day: str, leagues: list[int], doc: dict, now: datetime) -> dict:
    doc = {"jour": day, "regle": REGLE, "positions": [], **doc}
    doc["regle"] = REGLE
    kept = [p for p in doc["positions"] if p.get("fige") or now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE]
    for p in kept:
        p["fige"] = True
    taken = {p["match_id"] for p in kept}
    pool, scanned = [], []
    for lg in leagues:
        season = int(day[:4]) if int(day[5:7]) >= 7 else int(day[:4]) - 1
        for fx in _get("fixtures", league=lg, season=season, date=day, timezone="Europe/Paris").get("response") or []:
            fid, ko = fx["fixture"]["id"], fx["fixture"]["date"]
            if fid in taken or now >= datetime.fromisoformat(ko) - FREEZE:
                continue
            home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
            fh, fa = _form(fx["teams"]["home"]["id"], day), _form(fx["teams"]["away"]["id"], day)
            odds = (_get("odds", fixture=fid).get("response") or [{}])[0].get("bookmakers") or []
            by_key: dict[str, list] = {}
            for bk in odds:
                for cand in _candidates(_outcomes(bk), home, away):
                    by_key.setdefault(cand["cle"], []).append(cand)
            best = None
            for cle, rows in by_key.items():
                chances = [r["chance"] for r in rows if r["chance"] is not None]
                top = max(rows, key=lambda r: r["cote"])
                if not chances or not (ODDS_MIN <= top["cote"] <= ODDS_MAX):
                    continue
                cand = {**top, "chance": round(statistics.median(chances), 4), "nb_bookmakers": len(rows)}
                combo = _is_combo(cle)
                if cand["chance"] < (MIN_CHANCE_COMBO if combo else MIN_CHANCE) or not _form_ok(cle, fh, fa):
                    continue
                if best is None or (combo, cand["chance"]) > (_is_combo(best["cle"]), best["chance"]):
                    best = cand
            scanned.append(f"{home} - {away}")
            if best:
                pool.append({"fx": fx, "home": home, "away": away, "best": best, "fh": fh, "fa": fa})
    pool.sort(key=lambda x: (not _is_combo(x["best"]["cle"]), -x["best"]["chance"]))
    fresh = []
    for x in pool[: max(0, MAX_PLACES - len(kept))]:
        fx, b, fh, fa = x["fx"], x["best"], x["fh"], x["fa"]
        how = (" Cote obtenue en couvrant les deux issues chez le même site." if b.get("cote_type") else "")
        fresh.append({
            "sport": "foot", "match": f"{x['home']} - {x['away']}", "match_id": fx["fixture"]["id"],
            "competition": fx["league"]["name"], "coup_envoi": fx["fixture"]["date"], "pari": b["pari"],
            "cle": b["cle"], "cote": round(b["cote"], 2), "proba_marche": b["chance"],
            "cote_type": b.get("cote_type", "cotée"), "nb_sites": b["nb_bookmakers"],
            "forme_5": {"domicile": fh, "exterieur": fa},
            "explication": (f"Le marché donne {round(b['chance'] * 100)} % de chances à « {b['pari']} » "
                            f"(cote {_fmt(round(b['cote'], 2))}). Forme sur 5 matchs (du plus récent) : "
                            f"{x['home']} {fh or '?'}, {x['away']} {fa or '?'}.{how}"),
            "resultat": None, "fige": False, "choisi_a": now.isoformat(timespec="minutes")})
    doc["positions"] = kept + fresh
    doc["matchs_etudies"] = sorted(set(doc.get("matchs_etudies") or []) | set(scanned))
    doc["non_cote"] = ("« Équipe ou nul + buts » n'est pas proposé tel quel dans nos cotes : prix obtenu en couvrant "
                       "les deux issues chez le même site.")
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def regler(doc: dict, now: datetime) -> dict:
    for p in doc.get("positions") or []:
        if p.get("resultat"):
            continue
        fx = (_get("fixtures", id=p["match_id"]).get("response") or [None])[0]
        if not fx or fx["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
            continue
        sc = fx["score"]
        h, a = sc["fulltime"]["home"], sc["fulltime"]["away"]  # 90 minutes
        p["score"] = f"{h}-{a}"
        p["resultat"] = _settle(p["cle"], h, a, sc["halftime"]["home"], sc["halftime"]["away"])
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def _ratio(doc: dict) -> dict:
    s = [p for p in doc.get("positions") or [] if p.get("resultat") in ("gagne", "perdu")]
    doc["ratio"] = {"gagnes": sum(p["resultat"] == "gagne" for p in s), "joues": len(s),
                    "rembourses": sum(p.get("resultat") == "rembourse" for p in doc.get("positions") or [])}
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("passage", "regler"))
    ap.add_argument("--jour")
    ap.add_argument("--ligues", default="3")
    ap.add_argument("--doc")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    doc = json.loads(Path(a.doc).read_text()) if a.doc and Path(a.doc).exists() else {}
    doc = doc.get("data", doc)
    for k in ("version", "id", "updated_at", "created_at"):
        doc.pop(k, None)
    if a.cmd == "passage":
        doc = passage(a.jour, [int(x) for x in a.ligues.split(",")], doc, now)
    else:
        doc = regler(doc, now)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(json.dumps([(p["match"], p["pari"], p["cote"], p["proba_marche"], p["resultat"]) for p in doc["positions"]],
                     ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
