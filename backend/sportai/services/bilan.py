"""The "Résultats et statistiques" tab and the bankroll, from the page's own documents.

Every settled forecast of every day: the tickets and the three best odds of "Mon pronostic", the
selections and the estimated final score of each match, each marked right or wrong, with the hit rate
overall and per kind. The bankroll starts from the page's setting (reglages/bankroll) and follows the
tickets: each ticket stakes a fixed share of the bankroll as it stood at the start of its day.
"""

from __future__ import annotations

import math
from datetime import datetime

BANKROLL_DEFAULTS = {"depart": 100.0, "objectif": 100000.0, "mises": {"safe": 0.03, "semi_safe": 0.02, "fun": 0.01}}
TICKET_ORDER = ("safe", "semi_safe", "fun")
NOTE = ("Seuls les pronostics dont le match est terminé comptent. Un score exact est rarement trouvé (environ une fois "
        "sur dix pour le score le plus probable) : ce taux ne mesure pas la qualité des autres pronostics.")


def bankroll_settings(raw: dict | None) -> dict:
    out = {**BANKROLL_DEFAULTS, **(raw or {})}
    out["mises"] = {**BANKROLL_DEFAULTS["mises"], **((raw or {}).get("mises") or {})}
    return out


def _ticket_return(ticket: dict) -> float | None:
    """Gain per unit staked once settled: odds of the won legs minus 1, -1 if any leg lost."""
    res = ticket.get("resultat")
    if res in (None, "en_cours"):
        return None
    if res == "perdu":
        return -1.0
    return math.prod(l["cote"] for l in ticket["jambes"] if l.get("resultat") == "gagne") - 1


def replay(pronostics: dict[str, dict], settings: dict) -> tuple[float, list[dict]]:
    """Bankroll after every settled ticket, in day order; returns the current amount and its history."""
    bank, history = float(settings["depart"]), []
    for day in sorted(pronostics):
        start = bank
        for key in TICKET_ORDER:
            t = (pronostics[day].get("tickets") or {}).get(key) or {}
            r = _ticket_return(t) if t.get("jambes") else None
            if r is None:
                continue
            stake = t.get("mise") or round(start * settings["mises"][key], 2)
            bank = round(bank + stake * r, 2)
            history.append({"jour": day, "ticket": t.get("titre", key), "mise": stake,
                            "cote": t.get("cote_totale"), "resultat": t["resultat"], "gain": round(stake * r, 2),
                            "bankroll": bank})
    return bank, history


def apply_stakes(doc: dict, settings: dict, bank_at_start: float) -> dict:
    """Stake of each ticket of a day not yet frozen, a fixed share of the bankroll at the start of the day."""
    for key, t in (doc.get("tickets") or {}).items():
        if t.get("jambes") and "mise" not in t:
            pct = settings["mises"].get(key, 0.0)
            t["mise_pct"] = pct
            t["mise"] = round(bank_at_start * pct, 2)
    doc["bankroll_du_jour"] = round(bank_at_start, 2)
    return doc


def _rate(items: list[dict]) -> dict:
    ok = sum(i["valide"] for i in items)
    return {"valides": ok, "non_valides": len(items) - ok, "taux": round(ok / len(items), 4) if items else None}


def build_bilan(matchs: dict[str, dict], pronostics: dict[str, dict], settings: dict, now: datetime) -> dict:
    items = []
    for day in sorted(pronostics):
        doc = pronostics[day]
        for key in TICKET_ORDER:
            t = (doc.get("tickets") or {}).get(key) or {}
            if t.get("jambes") and t.get("resultat") in ("gagne", "perdu"):
                items.append({"jour": day, "type": t.get("titre", key), "categorie": key,
                              "libelle": " + ".join(f"{l['pari']} ({l['match']})" for l in t["jambes"]),
                              "cote": t.get("cote_totale"), "proba": t.get("proba_totale"),
                              "jambes": t["jambes"], "valide": t["resultat"] == "gagne"})
        for leg in doc.get("top3") or []:
            if leg.get("resultat") in ("gagne", "perdu"):
                items.append({"jour": day, "type": "Top 3 des cotes", "categorie": "top3",
                              "libelle": f"{leg['pari']} ({leg['match']})", "cote": leg["cote"], "proba": leg["proba"],
                              "match_id": leg["match_id"], "valide": leg["resultat"] == "gagne"})
    for key in sorted(matchs):
        m = matchs[key]
        if m.get("statut") != "termine":
            continue
        match = f"{m.get('domicile')} - {m.get('exterieur')}"
        for s in m.get("selections") or []:
            if s.get("resultat") in ("gagne", "perdu"):
                items.append({"jour": m.get("jour"), "type": "Sélection", "categorie": "selection",
                              "libelle": f"{s['pari']} ({match})", "cote": s.get("cote_45") or s.get("cote_3h")
                              or s.get("cote_matin"), "proba": s.get("proba"), "match_id": key,
                              "valide": s["resultat"] == "gagne"})
        est = m.get("score_estime") or {}
        if est.get("resultat"):
            items.append({"jour": m.get("jour"), "type": "Score estimé", "categorie": "score",
                          "libelle": f"{match} : {est['score']} (réel {m.get('score')})", "cote": None,
                          "proba": est.get("proba"), "match_id": key, "valide": est["resultat"] == "reussi",
                          "explication": f"Score estimé {est['score']}, score final {m.get('score')}."})
    items.sort(key=lambda i: (i["jour"] or "", i["type"]), reverse=True)
    bank, history = replay(pronostics, settings)
    kinds = {}
    for key, label in (("safe", "Ticket safe"), ("semi_safe", "Ticket semi safe"), ("fun", "Ticket fun"),
                       ("top3", "Top 3 des cotes"), ("selection", "Sélections"), ("score", "Scores estimés")):
        kinds[key] = {"titre": label, **_rate([i for i in items if i["categorie"] == key])}
    return {
        "maj": now.isoformat(), "global": _rate(items), "par_type": kinds,
        "valides": [i for i in items if i["valide"]], "non_valides": [i for i in items if not i["valide"]],
        "bankroll": {"depart": settings["depart"], "objectif": settings["objectif"], "actuelle": bank,
                     "progression": round(bank / settings["objectif"], 6), "mises": settings["mises"],
                     "historique": history},
        "note": NOTE,
    }
