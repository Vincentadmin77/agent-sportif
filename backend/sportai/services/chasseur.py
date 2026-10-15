"""Le chasseur alpin: a montante (all winnings re-staked) on one coherent single at 1.50 or more.

Owner, 2026-10-14 and 2026-10-15: a separate agent with its own gauge, aiming at 100 000 like the others. It starts a
climb with 100 EUR and stakes the whole climb on one bet; every win is re-staked. As soon as the climb reaches 600 EUR,
it reaches a "refuge": everything above 100 EUR goes into its gauge (the vault) and it starts again with 100 EUR. When
it judges the day too risky it does not bet ("il reste au chalet").

Rules (docs/chasseur-alpin-avis.md, docs/13-lecons.md):
- one single bet a day at most, never a combined ticket, and never before the previous bet is settled (a montante
  only re-stakes money it has actually won);
- odds from 1.50 to 2.00, validated markets only (1X2, double chance, draw no bet; the European cups' win within
  their own limits), never a market on trial;
- chosen on confidence, not on value (lesson of 2026-10-14): the more cautious of the model's and the bookmakers'
  chances must reach SEUIL (58 %), the bookmakers must make it the favourite (>= 50 %), the model must agree with
  the market (pronostic.agrees), and the value on the cautious chance must be >= VALEUR_MIN;
- the pick is frozen 60 minutes before kick-off (the pass 45 minutes before, line-ups known); until then a better
  candidate may replace it;
- after a fall the climb restarts with 100 EUR taken from the vault, or a new 100 EUR (counted in `apports`) when the
  vault holds less;
- it learns: after 10 settled bets winning clearly less often than their chances said, it asks for 62 %.

Virtual only: no real bet is placed. Documents: chasseur/<day> (the day's pick or why none) and chasseur/etat.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sportai.services.prematch import LOCAL_TZ
from sportai.services.pronostic import _settle_ticket, agrees, cautious_proba, confidence

DEPART = 100.0  # each climb starts with 100 EUR
REFUGE = 600.0  # at 600 EUR the climb reaches a refuge: everything above DEPART goes into the vault
OBJECTIF = 100_000.0
COTE_MIN, COTE_MAX = 1.50, 2.00
SEUIL, SEUIL_PRUDENT = 0.58, 0.62
FAVORI = 0.50
VALEUR_MIN = 0.02
FIGE_AVANT = timedelta(minutes=60)
PREUVES = 10  # settled bets before it judges its own record
AVERTISSEMENT = ("Défi virtuel à haut risque : le chasseur remise tout ce qu'il gagne, une seule défaite efface la "
                 "montante en cours. Ce n'est pas une méthode à copier ; le joueur pro montre la gestion prudente.")
REGLES = [
    "Une seule cote par jour au maximum, en pari simple, jamais en combiné.",
    "Cote entre 1,50 et 2,00, uniquement sur les marchés validés.",
    "Au moins 58 % de chances selon le plus prudent du modèle et des bookmakers, et favori des bookmakers.",
    "Pari choisi 45 minutes avant le match, compositions connues.",
    "Il remise tout ce qu'il gagne. À 600 €, il dépose tout ce qui dépasse 100 € dans sa jauge et repart avec 100 €.",
    "Après une chute, il repart avec 100 € pris dans sa jauge.",
    "Si rien n'est assez sûr, il ne joue pas et il dit pourquoi.",
]


def _fr(x: float, dec: int = 2) -> str:
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _started(leg: dict, page: dict[str, dict], now: datetime) -> bool:
    status = (page.get(leg["match_id"]) or {}).get("statut", "a_venir")
    return status != "a_venir" or datetime.fromisoformat(leg["coup_envoi"]) <= now


def eligible(leg: dict, seuil: float = SEUIL) -> str | None:
    """None when the chasseur may climb on this bet, else why not (in French, for the page)."""
    if not leg.get("valide", True) or leg.get("validation"):
        return "marché non validé"
    if not COTE_MIN <= leg["cote"] <= COTE_MAX:
        return "cote hors de 1,50 à 2,00"
    if leg.get("proba_marche") is None:
        return "pas de cote des bookmakers à comparer"
    if leg["proba_marche"] < FAVORI:
        return "les bookmakers n'en font pas le favori"
    if not agrees(leg):
        return "le modèle et les bookmakers ne sont pas d'accord"
    if confidence(leg) < seuil:
        return f"moins de {_pct(seuil)} de chances"
    if cautious_proba(leg) * leg["cote"] - 1 < VALEUR_MIN:
        return "pas assez de valeur"
    return None


def choose(legs: list[dict], page: dict[str, dict], now: datetime, seuil: float = SEUIL) -> tuple[dict | None, str]:
    """The best bet among today's matches still to come: highest confidence, then highest odds."""
    open_legs = [l for l in legs if not _started(l, page, now)]
    ok = [l for l in open_legs if eligible(l, seuil) is None]
    if not ok:
        if not open_legs:
            return None, "Plus aucun match à venir aujourd'hui."
        near = [l for l in open_legs if COTE_MIN <= l["cote"] <= COTE_MAX and l.get("valide", True)]
        best = max(near, key=confidence, default=None)
        why = (f" La meilleure piste, {best['pari']} ({best['match']}) à {_fr(best['cote'])}, est écartée : "
               f"{eligible(best, seuil)}." if best else " Aucune cote validée entre 1,50 et 2,00.")
        return None, "Je reste au chalet : rien d'assez sûr aujourd'hui." + why
    best = max(ok, key=lambda l: (round(confidence(l), 2), l["cote"]))
    clean = {k: v for k, v in best.items() if not k.startswith("_")}
    return clean, (f"Je vise {best['pari']} ({best['match']}) à {_fr(best['cote'])} : {_pct(confidence(best))} de "
                   "chances selon le plus prudent du modèle et des bookmakers.")


