"""Cote Winamax (site agréé en France) de nos positions, via OddsPapi. LECTURE SEULE.

Ne touche ni au modèle ni aux positions. Pour les positions du jour dont le match commence
dans les prochaines heures, lit la cote Winamax avant-match du même marché à la même ligne et
l'ajoute dans cotes-fr/<jour>.json (champ « winamax » de chaque position).

Garde-fou : compteur mensuel de requêtes (cotes-fr/oddspapi/compteur.json), arrêt à --plafond
(3 800 par défaut, la formule en a 5 000 et ~1 000 sont gardées pour le hockey).

Usage : python3 -I cotes_winamax.py --jour 2026-11-18 [--horizon 4] [--plafond 3800]
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cotes_fr import num, positions, same_name  # noqa: E402

API = "https://api.oddspapi.io/v4"
BOOK = "winamax.fr"
SPORT_ID = {"foot": 10, "basket": 11, "tennis": 12}
PERIOD = {"foot": "fulltime", "basket": "result", "tennis": "result"}


class Budget:
    def __init__(self, fn, plafond):
        self.fn, self.plafond = fn, plafond
        self.mois = dt.date.today().strftime("%Y-%m")
        self.d = json.load(open(fn)) if os.path.exists(fn) else {}
        self.d.setdefault(self.mois, 0)

    def ok(self):
        return self.d[self.mois] < self.plafond

    def add(self):
        self.d[self.mois] += 1
        json.dump(self.d, open(self.fn, "w"))


def get(budget, path, **q):
    if not budget.ok():
        raise RuntimeError("plafond mensuel de requêtes atteint")
    q["apiKey"] = os.environ["ODDSPAPI_KEY"]
    url = f"{API}{path}?{urllib.parse.urlencode(q)}"
    for _ in range(3):
        budget.add()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; releve-cotes/1.0)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                time.sleep(3)
                continue
            raise
        if isinstance(d, dict) and (d.get("error") or {}).get("code") == "RATE_LIMITED":
            time.sleep(1.5)
            continue
        if isinstance(d, dict) and d.get("error"):
            raise RuntimeError(d["error"].get("message"))
        return d
    raise RuntimeError("OddsPapi : trop de requêtes rapprochées")


def parse(sport, pari, home, away):
    """-> (marketType, handicap ou None, nom d'issue) pour Winamax, ou None."""
    p = pari.strip()

    def side(name):
        if same_name(name, home):
            return 1
        if same_name(name, away):
            return 2
        return None

    unit = {"foot": "buts", "basket": "points dans le match", "tennis": "jeux dans le match"}[sport]
    m = re.fullmatch(r"(Moins|Plus) de ([\d.,]+) " + re.escape(unit), p)
    if m:
        t = "totals-games" if sport == "tennis" else "totals"
        return t, num(m[2]), "Under" if m[1] == "Moins" else "Over"
    m = re.fullmatch(r"Victoire (.+)", p) or re.fullmatch(r"(.+) gagne le match", p)
    if m and " et " not in m[1] and side(m[1]):
        return ("1x2" if sport == "foot" else "moneyline"), 0.0, str(side(m[1]))
    if sport == "foot":
        m = re.fullmatch(r"(.+) ou nul", p)
        if m and side(m[1]):
            return "doublechance", 0.0, "1X" if side(m[1]) == 1 else "2X"
        m = re.fullmatch(r"(.+) \(remboursé si nul\)", p)
        if m and side(m[1]):
            return "drawnobet", 0.0, str(side(m[1]))
        if re.fullmatch(r"Les deux équipes marquent( : oui)?", p):
            return "bothteamsscore", 0.0, "Yes"
        if p == "Nombre de buts pair":
            return "oddeven", 0.0, "Even"
        m = re.fullmatch(r"(.+?) : (plus|moins) de ([\d.,]+)( buts)?( dans le match)?", p)
        if m and side(m[1]):
            return f"teamtotals-team{side(m[1])}", num(m[3]), "Over" if m[2] == "plus" else "Under"
        m = re.fullmatch(r"(.+) handicap asiatique ([+-][\d.,]+)", p)
        if m and side(m[1]):
            h = num(m[2])
            return "spreads", (h if side(m[1]) == 1 else -h), str(side(m[1]))
        return None
    if sport == "basket":
        m = re.fullmatch(r"(.+?) (?:handicap )?([+-][\d.,]+) points(?: \(handicap\))?", p)
        if m and side(m[1]):
            h = num(m[2])
            return "spreads", (h if side(m[1]) == 1 else -h), str(side(m[1]))
        m = re.fullmatch(r"(.+?) : (plus|moins) de ([\d.,]+) points", p)
        if m and side(m[1]):
            return f"teamtotals-team{side(m[1])}", num(m[3]), "Over" if m[2] == "plus" else "Under"
        return None
    # tennis
    m = re.fullmatch(r"(.+?) handicap ([+-][\d.,]+) jeux", p)
    if m and side(m[1]):
        h = num(m[2])
        return "spreads-games", (h if side(m[1]) == 1 else -h), str(side(m[1]))
    m = re.fullmatch(r"(.+) gagne au moins un set", p)
    if m and side(m[1]):
        return f"winaset-team{side(m[1])}", 0.0, "Yes"
    m = re.fullmatch(r"(.+) gagne (\d) sets? à (\d)", p)
    if m and side(m[1]):
        a, b = m[2], m[3]
        if (a, b) == ("2", "0"):  # gagner 2-0 = handicap sets -1,5 (marché proposé en France)
            return "spreads", (-1.5 if side(m[1]) == 1 else 1.5), str(side(m[1]))
        return "correctscore", 0.0, f"{a}:{b}" if side(m[1]) == 1 else f"{b}:{a}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jour", required=True)
    ap.add_argument("--base", default="/mnt/project-files/agent-sportif")
    ap.add_argument("--horizon", type=float, default=4.0, help="heures à venir couvertes")
    ap.add_argument("--plafond", type=int, default=3800)
    a = ap.parse_args()
    cdir = f"{a.base}/cotes-fr"
    odir = f"{cdir}/oddspapi"
    os.makedirs(odir, exist_ok=True)
    budget = Budget(f"{odir}/compteur.json", a.plafond)
    markets = json.load(gzip.open(f"{odir}/markets.json.gz", "rt"))
    idx = {}
    for m in markets:
        idx[(m["sportId"], m["period"], m["marketType"], round(float(m["handicap"]), 2))] = m

    out_fn = f"{cdir}/{a.jour}.json"
    data = json.load(open(out_fn)) if os.path.exists(out_fn) else {"jour": a.jour, "positions": {}}
    now = dt.datetime.now(dt.timezone.utc)
    todo = []
    for p in positions(a.base, a.jour):
        ko = dt.datetime.fromisoformat(p["coup_envoi"].replace("Z", "+00:00"))
        if not (now < ko <= now + dt.timedelta(hours=a.horizon)):
            continue
        key = f"{p['liste']}|{p['sport']}|{p['acteur']}|{p['match_id']}|{p['pari']}"
        rec = data["positions"].get(key) or {k: p.get(k) for k in ("liste", "sport", "acteur", "match", "competition", "coup_envoi", "pari", "cote", "resultat")}
        if rec.get("winamax"):
            continue
        data["positions"][key] = rec
        todo.append((p, rec))

    fixtures, odds, used0 = {}, {}, budget.d[budget.mois]
    erreurs = []
    for p, rec in todo:
        sport = p["sport"]
        if sport not in fixtures:
            fx_fn = f"{odir}/fixtures_{a.jour}_{sport}.json"
            if os.path.exists(fx_fn):
                fixtures[sport] = json.load(open(fx_fn))
            else:
                try:
                    start = dt.datetime.fromisoformat(a.jour + "T00:00:00+00:00") - dt.timedelta(hours=2)
                    fixtures[sport] = get(budget, "/fixtures", sportId=SPORT_ID[sport],
                                          **{"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             "to": (start + dt.timedelta(hours=34)).strftime("%Y-%m-%dT%H:%M:%SZ")})
                    json.dump(fixtures[sport], open(fx_fn, "w"))
                except Exception as e:  # noqa: BLE001
                    erreurs.append(str(e)); fixtures[sport] = []
        home, _, away = p["match"].partition(" - ")
        ko = dt.datetime.fromisoformat(p["coup_envoi"].replace("Z", "+00:00"))
        fx, swap = None, False
        for f in fixtures[sport]:
            ft = dt.datetime.fromisoformat(f["startTime"].replace("Z", "+00:00"))
            if abs((ft - ko).total_seconds()) > 3 * 3600:
                continue
            if same_name(home, f["participant1Name"]) and same_name(away, f["participant2Name"]):
                fx = f; break
            if same_name(home, f["participant2Name"]) and same_name(away, f["participant1Name"]):
                fx, swap = f, True; break
        if not fx:
            rec["winamax_etat"] = "match introuvable"
            continue
        h, aw = (fx["participant1Name"], fx["participant2Name"])
        parsed = parse(sport, p["pari"], h, aw)
        if not parsed:
            rec["winamax_etat"] = "marché non lu"
            continue
        if fx["fixtureId"] not in odds:
            time.sleep(1.2)  # OddsPapi limite les requêtes rapprochées
            try:
                d = get(budget, "/odds", fixtureId=fx["fixtureId"], bookmakers=BOOK)
                odds[fx["fixtureId"]] = ((d.get("bookmakerOdds") or {}).get(BOOK) or {}).get("markets") or {}
            except Exception as e:  # noqa: BLE001
                erreurs.append(str(e)); odds[fx["fixtureId"]] = None
        bm = odds[fx["fixtureId"]]
        if bm is None:
            continue
        mtype, hcp, outname = parsed
        mk = idx.get((SPORT_ID[sport], PERIOD[sport], mtype, round(hcp or 0.0, 2)))
        price = None
        if mk and str(mk["marketId"]) in bm:
            oid = {o["outcomeName"]: str(o["outcomeId"]) for o in mk["outcomes"]}.get(outname)
            o = (bm[str(mk["marketId"])].get("outcomes") or {}).get(oid) or {}
            pl = (o.get("players") or {}).get("0") or {}
            if pl.get("active", True) and pl.get("price"):
                price = pl["price"]
        if price:
            rec["winamax"] = {"cote": price, "releve_a": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            rec["winamax_etat"] = "ok"
            rec["winamax_ecart_pct"] = round(100 * (price / p["cote"] - 1), 1)
        else:
            rec["winamax_etat"] = "pas proposé par Winamax"
    data["maj_winamax"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(data, open(out_fn, "w"), ensure_ascii=False, indent=1)
    n = sum(1 for _, r in todo if r.get("winamax"))
    print(f"{a.jour}: {len(todo)} positions à relever, {n} cotes Winamax ; requêtes {budget.d[budget.mois] - used0} "
          f"(mois {budget.d[budget.mois]}/{a.plafond})" + (f" ; erreurs : {erreurs[:2]}" if erreurs else ""))


if __name__ == "__main__":
    main()
