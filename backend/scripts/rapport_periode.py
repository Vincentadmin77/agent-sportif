"""Weekly and monthly results report (owner 2026-10-29: "un rapport avec un graphique tous les lundis et une fois par
mois"). Builds the page document rapports/<id> from copies of the page database, saved with ArtifactData get/list and
out_dir: stats/bilan.json (football positions), basket_pronostic/*.json and rapports/changements.json (dated rule
changes). The admin tab "Rapports" draws the chart from this document.

    python3 rapport_periode.py DUMP_DIR semaine 2026-11-02   # the week Monday 26/10 to Sunday 01/11
    python3 rapport_periode.py DUMP_DIR mois 2026-11-01      # October
    python3 rapport_periode.py DUMP_DIR debut 2026-10-29     # from the first day to the day before

Writes DUMP_DIR/out/rapports/<id>.json and prints its id. Ratios only, never money (owner 2026-10-22).
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

POSITION_ODDS = (1.50, 2.30)
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre"]


def period(kind: str, ref: date) -> tuple[date, date, str, str]:
    if kind == "semaine":  # the 7 days before the Monday of ref
        monday = ref - timedelta(days=ref.weekday())
        start, end = monday - timedelta(days=7), monday - timedelta(days=1)
        return start, end, f"semaine-{start.isoformat()}", f"Semaine du {start.day} au {end.day} {MOIS[end.month - 1]}"
    if kind == "mois":  # the month before ref
        end = ref.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        return start, end, f"mois-{start.strftime('%Y-%m')}", f"Mois de {MOIS[start.month - 1]} {start.year}"
    start, end = date(2026, 10, 10), ref - timedelta(days=1)
    return start, end, f"debut-{end.isoformat()}", f"Depuis le début (jusqu'au {end.day} {MOIS[end.month - 1]})"


def market(label: str, sport: str = "foot", marche: str | None = None) -> str:
    """Market family, with each sport's own axes (owner 2026-10-29 20:36: basket in points, tennis in sets and games)."""
    t = label.lower()
    if sport == "basket":
        if "quart" in t or "mi-temps" in t:
            return "Quart-temps / mi-temps"
        if "handicap" in t or re.search(r"[+\-−]\s*\d+(?:[,.]\d+)?\s*(?:points?)?(?:\s*\(|$)", t):
            return "Handicap"
        if "victoire" in t or "vainqueur" in t:
            return "Vainqueur"
        if "plus de" in t:
            return "Plus de … points"
        if "moins de" in t:
            return "Moins de … points"
        return "Autres"
    if sport == "tennis":
        if marche in ("Vainqueur", "Sets", "Handicap jeux", "Total de jeux", "Aces"):
            return marche
        if "ace" in t:
            return "Aces"
        if "handicap" in t and "jeu" in t:
            return "Handicap jeux"
        if "jeu" in t and ("plus de" in t or "moins de" in t or "total" in t):
            return "Total de jeux"
        if "set" in t:
            return "Sets"
        if "victoire" in t or "vainqueur" in t:
            return "Vainqueur"
        return "Autres"
    if "mi-temps" in t:
        return "Mi-temps"
    if "deux équipes" in t:
        return "Les deux équipes marquent"
    if "remboursé" in t or "double chance" in t or " ou nul" in t:
        return "Double chance / remboursé si nul"
    if "victoire" in t or "match nul" in t:
        return "Résultat"
    if "plus de" in t:
        return "Plus de … buts"
    if "moins de" in t:
        return "Moins de … buts"
    return "Autres"