def replay(days: dict[str, dict]) -> dict:
    """The climb, the vault and the record after each settled bet, from the first day on."""
    s = {"montante": DEPART, "coffre": 0.0, "apports": DEPART, "serie": 0, "record": DEPART, "refuges": [],
         "chutes": 0, "gagnes": 0, "perdus": 0, "attendus": 0.0, "historique": [], "en_attente": None}
    for day in sorted(days):
        p = (days[day] or {}).get("pari")
        if not p or not p.get("fige"):
            continue
        if p.get("resultat") not in ("gagne", "perdu", "rembourse"):
            s["en_attente"] = day
            continue
        s["attendus"] += p.get("confiance") or 0
        if p["resultat"] == "gagne":
            s["montante"] = round(s["montante"] * p["cote"], 2)
            s["serie"] += 1
            s["gagnes"] += 1
            s["record"] = max(s["record"], s["montante"])
            if s["montante"] >= REFUGE:
                depot = round(s["montante"] - DEPART, 2)
                s["coffre"] = round(s["coffre"] + depot, 2)
                s["refuges"].append({"jour": day, "montante": s["montante"], "depot": depot})
                s["montante"], s["serie"] = DEPART, 0
        elif p["resultat"] == "perdu":
            s["perdus"] += 1
            s["chutes"] += 1
            s["serie"] = 0
            if s["coffre"] >= DEPART:
                s["coffre"] = round(s["coffre"] - DEPART, 2)
            else:
                s["apports"] += DEPART
            s["montante"] = DEPART
        s["historique"].append({"jour": day, "jauge": round(s["coffre"] + s["montante"], 2),
                                "montante": s["montante"], "coffre": s["coffre"]})
    return s


def settle_day(doc: dict, page: dict[str, dict]) -> dict:
    p = (doc or {}).get("pari")
    if not p or not p.get("fige") or p.get("resultat"):
        return doc
    p = _settle_ticket({"jambes": [p]}, page)["jambes"][0]
    return {**doc, "pari": p, "statut": "joue"} if p.get("resultat") else doc


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

    if old and old.get("fige"):
        pari, raison = old, (previous_day or {}).get("raison")
    elif state["en_attente"]:
        pari, raison = None, (f"J'attends le résultat de mon pari du {state['en_attente']} : je ne remise que ce que "
                              "j'ai vraiment gagné.")
    else:
        pari, raison = choose(legs, page, now, seuil)
        if pari:
            ko = datetime.fromisoformat(pari["coup_envoi"])
            pari = {**pari, "confiance": round(confidence(pari), 4), "mise": state["montante"],
                    "gain_possible": round(state["montante"] * (pari["cote"] - 1), 2),
                    "fige": now >= ko - FIGE_AVANT}
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

    after = replay({**earlier, day: doc})
    jauge = round(after["coffre"] + after["montante"], 2)
    etat = {
        "maj": now.isoformat(), "nom": "Le chasseur alpin",
        "jauge": jauge, "montante": after["montante"], "coffre": after["coffre"], "apports": after["apports"],
        "net": round(jauge - after["apports"], 2), "objectif": OBJECTIF, "progression": round(jauge / OBJECTIF, 6),
        "depart": DEPART, "refuge": REFUGE, "serie": after["serie"], "record_montante": after["record"],
        "refuges": after["refuges"], "chutes": after["chutes"],
        "bilan": {"joues": after["gagnes"] + after["perdus"], "gagnes": after["gagnes"], "perdus": after["perdus"]},
        "historique": after["historique"][-200:], "seuil": seuil, "regles": REGLES, "avertissement": AVERTISSEMENT,
        "aujourdhui": {"jour": day, "statut": statut, "raison": raison},
    }
    changed = {d: v for d, v in earlier.items() if v is not past_days[d]}
    return doc, etat, changed


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
