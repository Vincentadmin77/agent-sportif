"""Le chasseur alpin: a montante on one coherent single a day at 1.50 or more.

Owner, 2026-10-14 to 2026-10-21: a separate agent with its own gauge, aiming at 100 000 like the others. Its bankroll
starts at 100 EUR. Each climb stakes MISE_DEPART (25 EUR) from it, and every win re-stakes the whole return, stake and
gain (owner 2026-10-21: "doit remettre la totalité de ce qu'il a gagné à chaque fois ... c'est une montante"). A loss
ends the climb; the next one starts again with 25 EUR. As soon as the stake riding reaches 600 EUR it reaches a
"refuge": everything above 25 EUR goes into the vault and it carries on with 25 EUR. When it judges the day too risky
it does not bet ("il reste au chalet").

Rules (docs/chasseur-alpin-avis.md, docs/13-lecons.md):
- one single bet a day at most, never a combined ticket, and never before the previous bet is settled (a montante
  only re-stakes money it has actually won);
- odds from 1.50 to 2.00, on any market the model prices, like the joueur pro (owner 2026-10-15: "les mêmes
  capacités que le joueur pro"): a market the backtest has not validated asks 3 % of value and has its own record,
  and goes back to paper (noted, not played) after ESSAI_ARRET settled bets winning clearly less than announced;
- no live bet (owner 2026-10-15: not a copy of the joueur pro, which bets live): the pick may change until 30
  minutes before kick-off, then it is frozen, by the pass or by the live loop, at its last price seen; the bet is
  followed minute by minute and settled at the final whistle, so the climb moves as soon as a match ends;
- chosen on confidence, not on value (lesson of 2026-10-14): the more cautious of the model's and the bookmakers'
  chances must reach SEUIL (58 %), the bookmakers must make it the favourite (>= 50 %), the model must agree with
  the market (pronostic.agrees), and the value on the cautious chance must be >= VALEUR_MIN;
- when the bankroll cannot pay a new 25 EUR stake, it is taken from the vault, or from a new contribution (counted in
  `apports`);
- the 10 % stakes of 2026-10-16 to 2026-10-21 are settled with their own stake; their net gain rides on the first
  climb (5.30 EUR won on 2026-10-17: first stake 30.30 EUR);
- the bets of 2026-10-15, staked all-in under the first rule, are kept for the record but left out of the climb
  (owner 2026-10-16: "remets-lui les 100 euros");
- it learns: after 10 settled bets winning clearly less often than their chances said, it asks for 62 %.

Virtual only: no real bet is placed. Documents: chasseur/<day> (the day's pick or why none) and chasseur/etat.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sportai.services.prematch import LOCAL_TZ
from sportai.services.pronostic import TRIAL_MIN_VALUE, _settle_ticket, agrees, cautious_proba, confidence

DEPART = 100.0  # each climb starts with 100 EUR
REFUGE = 600.0  # at 600 EUR the climb reaches a refuge: everything above DEPART goes into the vault
MISE_DEPART = 25.0  # owner 2026-10-21: each climb starts with a 25 EUR stake, then re-stakes the whole return
REGLE = "montante"  # bets placed under that rule
OBJECTIF = 100_000.0
COTE_MIN, COTE_MAX = 1.50, 2.00
SEUIL, SEUIL_PRUDENT = 0.58, 0.62
FAVORI = 0.50
VALEUR_MIN = 0.02
FIGE_AVANT = timedelta(minutes=30)  # owner 2026-10-15: its ticket is frozen 30 min before kick-off
PREUVES = 10  # settled bets before it judges its own record
ESSAI_ARRET = (8, 0.8)  # a market on trial: back to paper after 8 settled bets winning under 80 % of the announced
DIRECT = "Direct"
AVERTISSEMENT = ("Défi virtuel à haut risque : le chasseur remise la totalité de ce qu'il gagne (une montante), une "
                 "défaite fait repartir la montante à 25 €. Ce n'est pas une méthode à copier ; le joueur pro montre "
                 "la gestion prudente.")
REGLES = [
    "Une seule cote par jour au maximum, en pari simple, jamais en combiné.",
    "Cote entre 1,50 et 2,00, sur tous les marchés, mais seulement des cotes vérifiées : chiffrées par le modèle et "
    "comparées aux bookmakers. Un marché pas encore validé demande "
    "3 % de valeur et il est arrêté si son bilan perd.",
    "Au moins 58 % de chances selon le plus prudent du modèle et des bookmakers, et favori des bookmakers.",
    "Pari figé 30 minutes avant le match, compositions connues ; il ne parie pas en direct, mais il suit son pari "
    "minute par minute.",
    "Sa bankroll de départ est de 100 €. Chaque montante démarre avec une mise de 25 €.",
    "Quand il gagne, il remise la totalité : la mise et le gain. Quand il perd, la montante repart à 25 €.",
    "Dès que la mise atteint 600 €, il met de côté tout ce qui dépasse 25 € et repart avec 25 €.",
    "Si rien n'est assez sûr, il ne joue pas et il dit pourquoi.",
]


def _fr(x: float, dec: int = 2) -> str:
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _started(leg: dict, page: dict[str, dict], now: datetime) -> bool:
    status = (page.get(leg["match_id"]) or {}).get("statut", "a_venir")
    return status != "a_venir" or datetime.fromisoformat(leg["coup_envoi"]) <= now


def family(leg: dict) -> str | None:
    """The trial group of a bet: None for a validated market, "Direct" for a live bet, else its market."""
    if leg.get("en_direct"):
        return DIRECT
    if leg.get("valide", True) and not leg.get("validation"):
        return None
    return leg.get("marche") or leg.get("pari")


def eligible(leg: dict, seuil: float = SEUIL, arretes: set[str] | frozenset = frozenset()) -> str | None:
    """None when the chasseur may climb on this bet, else why not (in French, for the page)."""
    fam = family(leg)
    if fam in arretes:
        return f"{fam} : mon bilan sur ce marché perd, je le note sans le jouer"
    if not COTE_MIN <= leg["cote"] <= COTE_MAX:
        return "cote hors de 1,50 à 2,00"
    if leg.get("proba") is None or leg.get("proba_marche") is None:  # owner 2026-10-15: "pas de cote non vérifiée"
        return "cote non vérifiée : pas chiffrée par le modèle ou pas comparée aux bookmakers"
    if leg["proba_marche"] < FAVORI:
        return "les bookmakers n'en font pas le favori"
    if not agrees(leg):
        return "le modèle et les bookmakers ne sont pas d'accord"
    if confidence(leg) < seuil:
        return f"moins de {_pct(seuil)} de chances"
    if cautious_proba(leg) * leg["cote"] - 1 < (TRIAL_MIN_VALUE if fam else VALEUR_MIN):
        return "pas assez de valeur"
    return None


def choose(legs: list[dict], page: dict[str, dict], now: datetime, seuil: float = SEUIL,
           arretes: set[str] | frozenset = frozenset()) -> tuple[dict | None, str]:
    """The best bet among today's matches still to come: highest confidence, then a validated market, then the
    highest odds."""
    open_legs = [l for l in legs if not _started(l, page, now)]
    ok = [l for l in open_legs if eligible(l, seuil, arretes) is None]
    if not ok:
        if not open_legs:
            return None, "Plus aucun match à venir aujourd'hui."
        near = [l for l in open_legs if COTE_MIN <= l["cote"] <= COTE_MAX]
        best = max(near, key=confidence, default=None)
        why = (f" La meilleure piste, {best['pari']} ({best['match']}) à {_fr(best['cote'])}, est écartée : "
               f"{eligible(best, seuil, arretes)}." if best else " Aucune cote entre 1,50 et 2,00.")
        return None, "Je reste au chalet : rien d'assez sûr aujourd'hui." + why
    best = max(ok, key=lambda l: (round(confidence(l), 2), family(l) is None, l["cote"]))
    clean = {k: v for k, v in best.items() if not k.startswith("_")}
    return clean, (f"Je vise {best['pari']} ({best['match']}) à {_fr(best['cote'])} : {_pct(confidence(best))} de "
                   "chances selon le plus prudent du modèle et des bookmakers.")


def replay(days: dict[str, dict]) -> dict:
    """The bankroll, the stake riding (the montante), the vault and the record after each settled bet.

    Each climb starts with MISE_DEPART taken from the bankroll; a win re-stakes the whole return (stake + gain); a loss
    ends the climb and the next starts again with MISE_DEPART. At REFUGE the climb deposits everything above
    MISE_DEPART into the vault and carries on with MISE_DEPART. The 10 % stakes of 2026-10-16 to 2026-10-21 (no
    `regle`) are settled with their own stake into the bankroll; their net gain then starts the first climb (owner
    2026-10-21: "il a pris 5 il met 30")."""
    s = {"banque": DEPART, "montante": None, "coffre": 0.0, "apports": DEPART, "serie": 0, "record": MISE_DEPART,
         "refuges": [], "chutes": 0, "gagnes": 0, "perdus": 0, "attendus": 0.0, "historique": [], "en_attente": None,
         "essais": {}}

    def start(gain: float = 0.0) -> None:  # a new climb: MISE_DEPART (+ a carried gain) from the bankroll
        need = round(MISE_DEPART + gain, 2)
        if s["banque"] < need:
            if s["coffre"] >= need - s["banque"]:
                s["coffre"] = round(s["coffre"] - (need - s["banque"]), 2)
            else:
                s["apports"] = round(s["apports"] + need - s["banque"], 2)
            s["banque"] = need
        s["banque"], s["montante"] = round(s["banque"] - need, 2), need

    for day in sorted(days):
        p = (days[day] or {}).get("pari")
        if not p or not p.get("fige") or p.get("annule") or not ("mise_pct" in p or p.get("regle") == REGLE):
            continue  # the all-in bet of 2026-10-15: left out
        if p.get("resultat") not in ("gagne", "perdu", "rembourse"):
            s["en_attente"] = day
            continue
        s["attendus"] += p.get("confiance") or 0
        fam = family(p)
        if fam and p["resultat"] != "rembourse":
            e = s["essais"].setdefault(fam, {"joues": 0, "gagnes": 0, "attendus": 0.0})
            e["joues"] += 1
            e["gagnes"] += p["resultat"] == "gagne"
            e["attendus"] = round(e["attendus"] + (p.get("confiance") or 0), 4)
        won, lost = p["resultat"] == "gagne", p["resultat"] == "perdu"
        s["gagnes"] += won
        s["perdus"] += lost
        if p.get("regle") != REGLE:  # a 10 % stake of the second rule: into the bankroll with its own stake
            s["banque"] = round(s["banque"] + (p["mise"] * (p["cote"] - 1) if won else -p["mise"] if lost else 0), 2)
        else:
            if s["montante"] is None:
                start(max(0.0, s["banque"] - DEPART))
            if won:
                s["montante"] = round(s["montante"] * p["cote"], 2)
                s["serie"] += 1
                s["record"] = max(s["record"], s["montante"])
                if s["montante"] >= REFUGE:
                    depot = round(s["montante"] - MISE_DEPART, 2)
                    s["coffre"] = round(s["coffre"] + depot, 2)
                    s["refuges"].append({"jour": day, "montante": s["montante"], "depot": depot})
                    s["montante"], s["serie"] = MISE_DEPART, 0
            elif lost:
                s["chutes"] += 1
                s["serie"] = 0
                s["montante"] = None
                start()
        s["historique"].append({"jour": day, "jauge": jauge(s), "montante": s["montante"], "coffre": s["coffre"],
                                "banque": s["banque"]})
    if s["montante"] is None:
        start(max(0.0, s["banque"] - DEPART))
    return s


def jauge(s: dict) -> float:
    return round(s["banque"] + (s["montante"] or 0) + s["coffre"], 2)


def settle_day(doc: dict, page: dict[str, dict]) -> dict:
    p = (doc or {}).get("pari")
    if not p or not p.get("fige") or p.get("resultat"):
        return doc
    p = _settle_ticket({"jambes": [p]}, page)["jambes"][0]
    return {**doc, "pari": p, "statut": "joue"} if p.get("resultat") else doc


def stopped(s: dict) -> set[str]:
    """The markets on trial whose record says the model overrates them: back to paper."""
    n, ratio = ESSAI_ARRET
    return {f for f, e in s["essais"].items() if e["joues"] >= n and e["gagnes"] < ratio * e["attendus"]}


def threshold(s: dict) -> tuple[float, str | None]:
    """SEUIL, or SEUIL_PRUDENT when its settled bets won clearly less often than their chances said."""
    n = s["gagnes"] + s["perdus"]
    if n >= PREUVES and s["gagnes"] < 0.9 * s["attendus"]:
        return SEUIL_PRUDENT, (f"Sur mes {n} derniers paris j'ai gagné {s['gagnes']} fois au lieu d'environ "
                               f"{s['attendus']:.0f} : je demande désormais {_pct(SEUIL_PRUDENT)} de chances.")
    return SEUIL, None


def run(legs: list[dict], page: dict[str, dict], previous_day: dict | None, past_days: dict[str, dict],
        now: datetime, day: str) -> tuple[dict, dict, dict[str, dict]]:
    """The day's chasseur/<day> and chasseur/etat documents, and the earlier days whose bet has just been settled
    (a match after midnight is settled at the next morning's pass)."""
    earlier = {d: settle_day(v, page) for d, v in past_days.items() if d < day}
    state = replay(earlier)
    seuil, lecon = threshold(state)
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    journal = list((previous_day or {}).get("journal") or [])
    old = (previous_day or {}).get("pari")

    if old and not old.get("fige") and now >= datetime.fromisoformat(old["coup_envoi"]) - FIGE_AVANT:
        old = {**old, "fige": True, "fige_a": now.isoformat()}  # its time has come: frozen at the last price seen
    if old and old.get("fige"):
        pari, raison = old, (previous_day or {}).get("raison")
        old = (previous_day or {}).get("pari")
    elif state["en_attente"]:
        pari, raison = None, (f"J'attends le résultat de mon pari du {state['en_attente']} : je ne remise que ce que "
                              "j'ai vraiment gagné.")
    else:
        pari, raison = choose(legs, page, now, seuil, stopped(state))
        if pari:
            ko = datetime.fromisoformat(pari["coup_envoi"])
            same = old and (old["match_id"], old["pari"]) == (pari["match_id"], pari["pari"])
            cotes = list((old or {}).get("cotes") or []) if same else []
            if not cotes or cotes[-1]["cote"] != pari["cote"]:  # the price watched across passes
                cotes.append({"heure": now.isoformat(), "cote": pari["cote"]})
            mise = state["montante"]
            pari = {**pari, "confiance": round(confidence(pari), 4), "regle": REGLE, "mise": mise,
                    "bankroll": state["banque"], "gain_possible": round(mise * (pari["cote"] - 1), 2),
                    "fige": now >= ko - FIGE_AVANT, "cotes": cotes[-20:]}
        elif old:  # an unfrozen pick no longer qualifies: dropped
            raison = f"Je retire {old['pari']} ({old['match']}) : il ne passe plus mes règles. " + raison
    if pari:
        pari = _settle_ticket({"jambes": [pari]}, page)["jambes"][0]

    if lecon and not any(x["texte"] == lecon for x in journal):
        journal.append({"heure": heure, "texte": lecon})
    if raison and (not journal or journal[-1]["texte"] != raison):
        journal.append({"heure": heure, "texte": raison})
    if pari and pari.get("fige") and not (old or {}).get("fige"):
        journal.append({"heure": heure, "texte": f"Pari figé : {_fr(pari['mise'])} € sur {pari['pari']} à "
                                                 f"{_fr(pari['cote'])}."})
    if pari and pari.get("resultat") and not (old or {}).get("resultat"):
        word = {"gagne": "gagné", "perdu": "perdu", "rembourse": "remboursé"}[pari["resultat"]]
        journal.append({"heure": heure, "texte": f"{pari['pari']} {word}."})

    statut = ("joue" if pari and pari.get("fige") else "vise" if pari else
              "en_attente" if state["en_attente"] else "au_chalet")
    doc = {"jour": day, "maj": now.isoformat(), "statut": statut, "pari": pari, "raison": raison,
           "montante_du_jour": state["montante"], "seuil": seuil, "journal": journal[-40:]}
    for key in ("en_direct",):  # written by the live loop, kept across the pre-match passes
        if (previous_day or {}).get(key):
            doc[key] = previous_day[key]

    etat = _etat(replay({**earlier, day: doc}), now, doc)
    changed = {d: v for d, v in earlier.items() if v is not past_days[d]}
    return doc, etat, changed


def _etat(after: dict, now: datetime, doc: dict) -> dict:
    total = jauge(after)
    return {
        "maj": now.isoformat(), "nom": "Le chasseur alpin",
        # jauge = bankroll left + the stake riding (montante, its next stake) + the vault of the refuges
        "jauge": total, "banque": after["banque"], "montante": after["montante"], "mise_suivante": after["montante"],
        "coffre": after["coffre"], "apports": after["apports"],
        "net": round(total - after["apports"], 2), "objectif": OBJECTIF, "progression": round(total / OBJECTIF, 6),
        "depart": DEPART, "mise_depart": MISE_DEPART, "refuge": REFUGE, "serie": after["serie"], "record_montante": after["record"],
        "refuges": after["refuges"], "chutes": after["chutes"],
        "bilan": {"joues": after["gagnes"] + after["perdus"], "gagnes": after["gagnes"], "perdus": after["perdus"]},
        "historique": after["historique"][-200:], "seuil": doc["seuil"], "regles": REGLES,
        "avertissement": AVERTISSEMENT, "essais": after["essais"], "sur_papier": sorted(stopped(after)),
        "aujourdhui": {"jour": doc["jour"], "statut": doc["statut"], "raison": doc["raison"],
                       **({"en_direct": doc["en_direct"]} if doc.get("en_direct") else {})},
    }


def write(page_folder: str | None, out: str, legs: list[dict], page: dict[str, dict], now: datetime,
          day: str) -> dict:
    """Write out/chasseur/<day>.json and out/chasseur/etat.json from the page copy (collection `chasseur`)."""
    folder = Path(page_folder or "/nonexistent") / "chasseur"
    past = {p.stem: json.loads(p.read_text()) for p in folder.glob("*.json") if p.stem != "etat"}
    doc, etat, changed = run(legs, page, past.get(day), past, now, day)
    (Path(out) / "chasseur").mkdir(parents=True, exist_ok=True)
    for name, body in ((day, doc), ("etat", etat), *changed.items()):
        (Path(out) / "chasseur" / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc


def live(doc: dict, page: dict[str, dict], now: datetime, past_days: dict[str, dict]) -> dict:
    """The day's document during the matches: its pick frozen 30 minutes before kick-off if no pass did it, then
    followed minute by minute and settled at the final whistle. It never bets live."""
    from sportai.services.joueur import leg_live_chance

    doc = {**doc, "journal": list(doc.get("journal") or [])}
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    p = doc.get("pari")
    if p and not p.get("fige") and now >= datetime.fromisoformat(p["coup_envoi"]) - FIGE_AVANT:
        p = {**p, "fige": True, "fige_a": now.isoformat()}
        doc["statut"] = "joue"
        doc["journal"].append({"heure": heure, "texte": f"Pari figé : {_fr(p['mise'])} € sur {p['pari']} à "
                                                        f"{_fr(p['cote'])}."})
    if p and p.get("fige"):
        m = page.get(p["match_id"]) or {}
        if m.get("statut") in ("en_direct", "termine"):
            c = leg_live_chance(p, m)
            old = p.get("direct") or {}
            p = {**p, "direct": {"maj": now.isoformat(), "score": m.get("score"), "minute": m.get("minute"),
                                 "etat": "termine" if m["statut"] == "termine" else "en_cours",
                                 "chance_avant": p.get("confiance"), "chance": None if c is None else round(c, 4)}}
            doc["en_direct"] = {"maj": now.isoformat(), "resume": (
                f"En direct : {p['pari']} ({p['match']} {m.get('score') or ''}"
                + (f", {m.get('minute')}e" if m["statut"] == "en_direct" and m.get("minute") else "") + ")"
                + (f", {_pct(c)} de chances." if c is not None else "."))}
            if m.get("score") != old.get("score") and m["statut"] == "en_direct":
                doc["journal"].append({"heure": heure, "texte": (
                    f"{p['match']} {m.get('score') or ''} ({m.get('minute')}e) : "
                    + (f"{_pct(c)} de chances pour {p['pari']}." if c is not None else f"je suis {p['pari']}."))})
        settled = _settle_ticket({"jambes": [p]}, page)["jambes"][0]
        if settled.get("resultat") and not p.get("resultat"):
            word = {"gagne": "gagné", "perdu": "perdu", "rembourse": "remboursé"}[settled["resultat"]]
            doc["journal"].append({"heure": heure, "texte": f"{p['pari']} {word}."})
        p = settled
    doc["pari"] = p
    doc["maj"] = now.isoformat()
    doc["journal"] = doc["journal"][-40:]
    return doc


def live_write(page_folder: str | None, out: str, page: dict[str, dict], now: datetime) -> list[str]:
    """Update out/chasseur/<day>.json and etat for each day with a match under way or just ended."""
    folder = Path(page_folder or "/nonexistent") / "chasseur"
    past = {p.stem: json.loads(p.read_text()) for p in folder.glob("*.json") if p.stem != "etat"}
    days = sorted({m.get("jour") for m in page.values()} & set(past))
    if not days:
        return []
    for name in days:
        past[name] = live(past[name], page, now, past)
    last = past[max(past)]
    etat = _etat(replay({d: settle_day(v, page) for d, v in past.items()}), now, last)
    (Path(out) / "chasseur").mkdir(parents=True, exist_ok=True)
    for name, body in (*((d, past[d]) for d in days), ("etat", etat)):
        (Path(out) / "chasseur" / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return days
