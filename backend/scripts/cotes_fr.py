"""Relevé des cotes des sites agréés en France pour nos positions figées (LECTURE SEULE).

Ne modifie ni le modèle ni les positions : lit top5/<jour>.json et version-finale/<jour>.json,
interroge The Odds API (région « fr » : vainqueur, total principal, handicap principal),
et écrit cotes-fr/<jour>.json avec, pour chaque position, la cote française quand le même
marché à la même ligne existe en France.

Usage :
  python3 cotes_fr.py direct  --jour 2026-11-16            # cotes FR actuelles (matchs pas encore commencés)
  python3 cotes_fr.py passe   --jour 2026-11-12            # cotes FR à H-45 (historique, 10 crédits/marché)
Options : --base /mnt/project-files/agent-sportif  --out <dossier>  --max-credits 400
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import unicodedata
import urllib.parse
import urllib.request

API = "https://api.the-odds-api.com/v4"

FOOT_KEYS = {
    "Ligue des Nations": "soccer_uefa_nations_league", "UEFA Nations League": "soccer_uefa_nations_league",
    "Coupe de France": "soccer_france_coupe_de_france", "Ligue 1": "soccer_france_ligue_one",
    "Serie A": "soccer_italy_serie_a", "Bundesliga": "soccer_germany_bundesliga",
    "Brasileirão": "soccer_brazil_campeonato", "Liga Portugal": "soccer_portugal_primeira_liga",
    "Primeira Liga": "soccer_portugal_primeira_liga", "LaLiga": "soccer_spain_la_liga",
    "FA Cup": "soccer_fa_cup", "Süper Lig": "soccer_turkey_super_league", "Premier League": "soccer_epl",
}
MARKETS = {"foot": "h2h,totals", "basket": "h2h,spreads,totals", "tennis": "h2h,spreads,totals"}
# historique : 10 crédits par marché, on garde les marchés où nos positions trouvent le plus souvent un équivalent
MARKETS_PASSE = {"foot": "totals", "basket": "h2h,spreads,totals", "tennis": "h2h,totals"}
ALIASES = {"fyr macedonia": "north macedonia"}


def sport_key(sport: str, comp: str | None) -> str | None:
    if sport == "basket":
        return "basketball_nba" if comp == "NBA" else None
    if sport == "foot":
        return FOOT_KEYS.get(comp or "")
    c = (comp or "").lower()
    if "riyadh" in c or ("finals" in c and "wta" in c):
        return "tennis_wta_finals"
    if "turin" in c:
        return "tennis_atp_finals"
    return None  # tournoi non couvert en France par notre source


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = " ".join(s.split())
    return ALIASES.get(s, s)


def same_name(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    if a == b or a in b or b in a:
        return True
    ta, tb = a.split(), b.split()
    # tennis « C. Alcaraz » vs « Carlos Alcaraz » : même nom de famille
    if ta and tb and ta[-1] == tb[-1] and len(ta[-1]) > 2:
        return True
    big = {w for w in ta if len(w) > 3} & {w for w in tb if len(w) > 3}
    return bool(big)


def num(x: str) -> float:
    return float(x.replace(",", "."))


def parse(sport: str, pari: str):
    """Renvoie (marché, nom de l'issue, ligne) ou None si le marché n'existe pas en France dans notre source."""
    p = pari.strip()
    unit = {"foot": "buts", "basket": "points dans le match", "tennis": "jeux dans le match"}[sport]
    m = re.fullmatch(r"(Moins|Plus) de ([\d.,]+) " + re.escape(unit), p)
    if m:
        return "totals", "Under" if m[1] == "Moins" else "Over", num(m[2])
    m = re.fullmatch(r"Victoire (.+)", p)
    if m and " et " not in m[1]:
        return "h2h", m[1], None
    m = re.fullmatch(r"(.+) gagne le match", p)
    if m:
        return "h2h", m[1], None
    if sport in ("basket", "tennis"):
        m = re.fullmatch(r"(.+?) (?:handicap )?([+-][\d.,]+) (?:points|jeux)(?: \(handicap\))?", p)
        if m:
            return "spreads", m[1], num(m[2])
    return None


def get(path: str, **q):
    q["apiKey"] = os.environ["THE_ODDS_API_KEY"]
    url = f"{API}{path}?{urllib.parse.urlencode(q)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        used = int(r.headers.get("x-requests-last") or 0)
        left = r.headers.get("x-requests-remaining")
        return json.load(r), used, left


def positions(base: str, jour: str):
    out = []
    for src, fn in (("top5", f"{base}/top5/{jour}.json"), ("version_finale", f"{base}/version-finale/{jour}.json")):
        if not os.path.exists(fn):
            continue
        d = json.load(open(fn))
        for sport in ("foot", "basket", "tennis"):
            for acteur, w in (d.get(sport) or {}).items():
                if isinstance(w, dict):
                    for p in w.get("positions") or []:
                        if p.get("cote"):
                            out.append({"liste": src, "sport": sport, "acteur": acteur, **p})
    return out


def find_event(events, match: str, ko: str):
    home, _, away = match.partition(" - ")
    t = dt.datetime.fromisoformat(ko.replace("Z", "+00:00"))
    for e in events:
        et = dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        if abs((et - t).total_seconds()) > 3 * 3600:
            continue
        if same_name(home, e["home_team"]) and same_name(away, e["away_team"]):
            return e, False
        if same_name(home, e["away_team"]) and same_name(away, e["home_team"]):
            return e, True
    return None, False


def fr_prices(event, market, name, point):
    rows = {}
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m["key"] != market:
                continue
            for o in m["outcomes"]:
                if market == "totals":
                    ok = o["name"] == name and abs((o.get("point") or 0) - point) < 1e-6
                elif market == "spreads":
                    ok = same_name(name, o["name"]) and abs((o.get("point") or 0) - point) < 1e-6
                else:
                    ok = same_name(name, o["name"])
                if ok:
                    rows[b["key"]] = o["price"]
    return rows


def lines_available(event, market, name):
    pts = set()
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m["key"] == market:
                for o in m["outcomes"]:
                    if (market == "totals" and o["name"] == name) or (market == "spreads" and same_name(name, o["name"])):
                        pts.add(o.get("point"))
    return sorted(p for p in pts if p is not None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["direct", "passe"])
    ap.add_argument("--jour", required=True)
    ap.add_argument("--base", default="/mnt/project-files/agent-sportif")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-credits", type=int, default=400)
    a = ap.parse_args()
    out_dir = a.out or f"{a.base}/cotes-fr"
    os.makedirs(out_dir, exist_ok=True)
    out_fn = f"{out_dir}/{a.jour}.json"
    prev = json.load(open(out_fn)) if os.path.exists(out_fn) else {"positions": {}}
    now = dt.datetime.now(dt.timezone.utc)

    pos = positions(a.base, a.jour)
    # historique : un relevé à H-45 du premier match d'un groupe de 3 h (le relevé contient aussi les matchs suivants)
    snaps: dict = {}
    by_key: dict = {}
    for p in pos:
        sk = sport_key(p["sport"], p.get("competition"))
        if sk and parse(p["sport"], p["pari"]):
            by_key.setdefault(sk, set()).add(p["coup_envoi"])
    for sk, kos in by_key.items():
        start = None
        for k in sorted(kos, key=lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00"))):
            t = dt.datetime.fromisoformat(k.replace("Z", "+00:00")) - dt.timedelta(minutes=45)
            if start is None or (t - start) > dt.timedelta(hours=3):
                start = t.replace(second=0, microsecond=0, minute=(t.minute // 5) * 5)
            snaps[(sk, k)] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    credits = 0
    cache: dict = {}
    for p in pos:
        key = f"{p['liste']}|{p['sport']}|{p['acteur']}|{p['match_id']}|{p['pari']}"
        old = prev["positions"].get(key)
        if old and old.get("cotes_fr"):
            continue  # cote FR déjà relevée : on la garde (relevé figé, comme nos positions)
        ko = dt.datetime.fromisoformat(p["coup_envoi"].replace("Z", "+00:00"))
        rec = {k: p.get(k) for k in ("liste", "sport", "acteur", "match", "competition", "coup_envoi", "pari", "cote", "resultat")}
        parsed = parse(p["sport"], p["pari"])
        sk = sport_key(p["sport"], p.get("competition"))
        if not parsed:
            rec["etat"] = "marché non proposé en France par notre source"
        elif not sk:
            rec["etat"] = "compétition non couverte par notre source FR"
        else:
            if a.mode == "direct":
                if ko <= now:
                    rec["etat"] = "match commencé avant le relevé"
                    prev["positions"].setdefault(key, rec)
                    continue
                ck = (sk, "now")
            else:
                ck = (sk, snaps[(sk, p["coup_envoi"])])
            if ck not in cache:
                if credits >= a.max_credits:
                    rec["etat"] = "plafond de crédits atteint"
                    prev["positions"][key] = rec
                    continue
                try:
                    if a.mode == "direct":
                        data, used, left = get(f"/sports/{sk}/odds", regions="fr", markets=MARKETS[p["sport"]], oddsFormat="decimal")
                        cache[ck] = (data, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                    else:
                        data, used, left = get(f"/historical/sports/{sk}/odds", regions="fr", markets=MARKETS_PASSE[p["sport"]],
                                               oddsFormat="decimal", date=ck[1])
                        cache[ck] = (data.get("data") or [], data.get("timestamp"))
                    credits += used
                    prev["credits_restants"] = left
                except Exception as e:  # noqa: BLE001
                    cache[ck] = ([], None)
                    rec["erreur"] = str(e)[:120]
            events, stamp = cache[ck]
            ev, swapped = find_event(events, p["match"], p["coup_envoi"])
            if not ev:
                rec["etat"] = "match introuvable chez les sites FR"
            else:
                market, name, point = parsed
                prices = fr_prices(ev, market, name, point)
                rec["releve_a"] = stamp
                if prices:
                    rec["etat"] = "même marché en France"
                    rec["cotes_fr"] = prices
                    rec["cote_fr_moyenne"] = round(statistics.mean(prices.values()), 3)
                    rec["cote_fr_meilleure"] = max(prices.values())
                    rec["ecart_pct"] = round(100 * (rec["cote_fr_moyenne"] / p["cote"] - 1), 1)
                else:
                    other = lines_available(ev, market, name) if market != "h2h" else []
                    rec["etat"] = "ligne différente en France" if other else "marché non proposé en France par notre source"
                    if other:
                        rec["lignes_fr"] = other
        prev["positions"][key] = rec
    prev.update({"jour": a.jour, "maj": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": a.mode,
                 "regle": "Lecture seule. Cote FR = moyenne des sites agréés en France trouvés, même marché, même ligne."})
    json.dump(prev, open(out_fn, "w"), ensure_ascii=False, indent=1)
    n = sum(1 for r in prev["positions"].values() if r.get("cotes_fr"))
    print(f"{a.jour}: {len(prev['positions'])} positions, {n} avec cote FR, crédits utilisés {credits}, restants {prev.get('credits_restants')}")


if __name__ == "__main__":
    sys.exit(main())
