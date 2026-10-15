"""The "Résultats et statistiques" tab and the bankroll, from the page's own documents.

Every settled forecast of every day: the tickets and the three best odds of "Mon pronostic", the
selections and the estimated final score of each match, each marked right or wrong, with the hit rate
overall and per kind. The bankroll starts from the page's setting (reglages/bankroll) and follows the
tickets; since 2026-10-15 each kind of ticket (safe, semi-safe, fun) has its own bankroll from that start, and stakes
a fixed share of it as it stood at the start of its day. The combined one is kept for reference.
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


def replay(pronostics: dict[str, dict], settings: dict, depart: float | None = None,
           restake: bool = False) -> tuple[float, list[dict]]:
    """Bankroll after every settled ticket, in day order; returns the current amount and its history.
    restake: each stake is the ticket's share (mise_pct) of this bankroll, not the amount it was published with."""
    bank, history = float(settings["depart"] if depart is None else depart), []
    for day in sorted(pronostics):
        start = bank
        for key in TICKET_ORDER:
            t = (pronostics[day].get("tickets") or {}).get(key) or {}
            r = _ticket_return(t) if t.get("jambes") else None
            if r is None:
                continue
            stake = (round(start * t.get("mise_pct", settings["mises"][key]), 2) if restake or "mise" not in t
                     else t["mise"])
            bank = round(bank + stake * r, 2)
            history.append({"jour": day, "ticket": t.get("titre", key), "mise": stake,
                            "cote": t.get("cote_totale"), "resultat": t["resultat"], "gain": round(stake * r, 2),
                            "bankroll": bank})
    return bank, history


def ticket_start(settings: dict) -> float:
    """Start of each kind's bankroll: the page's start split in three (owner 2026-10-15: "divise par trois")."""
    return round(settings["depart"] / len(TICKET_ORDER), 2)


def replay_by_ticket(pronostics: dict[str, dict], settings: dict) -> dict[str, tuple[float, list[dict]]]:
    """One bankroll per kind of ticket (owner 2026-10-15: "une jauge par ticket, sinon les fun vont plomber les
    safe"), a third of the start each, replayed over the settled tickets of that kind only, each staking its share
    of its own bankroll (also for the days before, published with stakes on the combined one)."""
    out = {}
    for key in TICKET_ORDER:
        only = {d: {**doc, "tickets": {key: (doc.get("tickets") or {}).get(key) or {}}} for d, doc in pronostics.items()}
        out[key] = replay(only, settings, ticket_start(settings), restake=True)
    return out


def apply_stakes(doc: dict, settings: dict, bank_at_start: float | dict[str, float]) -> dict:
    """Stake of each ticket of a day not yet frozen, a fixed share of its own bankroll at the start of the day
    (bank_at_start per kind of ticket; a single amount for all, as before 2026-10-15)."""
    for key, t in (doc.get("tickets") or {}).items():
        if t.get("jambes") and "mise" not in t:
            bank = bank_at_start.get(key, ticket_start(settings)) if isinstance(bank_at_start, dict) else bank_at_start
            pct = settings["mises"].get(key, 0.0)
            from sportai.services.pronostic import TRIAL_STAKE_FACTOR, UCL_STAKE_FACTOR

            if any(l.get("ucl") for l in t["jambes"]):  # Champions League bet inside: half the stake
                pct = round(pct * UCL_STAKE_FACTOR, 4)
            elif any(not l.get("valide", True) for l in t["jambes"]):  # a "non validé" market inside: half the stake
                pct = round(pct * TRIAL_STAKE_FACTOR, 4)
                t["mise_reduite"] = "pari non validé dans le ticket : mise divisée par deux"
            t["mise_pct"] = pct
            t["mise"] = round(bank * pct, 2)
    if isinstance(bank_at_start, dict):
        doc["bankrolls_du_jour"] = {k: round(v, 2) for k, v in bank_at_start.items()}
    else:
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
                          "explication": f"Score estimé {est['score']}, score final {m.get('score')} : issue "
                                         + ("juste" if est["resultat"] == "reussi" else "fausse")
                                         + (", score exact trouvé." if est.get("score_exact") else ".")})
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
                     "historique": history,
                     # one gauge per kind of ticket (owner 2026-10-15): the fun tickets no longer drag the safe ones
                     "par_ticket": {key: {"titre": title, "depart": ticket_start(settings), "actuelle": b,
                                          "progression": round(b / (settings["objectif"] / len(TICKET_ORDER)), 6),
                                          "mise": settings["mises"][key], "historique": h}
                                    for (key, (b, h)), title in zip(replay_by_ticket(pronostics, settings).items(),
                                                                    ("Ticket safe", "Ticket semi safe", "Ticket fun"))}},
        "note": NOTE,
    }