def rate(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def block(items: list[dict], start: date, end: date, before: list[dict], sport: str = "foot") -> dict:
    """items: {jour, valide, cote, niveau?, libelle} settled positions of one sport."""
    inside = [i for i in items if start.isoformat() <= i["jour"] <= end.isoformat()]
    days = {}
    for i in inside:
        d = days.setdefault(i["jour"], [0, 0])
        d[0 if i["valide"] else 1] += 1
    ok = sum(i["valide"] for i in inside)
    odds = [i["cote"] for i in inside if isinstance(i.get("cote"), (int, float))]
    cote = round(sum(odds) / len(odds), 2) if odds else None
    groups = {}
    for i in inside:
        g = groups.setdefault(market(i.get("libelle", ""), sport, i.get("marche")), [0, 0])
        g[0] += i["valide"]
        g[1] += 1
    levels = {}
    for i in inside:
        if i.get("niveau"):
            g = levels.setdefault(i["niveau"], [0, 0])
            g[0] += i["valide"]
            g[1] += 1
    return {"jours": [[d, v[0], v[1]] for d, v in sorted(days.items())], "valides": ok, "total": len(inside),
            "taux": rate(ok, len(inside)), "cote_moyenne": cote, "seuil": round(1 / cote, 4) if cote else None,
            "par_marche": {k: {"valides": v[0], "total": v[1], "taux": rate(*v)} for k, v in sorted(groups.items())},
            "par_niveau": {k: {"valides": v[0], "total": v[1], "taux": rate(*v)} for k, v in sorted(levels.items())},
            # everything settled before the period, so the cumulative line starts where it stood
            "avant": {"valides": sum(i["valide"] for i in before), "total": len(before)}}


def foot_items(dump: str) -> list[dict]:
    b = json.load(open(os.path.join(dump, "stats", "bilan.json")))
    return [{"jour": i["jour"], "valide": bool(i["valide"]), "cote": i.get("cote"), "niveau": i.get("niveau"),
             "libelle": i.get("libelle", ""), "competition": i.get("competition")}
            for i in (b.get("valides") or []) + (b.get("non_valides") or []) if i.get("categorie") == "position"]


def tennis_items(dump: str) -> list[dict]:
    """Tennis positions from the page collection "tennis" (one doc per day, raw tennis file: matchs[].choix with
    position, marche, cote, niveau, statut "retenu" or "Le modèle ne le retient pas", resultat). Only retained ones count."""
    out = []
    for f in sorted(glob.glob(os.path.join(dump, "tennis", "*.json"))):
        d = json.load(open(f))
        day = d.get("date") or os.path.basename(f)[:10]
        for m in d.get("matchs") or []:
            ch = m.get("choix")
            for c in (ch if isinstance(ch, list) else [ch] if isinstance(ch, dict) else []):
                if c.get("statut") != "retenu" or c.get("resultat") not in ("gagne", "perdu"):
                    continue
                cote = c.get("cote")
                if not isinstance(cote, (int, float)) or not POSITION_ODDS[0] <= cote <= POSITION_ODDS[1]:
                    continue
                out.append({"jour": day, "valide": c["resultat"] == "gagne", "cote": cote, "niveau": c.get("niveau"),
                            "libelle": f"{c.get('position')} ({m.get('joueur_1')} - {m.get('joueur_2')})",
                            "marche": c.get("marche"), "competition": m.get("circuit")})
    return out


def sport_items(dump: str, coll: str) -> list[dict]:
    """Settled positions of a sport kept per day in <coll>/<day> (basket_pronostic, tennis_pronostic)."""
    out = []
    for f in sorted(glob.glob(os.path.join(dump, coll, "*.json"))):
        d = json.load(open(f))
        day = os.path.basename(f)[:10]
        src = d.get("positions") if isinstance(d.get("positions"), list) else list((d.get("paris_retenus") or {}).values())
        seen = set()
        for p in src:
            if not isinstance(p, dict) or not p.get("pari") or p.get("resultat") not in ("gagne", "perdu"):
                continue
            if p.get("hors_ratio") or p.get("retenu") is False:
                continue
            cote = p.get("cote")
            if not isinstance(cote, (int, float)) or not POSITION_ODDS[0] <= cote <= POSITION_ODDS[1]:
                continue
            key = (p.get("match_id") or p.get("match"), p["pari"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"jour": day, "valide": p["resultat"] == "gagne", "cote": cote, "niveau": p.get("niveau"),
                        "libelle": f"{p['pari']} ({p.get('match')})", "competition": p.get("competition")})
    return out


def odds_band(c) -> str | None:
    if not isinstance(c, (int, float)):
        return None
    for lo, hi in ((1.50, 1.69), (1.70, 1.89), (1.90, 2.09), (2.10, 2.30)):
        if c <= hi + 1e-9:
            return f"Cotes {lo:.2f} à {hi:.2f}".replace(".", ",")
    return None


MIN_GROUP = 10   # under 10 positions a group says nothing
MARGIN = 0.05    # 5 points above or below the break-even


def strengths(items: list[dict], start: date, end: date, seuil: float | None, sport: str = "foot") -> dict:
    """Owner 2026-10-29: the strong and weak points of the period, what to keep and what to work on. Groups by market,
    confidence level, league and odds band; a group counts only from MIN_GROUP positions, and is strong or weak when
    its rate is MARGIN above or below the break-even of its own odds."""
    inside = [i for i in items if start.isoformat() <= i["jour"] <= end.isoformat()]
    groups: dict[tuple[str, str], list[dict]] = {}
    for i in inside:
        for axis, key in (("Marché", market(i.get("libelle", ""), sport, i.get("marche"))), ("Confiance", i.get("niveau")),
                          ("Championnat", i.get("competition")), ("Cotes", odds_band(i.get("cote")))):
            if key and key != "Autres":
                groups.setdefault((axis, key), []).append(i)
    rows = []
    for (axis, key), L in groups.items():
        if len(L) < MIN_GROUP:
            continue
        ok = sum(i["valide"] for i in L)
        odds = [i["cote"] for i in L if isinstance(i.get("cote"), (int, float))]
        be = len(odds) / sum(odds) if odds else seuil
        r = ok / len(L)
        rows.append({"axe": axis, "groupe": key, "valides": ok, "total": len(L), "taux": round(r, 4),
                     "seuil": round(be, 4), "ecart": round(r - be, 4)})
    rows.sort(key=lambda x: x["ecart"], reverse=True)
    fort = [x for x in rows if x["ecart"] >= MARGIN]
    faible = [x for x in rows if x["ecart"] <= -MARGIN][::-1]
    def say(x: dict, good: bool) -> str:
        sure = "à confirmer" if x["total"] < 40 else "assez de positions pour en tenir compte"
        return (f"{x['groupe']} ({x['axe'].lower()}) : {x['valides']} sur {x['total']} ({round(x['taux'] * 100)} %), "
                f"{'au-dessus' if good else 'en dessous'} de son seuil de {round(x['seuil'] * 100)} % ; {sure}.")
    return {"groupes": rows, "points_forts": [say(x, True) for x in fort[:6]],
            "points_faibles": [say(x, False) for x in faible[:6]],
            "a_garder": [x["groupe"] for x in fort if x["total"] >= 20][:3],
            "a_travailler": [x["groupe"] for x in faible if x["total"] >= 20][:3]}


def sentences(f: dict, b: dict, chg: list, t: dict | None = None) -> list[str]:
    out = []
    if f["total"]:
        verdict = "au-dessus" if f["taux"] >= (f["seuil"] or 1) else "en dessous"
        out.append(f"Football : {f['valides']} validées sur {f['total']} ({round(f['taux'] * 100)} %), cote moyenne "
                   f"{f['cote_moyenne']:.2f}, {verdict} du seuil de {f['seuil'] * 100:.1f} %.".replace(".", ",", 2)
                   .replace(",0 %", " %"))
        best = [(k, v) for k, v in f["par_marche"].items() if v["total"] >= 8 and k != "Autres"]
        if best:
            k, v = max(best, key=lambda kv: kv[1]["taux"])
            out.append(f"Meilleur marché : {k}, {v['valides']} sur {v['total']}.")
        if f["total"] < 100:
            out.append(f"Sur {f['total']} positions, un écart de 5 à 10 points avec la période d'avant peut venir du hasard.")
    else:
        out.append("Football : aucune position terminée sur la période.")
    if b["total"]:
        out.append(f"Basket : {b['valides']} validées sur {b['total']} ({round(b['taux'] * 100)} %), encore trop peu pour conclure."
                   if b["total"] < 60 else f"Basket : {b['valides']} validées sur {b['total']} ({round(b['taux'] * 100)} %).")
    if t and t["total"]:
        out.append(f"Tennis : {t['valides']} validées sur {t['total']} ({round(t['taux'] * 100)} %)"
                   + (", encore trop peu pour conclure." if t["total"] < 60 else "."))
    if chg:
        out.append(f"{len(chg)} changement{'s' if len(chg) > 1 else ''} de règle sur la période, marqué{'s' if len(chg) > 1 else ''} sur le graphique.")
    return out


def main() -> None:
    dump, kind, ref = sys.argv[1], sys.argv[2], date.fromisoformat(sys.argv[3])
    start, end, rid, title = period(kind, ref)
    foot, basket, tennis = foot_items(dump), sport_items(dump, "basket_pronostic"), tennis_items(dump)
    path = os.path.join(dump, "rapports", "changements.json")
    changes = json.load(open(path)).get("liste", []) if os.path.exists(path) else []
    chg = [c for c in changes if start.isoformat() <= c[0] <= end.isoformat()]
    f = block(foot, start, end, [i for i in foot if i["jour"] < start.isoformat()])
    b = block(basket, start, end, [i for i in basket if i["jour"] < start.isoformat()], "basket")
    t = block(tennis, start, end, [i for i in tennis if i["jour"] < start.isoformat()], "tennis")
    doc = {"type": kind, "debut": start.isoformat(), "fin": end.isoformat(), "titre": title,
           "maj": datetime.now(timezone.utc).isoformat(timespec="seconds"), "foot": f, "basket": b,
           "changements": chg, "texte": sentences(f, b, chg, t),
           "analyse_foot": strengths(foot, start, end, f["seuil"]),
           "analyse_basket": strengths(basket, start, end, b["seuil"], "basket")}
    if tennis:  # each sport keeps its own axes; tennis appears once it has positions
        doc["tennis"] = t
        doc["analyse_tennis"] = strengths(tennis, start, end, t["seuil"], "tennis")
    os.makedirs(os.path.join(dump, "out", "rapports"), exist_ok=True)
    json.dump(doc, open(os.path.join(dump, "out", "rapports", rid + ".json"), "w"), ensure_ascii=False, indent=1)
    print(rid)


if __name__ == "__main__":
    main()
