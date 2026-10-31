"""The daily review of what was missed ("revue des ratés", owner 2026-10-24).

The owner, on 2026-10-24: "je veux que tous les agents regardent les erreurs et les positions qui sont passées sans
qu'on ne les choisisse" (2026-10-23: Brest - Nice "les deux équipes marquent : oui" at 1.57, Alavés - Málaga "moins de
2,5 buts", Stuttgart - Gladbach "les deux équipes marquent : oui" all won and nobody took them).

Each day, from the page's settled match documents of the earlier days (every priced bet of a match: its
"tous_marches" list and its goals block), this module lists

- the bets at odds of 1.50 or more that won and that nobody chose (not in a ticket, an idea, the top 3, a match
  selection, the match position, nor among the player's bets), with the model's chance and the bookmakers';
- the chosen bets that lost;

and aggregates every settled bet by family of market (both teams score yes / no, over / under 1.5, 2.5, 3.5, team
totals, results, double chance, handicaps...): how often it won against what the model and the bookmakers
announced, and how often a winner went unpicked.

From that record each family gets a correction of the model's chance (and of the bookmakers' one), the wins seen
against the wins announced, applied to the odds p / (1 - p) as the player already does (joueur._corrige), and pulled
towards no correction by a prior of PRIOR_GAINS expected wins so that a handful of matches cannot swing it. The tickets
and ideas (pronostic.legs_from_db) and, through them, the player use the corrected chances; every corrected bet keeps
its raw chance next to it ("proba_brute") and the page shows the review's sentences ("ce qu'on a raté hier").

Pure functions over the page's documents: nothing is stored apart from the review itself; no money in any text.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sportai.engine.football.markets import MarketKey, is_quarter, settle_score
from sportai.selection.engine import COTE_MIN
from sportai.services.dashboard import _label_keys, bet_label

# Family of each priced bet: what is learnt about "both teams score: yes" says nothing about a handicap.
FAMILLES = {
    "victoire": "Victoire d'une équipe", "nul": "Match nul", "double_chance": "Double chance",
    "rembourse_si_nul": "Remboursé si nul",
    "btts_oui": "Les deux équipes marquent : oui", "btts_non": "Les deux équipes marquent : non",
    "plus_1_5": "Plus de 1,5 buts", "moins_1_5": "Moins de 1,5 buts",
    "plus_2_5": "Plus de 2,5 buts", "moins_2_5": "Moins de 2,5 buts",
    "plus_3_5": "Plus de 3,5 buts", "moins_3_5": "Moins de 3,5 buts",
    "plus_autres": "Plus de buts (autres lignes)", "moins_autres": "Moins de buts (autres lignes)",
    "equipe_plus_0_5": "Une équipe marque", "equipe_moins_0_5": "Une équipe ne marque pas",
    "equipe_plus_1_5": "Équipe : plus de 1,5 buts", "equipe_moins_1_5": "Équipe : moins de 1,5 buts",
    "equipe_autres": "Buts d'une équipe (autres lignes)",
    "handicap_asiatique": "Handicap asiatique", "handicap_europeen": "Handicap européen",
    "resultat_et_buts": "Résultat et nombre de buts", "resultat_et_btts": "Résultat et les deux équipes marquent",
    "buts_et_btts": "Nombre de buts et les deux équipes marquent", "sans_encaisser": "Sans but encaissé",
    "score_exact": "Score exact", "nombre_exact_de_buts": "Nombre exact de buts", "pair_impair": "Buts pair ou impair",
    "nul_avec_buts": "Match nul avec buts",
}
# The goal families the owner wants proposed whenever they have value (2026-10-24: "pas assez de « + de 1,5 but »
# ou « les deux équipes marquent »"): pronostic.py and joueur.py give them their own, less crowded, rules.
GOAL_FAMILIES = frozenset({"btts_oui", "btts_non", "plus_1_5", "moins_1_5", "plus_2_5", "moins_2_5", "plus_3_5",
                           "moins_3_5", "equipe_plus_0_5", "equipe_moins_0_5", "equipe_plus_1_5", "equipe_moins_1_5"})

PRIOR_GAINS = 15.0  # the model is trusted as if it had already been right on 15 expected wins of the family
MIN_MATCHS = 12  # fewer settled matches in a family: no correction at all
BORNES = (0.75, 1.33)  # the correction never goes beyond these
FENETRE = 30  # days of settled matches reviewed
MAX_LISTE = 10
RATES_COTE_MAX = 2.50  # the missed winners listed: odds of 1.50 to 2.50


def _line(x: float) -> str:
    return f"{x:g}".replace(".", "_")


def famille(market: str, selection: str, line: float | None) -> str | None:
    """The family of one bet, None for a market the review does not follow (scorers...)."""
    if market == "1x2":
        return "nul" if selection == "draw" else "victoire"
    if market == "double_chance":
        return "double_chance"
    if market == "dnb":
        return "rembourse_si_nul"
    if market == "btts":
        return "btts_oui" if selection == "yes" else "btts_non"
    side = "plus" if selection == "over" else "moins"
    if market == "totals":
        return f"{side}_{_line(line)}" if line in (1.5, 2.5, 3.5) else f"{side}_autres"
    if market in ("team_total_home", "team_total_away"):
        return f"equipe_{side}_{_line(line)}" if line in (0.5, 1.5) else "equipe_autres"
    return {"asian_handicap": "handicap_asiatique", "euro_handicap": "handicap_europeen",
            "result_total": "resultat_et_buts", "result_btts": "resultat_et_btts", "total_btts": "buts_et_btts",
            "clean_sheet_home": "sans_encaisser", "clean_sheet_away": "sans_encaisser",
            "win_to_nil_home": "sans_encaisser", "win_to_nil_away": "sans_encaisser", "exact_score": "score_exact",
            "exact_goals": "nombre_exact_de_buts", "exact_goals_home": "nombre_exact_de_buts",
            "exact_goals_away": "nombre_exact_de_buts", "goal_range": "nombre_exact_de_buts",
            "odd_even": "pair_impair", "odd_even_home": "pair_impair", "odd_even_away": "pair_impair",
            "scoring_draw": "nul_avec_buts"}.get(market)


def corrige(p: float, r: float) -> float:
    """The chance p corrected by the record r (wins seen / wins announced), on the odds p / (1 - p) (joueur._corrige)."""
    return round(min(p * r / (p * r + 1 - p), 0.99), 4) if 0 < p < 1 and r != 1.0 else p


def correct_leg(market: str, selection: str, line: float | None, p_win: float, p_push: float, odds: float,
                p_market: float | None, corrections: dict | None) -> dict:
    """A priced bet's chance, value and market chance once the review's correction of its family is applied."""
    fam = famille(market, selection, line)
    c = (corrections or {}).get(fam) or {}
    r, rm = c.get("modele", 1.0), c.get("marche", 1.0)
    push = p_push or 0.0
    p_cmp = p_win / (1 - push) if 0 < push < 1 else p_win
    p_cmp2 = corrige(p_cmp, r)
    p2 = round(p_cmp2 * (1 - push), 4) if push else p_cmp2
    return {"famille": fam, "proba": p2, "p_cmp": p_cmp2, "valeur": round(p2 * odds + push - 1, 4), "ratio": r,
            "proba_marche": None if p_market is None else corrige(p_market, rm), "ratio_marche": rm}


def _keys(home: str, away: str) -> dict[str, MarketKey]:
    keys = dict(_label_keys(home, away))
    for m, sels in (("1x2", ("home", "draw", "away")), ("double_chance", ("1X", "X2", "12")), ("dnb", ("home", "away"))):
        for s in sels:
            keys[bet_label(m, s, home, away)] = MarketKey(m, s)
    return keys


def _score(doc: dict) -> tuple[int, int] | None:
    try:
        hg, ag = (int(x) for x in doc["score"].split(" - "))
        return hg, ag
    except (KeyError, ValueError, AttributeError):
        return None


def priced_rows(doc: dict) -> list[dict]:
    """Every settled priced bet of a finished match: its "tous_marches" list (the best value first, capped by the
    page) and its goals block (complete for both teams score and over / under 1.5, 2.5, 3.5, odds or not)."""
    sc = _score(doc)
    home, away = doc.get("domicile"), doc.get("exterieur")
    if doc.get("statut") != "termine" or sc is None or not home or not away:
        return []
    keys = _keys(home, away)
    rows: dict[str, dict] = {}
    for r in ((doc.get("tous_marches") or {}).get("analyses") or []) + ((doc.get("buts") or {}).get("marches") or []):
        key = keys.get(r.get("pari"))
        if key is None or r.get("proba") is None or (r["pari"] in rows and "cote" in rows[r["pari"]]):
            continue
        fam = famille(key.market, key.selection, key.line)
        if fam is None or is_quarter(key.line):
            continue
        res = settle_score(key, *sc)
        if res not in ("gagne", "perdu"):
            continue
        push = r.get("rembourse") or 0.0
        p = r["proba"] / (1 - push) if 0 < push < 1 else r["proba"]
        row = {"match_id": doc.get("_id"), "match": f"{home} - {away}", "jour": doc.get("jour"), "pari": r["pari"],
               "famille": fam, "proba": round(p, 4), "resultat": res}
        if r.get("cote") is not None:
            row.update(cote=r["cote"], valeur=r.get("valeur"))
        if r.get("proba_marche") is not None:
            row["proba_marche"] = r["proba_marche"]
        rows[r["pari"]] = row
    return list(rows.values())


def _chosen(day: str, pronostics: dict, joueurs: dict, docs: list[dict]) -> tuple[set, list[dict]]:
    """The bets someone chose that day (tickets, ideas, top 3, selections, positions, the player's bets), and the
    chosen legs that lost."""
    legs = []
    p = pronostics.get(day) or {}
    legs += p.get("top3") or []
    for t in (p.get("tickets") or {}).values():
        legs += (t or {}).get("jambes") or []
    for group in (p.get("bonus") or {}).values():
        for t in group or []:
            legs += t.get("jambes") or []
    for d in (joueurs.get(day) or {}).get("decisions") or []:
        if d.get("joue"):
            legs += d.get("jambes") or []
    chosen = {(l.get("match_id"), l.get("pari")) for l in legs}
    for doc in docs:
        for s in doc.get("selections") or []:
            chosen.add((doc["_id"], s.get("pari")))
        pos = (doc.get("position") or {}).get("pari_conseille")
        if pos:
            chosen.add((doc["_id"], pos))
    lost, seen = [], set()
    for l in legs:
        k = (l.get("match_id"), l.get("pari"))
        if l.get("resultat") == "perdu" and k not in seen:
            seen.add(k)
            lost.append({"match": l.get("match"), "pari": l.get("pari"), "cote": l.get("cote"),
                         "proba": l.get("proba_brute", l.get("proba")), "proba_marche": l.get("proba_marche")})
    return chosen, lost


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _fr(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _jour_fr(day: str) -> str:
    return f"{day[8:10]}/{day[5:7]}"


def build_revue(page: dict[str, dict], pronostics: dict[str, dict], joueurs: dict[str, dict], today: date,
                fenetre: int = FENETRE) -> dict:
    """The review written into pronostic/<today> ("revue"): the families' record over the last `fenetre` days
    before today, their corrections, and what was missed on the last settled day."""
    start = f"{today - timedelta(days=fenetre):%Y-%m-%d}"
    by_day: dict[str, list[dict]] = defaultdict(list)
    for key, doc in page.items():
        # basketball has its own markets: its bets never correct the football families
        if (start <= (doc.get("jour") or "") < f"{today:%Y-%m-%d}" and doc.get("statut") == "termine"
                and doc.get("sport") != "basket"):
            by_day[doc["jour"]].append({**doc, "_id": key})
    acc: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    days_out = {}
    for day in sorted(by_day):
        docs = by_day[day]
        chosen, lost = _chosen(day, pronostics, joueurs, docs)
        missed, n_rows = [], 0
        for doc in docs:
            rows = priced_rows(doc)
            n_rows += len(rows)
            groups: dict[str, list[dict]] = defaultdict(list)
            for r in rows:
                groups[r["famille"]].append(r)
            for fam, grp in groups.items():
                w = 1 / len(grp)  # each match counts once per family, however many lines it offered
                a = acc[fam]
                a["matchs"] += 1
                for r in grp:
                    won = r["resultat"] == "gagne"
                    a["n"] += w
                    a["gagnes"] += w * won
                    a["attendus"] += w * r["proba"]
                    if r.get("proba_marche") is not None:
                        a["n_marche"] += w
                        a["gagnes_marche"] += w * won
                        a["attendus_marche"] += w * r["proba_marche"]
                    picked = (doc["_id"], r["pari"]) in chosen
                    if picked:
                        a["choisis"] += 1
                        a["choisis_gagnes"] += won
                        a["choisis_attendus"] += r["proba"]
                    elif won and (r.get("cote") or 0) >= COTE_MIN:
                        a["rates"] += 1
                        under = (r.get("valeur") is not None and r["valeur"] < 0) or (
                            r.get("proba_marche") is not None and r["proba"] < r["proba_marche"])
                        a["rates_sous_estimes"] += under
                        missed.append(r)
        days_out[day] = {"matchs": len(docs), "paris": n_rows, "rates": missed, "perdus": lost}
    familles, corrections = {}, {}
    for fam, a in sorted(acc.items()):
        r = r_m = 1.0
        if a["matchs"] >= MIN_MATCHS:
            r = round(min(max((PRIOR_GAINS + a["gagnes"]) / (PRIOR_GAINS + a["attendus"]), BORNES[0]), BORNES[1]), 3)
            if a["n_marche"] >= MIN_MATCHS:
                r_m = round(min(max((PRIOR_GAINS + a["gagnes_marche"]) / (PRIOR_GAINS + a["attendus_marche"]),
                                    BORNES[0]), BORNES[1]), 3)
        freq = a["gagnes"] / a["n"] if a["n"] else None
        familles[fam] = {
            "nom": FAMILLES[fam], "matchs": int(a["matchs"]), "frequence": None if freq is None else round(freq, 4),
            "modele_moyen": round(a["attendus"] / a["n"], 4) if a["n"] else None,
            "marche_moyen": round(a["attendus_marche"] / a["n_marche"], 4) if a["n_marche"] else None,
            "ecart_modele": None if freq is None else round(a["attendus"] / a["n"] - freq, 4),
            "choisis": int(a["choisis"]), "choisis_gagnes": int(a["choisis_gagnes"]),
            "choisis_attendus": round(a["choisis_attendus"], 2),
            "rates": int(a["rates"]), "rates_sous_estimes": int(a["rates_sous_estimes"]),
            "correction": r, "correction_marche": r_m}
        if r != 1.0 or r_m != 1.0:
            corrections[fam] = {"modele": r, "marche": r_m}
    last = max(days_out) if days_out else None
    hier = days_out.get(last) or {}
    # The winners left at reasonable odds, the most likely first (for the model or the bookmakers): the "les deux
    # équipes marquent à 1,57" kind, not the long shots that win now and then whatever is done.
    every = hier.get("rates") or []
    rates = sorted((r for r in every if r["cote"] <= RATES_COTE_MAX),
                   key=lambda r: -max(r["proba"], r.get("proba_marche") or 0))
    return {"jour": f"{today:%Y-%m-%d}", "depuis": min(days_out) if days_out else None, "dernier_jour": last,
            "familles": familles, "corrections": corrections,
            "rates": [{k: r[k] for k in ("match", "pari", "cote", "proba", "proba_marche", "valeur", "famille") if k in r}
                      for r in rates[:MAX_LISTE]],
            "n_rates": len(every), "n_rates_cote_raisonnable": len(rates),
            "perdus": (hier.get("perdus") or [])[:MAX_LISTE],
            "phrases": _phrases(last, hier, every, rates, familles)}


def _phrases(last: str | None, hier: dict, every: list[dict], rates: list[dict], familles: dict) -> list[str]:
    if not last:
        return ["Pas encore de match terminé à revoir."]
    seen = [r for r in rates if r.get("valeur") is not None and r["valeur"] >= 0]
    under = [r for r in rates if r.get("valeur") is not None and r["valeur"] < 0]
    out = [f"Revue du {_jour_fr(last)} : {hier['matchs']} match(s) terminé(s), {len(every)} pari(s) à une cote de 1,50 "
           f"ou plus ont gagné sans que personne les choisisse, dont {len(rates)} à une cote de "
           f"{_fr(RATES_COTE_MAX)} au plus ; parmi ceux-là, le modèle en voyait {len(seen)} avec de la valeur et en "
           f"sous-estimait {len(under)}."]
    goals = [r for r in rates if r["famille"] in GOAL_FAMILIES][:4]
    if goals:
        out.append("Ratés sur les buts : " + " ; ".join(
            f"{r['pari']} à {_fr(r['cote'])} ({r['match']}) : modèle {_pct(r['proba'])}"
            + (f", bookmakers {_pct(r['proba_marche'])}" if r.get("proba_marche") is not None else "") for r in goals) + ".")
    if hier.get("perdus"):
        out.append("Choisis mais perdus : " + " ; ".join(
            f"{l['pari']} ({l['match']}, modèle {_pct(l['proba'])}"
            + (f", bookmakers {_pct(l['proba_marche'])}" if l.get("proba_marche") is not None else "") + ")"
            for l in hier["perdus"][:5]) + ".")
    # the goal families first (what the owner asks about), then the biggest corrections
    def delta(f: dict) -> float:
        return corrige(f["modele_moyen"], f["correction"]) - f["modele_moyen"]

    moved = sorted((k for k, f in familles.items() if f["correction"] != 1.0 and abs(delta(f)) >= 0.005),
                   key=lambda k: (k not in GOAL_FAMILIES, -abs(familles[k]["correction"] - 1)))
    for f in [familles[k] for k in moved[:6]]:
        p, d = f["modele_moyen"], delta(f)
        out.append(f"{f['nom']} : sur {f['matchs']} matchs, le modèle annonçait {_pct(p)} en moyenne et c'est passé "
                   f"{_pct(f['frequence'])} du temps : je {'remonte' if d > 0 else 'baisse'} sa chance d'environ "
                   f"{max(abs(d) * 100, 1):.0f} point(s) pour les prochains tickets et le joueur.")
    if not moved:
        out.append(f"Aucune famille de paris n'a encore assez de matchs réglés ({MIN_MATCHS} au moins) ou un écart "
                   "assez net pour corriger le modèle : je garde ses chances.")
    return out
