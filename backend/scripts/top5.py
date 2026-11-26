"""Top 5 of each actor, per sport and per day (owner 2026-11-08 11:53Z): to compare the actors on equal terms,
each one keeps only its 5 strongest positions of the day, like the test selection and a ticket offered to a client.

The daily positions are not touched: this is a separate selection layer, filtered from what each actor already
produced. Same rule for everyone: its 5 positions of highest own confidence, one per match.
- modele: officielle of registre/<jour>, confidence = 50/50 model + market (the site's confidence)
- m: choix_m of registre/<jour> (pre-match only), confidence = market chance (the M's own rule)
- specialiste: specialistes/<jour> V1/V2/V3, confidence = its « chance »
- test: selection_telegram/<jour>, confidence = market chance (already at most 5)

A position is frozen at kick-off -45 min: once frozen it stays in the Top 5 and keeps its slot; free slots are
refilled at each pass only from matches not yet frozen. Results are read back from the sources at each pass.

Usage: top5.py --jour D --registre R.json --specialistes S.json --test T.json [--prev P.json] --out O.json [--retro]
--retro freezes everything (past days, rebuilt from final data).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

N = 5
FREEZE = timedelta(minutes=45)
SPORTS = ("foot", "basket", "tennis")
# actors removed by the owner, per sport (Vincent 2026-11-08 12:01Z: basket specialist V2 removed)
RETIRES = {"basket": {"specialiste"}}
# Vincent 2026-11-26 : un seul acteur par sport. Foot = le spécialiste seul ; basket = le Sur-mesure Analytrack seul
# (la liste « test » de la Version finale) ; tennis = le Mathématicien seul (sa sélection est dans registre, pas
# ici). Plus de nouvelles listes pour les autres à partir de ce jour ; le passé reste dans la base, jamais réécrit.
ARRET_DU = "2026-11-26"
GARDES_TOP5 = {"foot": {"specialiste"}, "basket": set(), "tennis": set()}  # Top 5 par acteur encore construits, par sport arrêté
GARDES_FINALE = {"foot": set(), "basket": {"test"}, "tennis": set()}      # listes de la Version finale encore construites
REGLES = {
    "modele": "Ses 5 positions de plus forte confiance (modèle et marché à parts égales), une par match.",
    "m": "Ses 5 choix avant match où le marché voit le plus de chances, une par match.",
    "specialiste": "Ses 5 positions où il se donne le plus de chances, une par match.",
    "test": "Ses 5 positions où le marché voit le plus de chances, une par match.",
}


def _load(path):
    if not path:
        return {}
    try:
        d = json.load(open(path))
    except (OSError, ValueError):
        return {}
    return d.get("data", d) if isinstance(d, dict) else {}


def _when(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(x):
    return x if isinstance(x, (int, float)) else None


def _res(r):
    return {"gagne": "gagne", "perdu": "perdu", "rembourse": "rembourse", "annule": "rembourse"}.get(r)


def candidates(reg: dict, spe: dict, test: dict) -> dict:
    """{sport: {actor: [P...]}} with every position each actor holds today."""
    out = {s: {a: [] for a in REGLES} for s in SPORTS}
    for key, a in (reg.get("avis") or {}).items():
        sport = a.get("sport")
        if sport not in out:
            continue
        base = {"match_id": str(key), "match": a.get("match"), "competition": a.get("competition"),
                "coup_envoi": a.get("coup_envoi")}
        o = a.get("officielle") or {}
        p, pm = _num(o.get("proba")), _num(o.get("proba_marche"))
        if o.get("pari") and _num(o.get("cote")) and (p or pm):
            conf = (p + pm) / 2 if p and pm else (p or pm)
            out[sport]["modele"].append({**base, "pari": o["pari"], "cote": o["cote"], "confiance": round(conf, 4),
                                         "resultat": _res(o.get("resultat"))})
        c = a.get("choix_m") or {}
        if c.get("pari") and _num(c.get("cote")) and _num(c.get("proba_marche")) and not c.get("retire"):
            out[sport]["m"].append({**base, "pari": c["pari"], "cote": c["cote"],
                                    "confiance": round(c["proba_marche"], 4), "resultat": _res(c.get("resultat"))})
    for sport in SPORTS:
        for mid, s in (spe.get(sport) or {}).items():
            conf = _num(s.get("chance")) or _num(s.get("p_marche"))
            if not s.get("pari") or not _num(s.get("cote_prise")) or not conf:
                continue
            key = mid if sport == "foot" else f"{sport}-{mid}"
            out[sport]["specialiste"].append({"match_id": key, "match": s.get("match"),
                                              "competition": s.get("competition"), "coup_envoi": s.get("coup_envoi"),
                                              "pari": s["pari"], "cote": s["cote_prise"], "confiance": round(conf, 4),
                                              "resultat": _res(s.get("resultat"))})
    for t in test.get("positions") or []:
        sport = t.get("sport", "foot")
        if sport in out and t.get("pari") and _num(t.get("cote")):
            out[sport]["test"].append({"match_id": f"{sport}-{t.get('match_id')}", "match": t.get("match"),
                                       "competition": t.get("competition"), "coup_envoi": t.get("coup_envoi"),
                                       "pari": t["pari"], "cote": t["cote"],
                                       "confiance": round(_num(t.get("proba_marche")) or 0, 4),
                                       "resultat": _res(t.get("resultat"))})
    return out


def pick(cands: list[dict], prev: list[dict], now: datetime, retro: bool = False) -> list[dict]:
    by_match = {c["match_id"]: c for c in cands}
    kept = []
    for p in prev or []:
        t = _when(p.get("coup_envoi"))
        if not p.get("fige") and t is not None and now >= t - FREEZE:
            p = {**p, "fige": True}  # it was in the Top 5 when its window closed: it stays
        if p.get("fige"):
            fresh = by_match.get(p["match_id"])
            # a frozen position never changes; only its result is read back
            if fresh and fresh.get("pari") == p.get("pari") and fresh.get("resultat"):
                p = {**p, "resultat": fresh["resultat"]}
            kept.append(p)
    taken = {p["match_id"] for p in kept}
    pool = []
    for c in cands:
        if c["match_id"] in taken:
            continue
        t = _when(c.get("coup_envoi"))
        frozen = retro or (t is not None and now >= t - FREEZE)
        if frozen and not retro and prev is not None:
            continue  # its kick-off window passed before it entered the Top 5: too late to add it
        pool.append({**c, "fige": frozen})
    pool.sort(key=lambda c: (-c["confiance"], c.get("coup_envoi") or ""))
    seen = set(taken)
    for c in pool:
        if len(kept) >= N:
            break
        if c["match_id"] in seen:
            continue
        seen.add(c["match_id"])
        kept.append(c)
    return sorted(kept, key=lambda c: c.get("coup_envoi") or "")


# owner 2026-11-19 (« même principe que le foot », basket cotes trop souvent proches de 1,50): every basket list keeps
# only odds 1.70 to 2.00 from that day on; frozen positions of earlier days are never rewritten.
ODDS_SPORT = {"basket": (1.70, 2.00)}
ODDS_SPORT_FROM = "2026-11-19"
_JOUR = {"j": ""}


def _in_range(sport: str, c: dict, actor: str = "") -> bool:
    if _JOUR["j"] < ODDS_SPORT_FROM or actor == "test":  # the basket Sur-mesure keeps its own rule (owner 19/11 08:19, plan B)
        return True
    lo, hi = ODDS_SPORT.get(sport, (0, 99))
    return lo <= c["cote"] <= hi


def ratio(ps: list[dict]) -> dict:
    g = sum(p.get("resultat") == "gagne" for p in ps)
    l = sum(p.get("resultat") == "perdu" for p in ps)
    r = sum(p.get("resultat") == "rembourse" for p in ps)
    return {"gagnees": g, "perdues": l, "remboursees": r, "jouees": g + l}


def build(jour, reg, spe, test, prev, now, retro=False) -> dict:
    _JOUR["j"] = jour
    cands = candidates(reg, spe, test)
    doc = {"jour": jour, "regle": "Chaque acteur garde ses 5 positions de plus forte confiance du jour, par sport, "
                                  "une par match, figées 45 min avant le coup d'envoi.",
           "maj": now.isoformat(timespec="seconds")}
    for s in SPORTS:
        doc[s] = {}
        for a, regle in REGLES.items():
            if a in RETIRES.get(s, ()):
                continue
            if jour >= ARRET_DU and s in GARDES_TOP5 and a not in GARDES_TOP5[s]:
                continue
            pool = [c for c in cands[s][a] if _in_range(s, c, a)]
            ps = pick(pool, ((prev.get(s) or {}).get(a) or {}).get("positions"), now, retro)
            if s in ODDS_SPORT and jour >= ODDS_SPORT_FROM and a != "test":
                regle = regle.rstrip(".") + ", cote de 1,70 à 2,00."
            doc[s][a] = {"regle": regle, "positions": ps, "ratio": ratio(ps), "candidats": len(pool)}
        if not doc[s]:
            del doc[s]
    return doc


# « Version finale » (owner 2026-11-08 14:09Z), à blanc: the one list kept per sport, plus the test beside it.
# actor -> (minimum odds, rule shown). Same pick (highest confidence, one per match, frozen at kick-off -45 min).
FINALE = {
    "foot": {"modele": (1.70, "Le modèle : ses 5 positions de plus forte confiance du jour, seulement à une cote de 1,70 ou "
                              "plus (moins de 5 s'il n'y en a pas assez)."),
             "test": (0, REGLES["test"])},
    "tennis": {"modele": (0, "Le modèle : ses 5 positions de plus forte confiance du jour, sans filtre de cote."),
               "test": (0, REGLES["test"])},
    "basket": {"m": (0, "Le M : ses 5 choix avant match où le marché voit le plus de chances, seulement à une cote de "
                        "1,70 à 2,00 (moins de 5 s'il n'y en a pas assez)."),
               "test": (0, REGLES["test"])},
}


def build_finale(jour, reg, spe, test, prev, now, retro=False) -> dict:
    _JOUR["j"] = jour
    cands = candidates(reg, spe, test)
    doc = {"jour": jour, "version_finale": True,
           "regle": "Version finale à blanc : une seule liste par sport (foot : modèle, cote 1,70 ou plus ; tennis : "
                    "modèle ; basket : M" + (", cote 1,70 à 2,00" if jour >= ODDS_SPORT_FROM else "") + "), 5 positions au plus par jour, une par match, figées 45 min avant le début. "
                    "La sélection test est montrée à côté.",
           "maj": now.isoformat(timespec="seconds")}
    for s, actors in FINALE.items():
        doc[s] = {}
        for a, (mn, regle) in actors.items():
            if jour >= ARRET_DU and s in GARDES_FINALE and a not in GARDES_FINALE[s]:
                continue
            if s == "basket" and a == "m" and jour < ODDS_SPORT_FROM:
                regle = "Le M : ses 5 choix avant match où le marché voit le plus de chances, sans filtre de cote."
            pool = [c for c in cands[s][a] if c["cote"] >= mn and _in_range(s, c, a)]
            key0 = "test" if a == "test" else "finale"
            ps = pick(pool, ((prev.get(s) or {}).get(key0) or {}).get("positions"), now, retro)
            key = "test" if a == "test" else "finale"  # the page reads the kept list under « finale »
            doc[s][key] = {"regle": regle, "acteur": a, "positions": ps, "ratio": ratio(ps), "candidats": len(pool)}
        if not doc[s]:
            del doc[s]
    if jour >= ARRET_DU:
        doc["regle"] = ("Un seul acteur par sport depuis le 26/11 : ici seulement le basket, le Sur-mesure Analytrack "
                        "(5 positions au plus, une par match, figées 45 min avant le début). En foot c'est le "
                        "spécialiste, en tennis le Mathématicien (hors de cette liste).")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jour", required=True)
    ap.add_argument("--registre")
    ap.add_argument("--specialistes")
    ap.add_argument("--test")
    ap.add_argument("--prev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--retro", action="store_true")
    ap.add_argument("--finale", action="store_true", help="the « Version finale » doc instead of the Top 5")
    a = ap.parse_args()
    doc = (build_finale if a.finale else build)(a.jour, _load(a.registre), _load(a.specialistes), _load(a.test), _load(a.prev),
                datetime.now(timezone.utc), a.retro)
    json.dump(doc, open(a.out, "w"), ensure_ascii=False, indent=1)
    for s in SPORTS:
        if s not in doc:
            continue
        print(s, {k: f"{len(v['positions'])}/{v['candidats']} {v['ratio']['gagnees']}-{v['ratio']['perdues']}"
                  for k, v in doc[s].items()})


if __name__ == "__main__":
    main()
