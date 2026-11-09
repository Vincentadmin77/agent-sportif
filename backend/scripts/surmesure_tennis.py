"""« Sur-mesure en test » tennis (owner 2026-11-09 07:46Z, via the coordinator), à blanc, beside the current tennis
test selection (not touched; the frozen tennis model and the live M are not touched either).

Rule: when a clear favourite has a small odds (best odds at most FAV_MAX, margin-free market chance at least
FAV_CHANCE), pair it on the same match with a total of games: « Alcaraz gagne et moins de 22,5 jeux ». The combo is either
the feed's own quoted « vainqueur + total de jeux » market (api-tennis « Result/Total Goals »): its odds are a real
price (best of the sites, within 8 % of their median), its chance is each site's margin-free share of the four
outcomes of that line (median). Combined odds 1.60-2.00, chance at least 50 %, one per match, 5 a day at most, the
surest first, frozen 45 min before the start. Own ratio.

French odds: the feed has no French site. Each pass asks The Odds API (region fr) whether a French site quotes the
match; the answer is stored on each position as « cote_fr ». With --exiger-fr, positions without a French quote are
not made.

    python scripts/surmesure_tennis.py passage --jour D --tennis TENNIS.json --doc DOC.json --out DOC.json [--exiger-fr]
    python scripts/surmesure_tennis.py regler --doc DOC.json --out DOC.json
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

TENNIS = "https://api.api-tennis.com/tennis/"
MAX_PLACES = 5
ODDS_MIN, ODDS_MAX = 1.60, 2.00
FAV_MAX, FAV_CHANCE = 1.45, 0.68
MIN_CHANCE = 0.50
OUTLIER = 1.08
FREEZE = timedelta(minutes=45)
REGLE = ("Sur-mesure en test (tennis) : quand un favori net a une petite cote (1,45 au plus, au moins 68 % de chances "
         "pour le marché), on l'associe sur le même match à un total de jeux. Cote totale de 1,60 à 2,00, au moins "
         "50 % de chances pour le marché, une position par match, 5 au plus par jour, les plus sûres d'abord, figées "
         "45 min avant le début.")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 1.0 else None


def _fmt(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _api(**q) -> dict:
    q["APIkey"] = os.environ["API_TENNIS_KEY"]
    with urllib.request.urlopen(TENNIS + "?" + urllib.parse.urlencode(q), timeout=60) as r:
        return json.load(r)


def _best(odds: list[float]) -> float:
    med = statistics.median(odds)
    return max(o for o in odds if o <= med * OUTLIER)


def favourite(o: dict) -> tuple[str, float, float, int] | None:
    """('Home'|'Away', best odds, median margin-free chance, sites) of a clear favourite with a small odds."""
    ha = o.get("Home/Away") or {}
    h, a = ha.get("Home") or {}, ha.get("Away") or {}
    for side, mine, other in (("Home", h, a), ("Away", a, h)):
        rows = [(_num(x), _num(other.get(b))) for b, x in mine.items()]
        rows = [(x, y) for x, y in rows if x and y]
        if not rows:
            continue
        ch = statistics.median((1 / x) / (1 / x + 1 / y) for x, y in rows)
        best = _best([x for x, _ in rows])
        if best <= FAV_MAX and ch >= FAV_CHANCE:
            return side, best, ch, len(rows)
    return None


def candidates(o: dict, j1: str, j2: str) -> list[dict]:
    fav = favourite(o)
    if not fav:
        return []
    side, fo, fc, fn = fav
    rt = o.get("Result/Total Goals") or {}
    lines = set()
    for k, v in rt.items():
        lines |= set(v)
    out = []
    for line in lines:
        q = {(s, t): (rt.get(f"Result/Total Goals {s}/{t}") or {}).get(line) or {}
             for s in ("Home", "Away") for t in ("Over", "Under")}
        for t in ("Over", "Under"):
            mine = q[(side, t)]
            odds = [x for x in (_num(v) for v in mine.values()) if x]
            if not odds:
                continue
            fair = []
            for b in mine:
                four = [_num(q[k].get(b)) for k in q]
                if all(four):
                    fair.append((1 / _num(mine[b])) / sum(1 / x for x in four))
            if not fair:
                continue
            cote, ch = _best(odds), statistics.median(fair)
            if not (ODDS_MIN <= cote <= ODDS_MAX) or ch < MIN_CHANCE:
                continue
            team = j1 if side == "Home" else j2
            word = "plus" if t == "Over" else "moins"
            out.append({"cle": f"st_{side[0].lower()}_{t[0].lower()}_{line}",
                        "pari": f"{team} gagne et {word} de {_fmt(float(line))} jeux",
                        "cote": round(cote, 2), "proba_marche": round(ch, 4), "nb_sites": len(fair),
                        "favori": {"pari": f"{team} gagne le match", "cote": round(fo, 2),
                                   "proba_marche": round(fc, 4), "nb_sites": fn}})
    # the same pair from the two single markets (wider range of lines), priced as their product, like the basket one
    ou = o.get("Over/Under by Games in Match") or {}
    ov, un = ou.get("Over/Under by Games in Match Over") or {}, ou.get("Over/Under by Games in Match Under") or {}
    team = j1 if side == "Home" else j2
    for line in set(ov) | set(un):
        try:
            if float(line) % 1 != 0.5:
                continue
        except ValueError:
            continue
        for t, mine, other in (("Over", ov.get(line) or {}, un.get(line) or {}),
                               ("Under", un.get(line) or {}, ov.get(line) or {})):
            rows = [(_num(x), _num(other.get(b))) for b, x in mine.items()]
            rows = [(x, y) for x, y in rows if x and y]
            if not rows:
                continue
            lc = statistics.median((1 / x) / (1 / x + 1 / y) for x, y in rows)
            lo = _best([x for x, _ in rows])
            cote, ch = fo * lo, fc * lc
            if lc < 0.55 or not (ODDS_MIN <= cote <= ODDS_MAX) or ch < MIN_CHANCE:
                continue
            word = "plus" if t == "Over" else "moins"
            out.append({"cle": f"st_{side[0].lower()}_{t[0].lower()}_{line}",
                        "pari": f"{team} gagne et {word} de {_fmt(float(line))} jeux",
                        "cote": round(cote, 2), "proba_marche": round(ch, 4), "nb_sites": len(rows),
                        "cote_type": "produit des deux cotes",
                        "favori": {"pari": f"{team} gagne le match", "cote": round(fo, 2),
                                   "proba_marche": round(fc, 4), "nb_sites": fn},
                        "jeux": {"pari": f"{word.capitalize()} de {_fmt(float(line))} jeux dans le match",
                                 "cote": round(lo, 2), "proba_marche": round(lc, 4), "nb_sites": len(rows)}})
    return out


def settle(cle: str, sets: list[tuple[int, int]]) -> str:
    _, side, t, line = cle.split("_")
    s1 = sum(a > b for a, b in sets)
    won = (s1 > len(sets) - s1) if side == "h" else (s1 < len(sets) - s1)
    g = sum(a + b for a, b in sets)
    tot = g > float(line) if t == "o" else g < float(line)
    return "gagne" if won and tot else "perdu"


def french_quotes(now: datetime) -> dict[frozenset, list[str]]:
    """{frozenset of the two surnames: French sites quoting the match} from The Odds API (region fr), all active
    tennis competitions. Empty when nothing is listed."""
    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        return {}
    base = "https://api.the-odds-api.com/v4/sports"
    try:
        with urllib.request.urlopen(f"{base}?apiKey={key}", timeout=30) as r:
            sports = [s["key"] for s in json.load(r) if s["key"].startswith("tennis")]
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for sk in sports:
        q = urllib.parse.urlencode({"apiKey": key, "regions": "fr", "markets": "h2h", "oddsFormat": "decimal"})
        try:
            with urllib.request.urlopen(f"{base}/{sk}/odds?{q}", timeout=30) as r:
                evs = json.load(r)
        except Exception:  # noqa: BLE001
            continue
        for e in evs:
            names = frozenset(n.split()[-1].lower() for n in (e["home_team"], e["away_team"]))
            out[names] = [b["key"] for b in e.get("bookmakers") or []]
    return out


def _surnames(match: str) -> frozenset:
    return frozenset(p.strip().split()[-1].lower() for p in match.split(" - ", 1))


def _paris_iso(day: str, hhmm: str) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ZoneInfo("Europe/Paris")).isoformat()


def passage(day: str, tennis_doc: dict, doc: dict, now: datetime, exiger_fr: bool = False) -> dict:
    doc = {"jour": day, "positions": [], **doc}
    doc["regle"] = REGLE
    kept = [p for p in doc["positions"] if p.get("fige") or now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE]
    for p in kept:
        p["fige"] = True
    taken = {p["match_id"] for p in kept}
    odds = _api(method="get_odds", date_start=day, date_stop=day).get("result") or {}
    fr = french_quotes(now)
    pool, scanned = [], []
    for m in tennis_doc.get("matchs") or []:
        mid = str(m["id"])
        if not m.get("heure_paris"):
            continue
        ko = _paris_iso(day, m["heure_paris"])
        if mid in taken or now >= datetime.fromisoformat(ko) - FREEZE or mid not in odds:
            continue
        match = f"{m['joueur_1']} - {m['joueur_2']}"
        scanned.append(match)
        cands = candidates(odds[mid], m["joueur_1"], m["joueur_2"])
        if not cands:
            continue
        sites_fr = fr.get(_surnames(match)) or []
        if exiger_fr and not sites_fr:
            continue
        best = max(cands, key=lambda c: c["proba_marche"])
        pool.append({"sport": "tennis", "match": match, "match_id": mid, "competition": m.get("tournoi"),
                     "coup_envoi": ko, **best, "cote_fr": bool(sites_fr)})
    pool.sort(key=lambda b: -b["proba_marche"])
    fresh = []
    for b in pool[: max(0, MAX_PLACES - len(kept))]:
        f = b["favori"]
        how = ("est proposé tel quel par les sites" if "jeux" not in b else
               f"associe « {b['jeux']['pari']} » ({round(b['jeux']['proba_marche'] * 100)} %, cote "
               f"{_fmt(b['jeux']['cote'])}), cote = produit des deux cotes")
        fresh.append({"cote_type": "cotée", **b, "resultat": None, "fige": False,
                      "choisi_a": now.isoformat(timespec="minutes"),
                      "explication": (f"Favori net : le marché donne {round(f['proba_marche'] * 100)} % à "
                                      f"« {f['pari']} » (cote {_fmt(f['cote'])}). Le combiné « {b['pari']} » "
                                      f"{how} : {round(b['proba_marche'] * 100)} % de chances, cote "
                                      f"{_fmt(b['cote'])}.")})
    doc["positions"] = sorted(kept + fresh, key=lambda p: p["coup_envoi"])
    doc["matchs_etudies"] = sorted(set(doc.get("matchs_etudies") or []) | set(scanned))
    doc["cotes_fr"] = {"verifie_a": now.isoformat(timespec="minutes"), "matchs_trouves": len(fr),
                       "exigees": exiger_fr}
    doc["maj"] = now.isoformat(timespec="minutes")
    return _ratio(doc)


def regler(doc: dict, now: datetime) -> dict:
    for p in doc.get("positions") or []:
        if now >= datetime.fromisoformat(p["coup_envoi"]) - FREEZE:
            p["fige"] = True
        if p.get("resultat"):
            continue
        r = (_api(method="get_fixtures", date_start=p["coup_envoi"][:10], date_stop=p["coup_envoi"][:10],
                  match_key=p["match_id"]).get("result") or [None])[0]
        if r and r.get("event_status") == "Finished" and r.get("scores"):
            sets = [(int(float(x["score_first"])), int(float(x["score_second"]))) for x in r["scores"]]
            p["score"], p["resultat"] = r.get("event_final_result"), settle(p["cle"], sets)
        elif r and r.get("event_status") in ("Retired", "Walk Over", "Cancelled"):
            p["resultat"], p["score"] = "rembourse", r.get("event_status")
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
    ap.add_argument("--tennis", help="the page's tennis/<jour> document")
    ap.add_argument("--doc")
    ap.add_argument("--out", required=True)
    ap.add_argument("--exiger-fr", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    doc = json.loads(Path(a.doc).read_text()) if a.doc and Path(a.doc).exists() else {}
    doc = doc.get("data", doc)
    for k in ("version", "id", "updated_at", "created_at"):
        doc.pop(k, None)
    if a.cmd == "passage":
        t = json.loads(Path(a.tennis).read_text())
        doc = passage(a.jour, t.get("data", t), doc, now, a.exiger_fr)
    doc = regler(doc, now)
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for p in doc["positions"]:
        print(p["match"], "|", p["pari"], p["cote"], p["proba_marche"], "FR" if p.get("cote_fr") else "-",
              p.get("resultat"))
    print("ratio", doc["ratio"], "| cotes FR:", doc.get("cotes_fr"))


if __name__ == "__main__":
    main()
