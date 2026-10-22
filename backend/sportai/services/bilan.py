"""The "Résultats et statistiques" tab and the bankroll, from the page's own documents.

Every settled forecast of every day: the tickets and the three best odds of "Mon pronostic", the
selections and the estimated final score of each match, each marked right or wrong, with the hit rate
overall and per kind. The bankroll starts from the page's setting (reglages/bankroll) and follows the
tickets; since 2026-10-15 each kind of ticket (safe, semi-safe, fun) has its own bankroll from that start, and stakes
a fixed share of it as it stood at the start of its day. The combined one is kept for reference. The bonus tickets
(later finds once an official ticket is fixed) have a gauge of their own since 2026-10-17, which never touches the others.
"""

from __future__ import annotations

import math
from datetime import datetime

from sportai.selection.engine import COTE_MIN

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


def under_floor(legs: list[dict]) -> bool:
    """A bet under the odds floor (owner 2026-10-22: never below 1.50, removed from the history too)."""
    return any((l.get("cote") or 0) < COTE_MIN for l in legs)


def replay(pronostics: dict[str, dict], settings: dict, depart: float | None = None,
           restake: bool = False) -> tuple[float, list[dict]]:
    """Bankroll after every settled ticket, in day order; returns the current amount and its history.
    restake: each stake is the ticket's share (mise_pct) of this bankroll, not the amount it was published with."""
    bank, history = float(settings["depart"] if depart is None else depart), []
    for day in sorted(pronostics):
        start = bank
        for key in TICKET_ORDER:
            t = (pronostics[day].get("tickets") or {}).get(key) or {}
            r = _ticket_return(t) if t.get("jambes") and not under_floor(t["jambes"]) else None
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


def replay_bonus(pronostics: dict[str, dict], settings: dict) -> tuple[float, list[dict]]:
    """The bonus tickets' own bankroll (owner 2026-10-17: "une jauge à part"), from the page's start, over every
    settled bonus ticket that was fixed, each staking its kind's share of this bankroll at the start of its day."""
    bank, history = float(settings["depart"]), []
    for day in sorted(pronostics):
        start, bonus = bank, pronostics[day].get("bonus") or {}
        for key in TICKET_ORDER:
            for t in bonus.get(key) or []:
                if not (t.get("jambes") and t.get("fige") and t.get("resultat") in ("gagne", "perdu", "rembourse")) \
                        or under_floor(t["jambes"]):
                    continue
                stake, r = round(start * stake_pct(t, key, settings)[0], 2), _ticket_return(t)
                bank = round(bank + stake * r, 2)
                history.append({"jour": day, "ticket": t.get("titre", f"Idée {key}"), "type": key,
                                "mise": stake, "cote": t.get("cote_totale"), "resultat": t["resultat"],
                                "gain": round(stake * r, 2), "bankroll": bank})
    return bank, history


def stake_pct(t: dict, key: str, settings: dict) -> tuple[float, str | None]:
    """Share of its bankroll a ticket stakes, and why it was cut if it was."""
    from sportai.services.pronostic import TRIAL_STAKE_FACTOR, UCL_STAKE_FACTOR

    pct = settings["mises"].get(key, 0.0)
    if any(l.get("ucl") for l in t["jambes"]):  # Champions League bet inside: half the stake
        return round(pct * UCL_STAKE_FACTOR, 4), None
    if any(not l.get("valide", True) for l in t["jambes"]):  # a "non validé" market inside: half the stake
        return round(pct * TRIAL_STAKE_FACTOR, 4), "pari non validé dans le ticket : mise divisée par deux"
    return pct, None


def apply_stakes(doc: dict, settings: dict, bank_at_start: float | dict[str, float]) -> dict:
    """Stake of each ticket of a day not yet frozen, a fixed share of its own bankroll at the start of the day
    (bank_at_start per kind of ticket; a single amount for all, as before 2026-10-15)."""
    for key, t in (doc.get("tickets") or {}).items():
        if t.get("jambes") and "mise" not in t:
            bank = bank_at_start.get(key, ticket_start(settings)) if isinstance(bank_at_start, dict) else bank_at_start
            pct, reduced = stake_pct(t, key, settings)
            if reduced:
                t["mise_reduite"] = reduced
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
            if t.get("jambes") and t.get("resultat") in ("gagne", "perdu") and not under_floor(t["jambes"]):
                items.append({"jour": day, "type": t.get("titre", key), "categorie": key,
                              "libelle": " + ".join(f"{l['pari']} ({l['match']})" for l in t["jambes"]),
                              "cote": t.get("cote_totale"), "proba": t.get("proba_totale"),
                              "jambes": t["jambes"], "valide": t["resultat"] == "gagne"})
        for leg in doc.get("top3") or []:
            if leg.get("resultat") in ("gagne", "perdu") and not under_floor([leg]):
                items.append({"jour": day, "type": "Top 3 des cotes", "categorie": "top3",
                              "libelle": f"{leg['pari']} ({leg['match']})", "cote": leg["cote"], "proba": leg["proba"],
                              "match_id": leg["match_id"], "valide": leg["resultat"] == "gagne"})
    for key in sorted(matchs):
        m = matchs[key]
        if m.get("statut") != "termine":
            continue
        match = f"{m.get('domicile')} - {m.get('exterieur')}"
        for s in m.get("selections") or []:
            cote = s.get("cote_45") or s.get("cote_3h") or s.get("cote_matin")
            if s.get("resultat") in ("gagne", "perdu") and not (cote and cote < COTE_MIN):
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
    bonus_bank, bonus_history = replay_bonus(pronostics, settings)
    kinds = {}
    for key, label in (("safe", "Top idée safe"), ("semi_safe", "Top idée semi safe"), ("fun", "Top idée fun"),
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
                                                                    ("Top idée safe", "Top idée semi safe", "Top idée fun"))},
                     # the bonus tickets apart (owner 2026-10-17): kept as a trace, never in the gauges above
                     "bonus": {"titre": "La boîte à idées", "depart": settings["depart"], "actuelle": bonus_bank,
                               "progression": round(bonus_bank / settings["objectif"], 6), "mise": settings["mises"],
                               "historique": bonus_history}},
        # "La boîte à idées" (owner 2026-10-17): the bonus tickets as ideas, their success and loss ratio
        "boite_a_idees": ideas_ratio(bonus_history),
        "note": NOTE,
    }


def ideas_ratio(history: list[dict]) -> dict:
    """Success and loss ratio of the ideas, overall and by kind; a refunded idea counts in neither."""
    def count(rows: list[dict]) -> dict:
        won, lost = (sum(r["resultat"] == k for r in rows) for k in ("gagne", "perdu"))
        return {"gagnees": won, "perdues": lost, "remboursees": len(rows) - won - lost,
                "taux_reussite": round(won / (won + lost), 4) if won + lost else None}
    return {"titre": "La boîte à idées", **count(history),
            "par_type": {k: count([r for r in history if r.get("type") == k]) for k in TICKET_ORDER}}
