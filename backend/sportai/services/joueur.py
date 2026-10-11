"""The professional player: decides which of the day's proposals to play and how much to stake.

It works on the page's virtual bankroll only and never places a real bet. Each pass it reads the day's
"Mon pronostic" document (the tickets and the three best odds), keeps what is already under way, and for
the rest stakes a capped fraction of the Kelly stake (the stake that grows a bankroll fastest when the
probabilities are right), by the profile chosen on the page (reglages/joueur). It passes when nothing is
worth it, never bets a leg the backtest has not validated, and takes one position per match at most.

Documents written for the page:
- joueur/<day>: the day's decisions (played or passed, and why), the stakes, the results, a journal.
- joueur/etat: the profile, the player's bankroll and its history, the plan towards the target, and a
  simulation of the three profiles showing what bigger stakes buy (speed) and cost (risk of ruin).
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from sportai.services.prematch import LOCAL_TZ
from sportai.services.pronostic import _settle_ticket

# Fraction of the Kelly stake, most of the bankroll on one bet and on one day, least value and chance to play.
PROFILS = {
    "prudent": {"titre": "Prudent", "kelly": 0.125, "max_pari": 0.02, "max_jour": 0.05, "valeur_min": 0.03,
                "proba_min": 0.40},
    "equilibre": {"titre": "Équilibré", "kelly": 0.25, "max_pari": 0.05, "max_jour": 0.10, "valeur_min": 0.02,
                  "proba_min": 0.30},
    "agressif": {"titre": "Agressif", "kelly": 0.5, "max_pari": 0.10, "max_jour": 0.20, "valeur_min": 0.01,
                 "proba_min": 0.20},
}
DEFAUT, RECOMMANDE = "prudent", "prudent"
FREIN = 0.6  # below 60 % of its best level the player drops to the profile below until it recovers
MISE_MIN = 0.10
TICKET_ORDER = ("safe", "semi_safe", "fun")
AVERTISSEMENT = ("Joueur virtuel : il ne place aucun vrai pari. Ses probabilités viennent du modèle, qui n'a pas encore "
                 "prouvé qu'il bat les bookmakers : tant que ce n'est pas mesuré, un bénéfice n'est pas garanti.")


def _fr(x: float, dec: int = 2) -> str:
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _pct(x: float, dec: int = 1) -> str:
    return f"{x * 100:.{dec}f} %".replace(".", ",")


def _started(leg: dict, page: dict[str, dict], now: datetime) -> bool:
    status = (page.get(leg["match_id"]) or {}).get("statut", "a_venir")
    return leg.get("verrouille") or status != "a_venir" or datetime.fromisoformat(leg["coup_envoi"]) <= now


def candidates(pronostic: dict) -> list[dict]:
    """What the player may play today: each ticket and each of the three best odds as a single bet."""
    out = []
    for key in TICKET_ORDER:
        t = (pronostic.get("tickets") or {}).get(key) or {}
        if t.get("jambes"):
            out.append({"id": f"ticket:{key}", "type": "ticket", "quoi": t.get("titre", key), "jambes": t["jambes"],
                        "cote": t["cote_totale"], "proba": t["proba_totale"]})
    for leg in pronostic.get("top3") or []:
        out.append({"id": f"top3:{leg['match_id']}:{leg['pari']}", "type": "top3",
                    "quoi": f"{leg['pari']} ({leg['match']})", "jambes": [leg], "cote": leg["cote"],
                    "proba": leg["proba"]})
    for c in out:
        c["valeur"] = round(c["proba"] * c["cote"] - 1, 4)
        c["kelly"] = round(max(c["valeur"], 0) / (c["cote"] - 1), 4) if c["cote"] > 1 else 0.0
    return out


def _result(decision: dict, page: dict[str, dict]) -> dict:
    settled = _settle_ticket({"jambes": decision["jambes"]}, page)
    d = {**decision, "jambes": settled["jambes"], "resultat": None if settled.get("resultat") == "en_cours" else settled.get("resultat")}
    if d["joue"] and d["resultat"] in ("gagne", "perdu", "rembourse"):
        won = math.prod(l["cote"] for l in d["jambes"] if l.get("resultat") == "gagne")
        d["gain"] = round(-d["mise"] if d["resultat"] == "perdu" else d["mise"] * (won - 1), 2)
    return d


def replay(days: dict[str, dict], depart: float) -> tuple[float, float, list[dict]]:
    """The player's bankroll after each settled day: current amount, best level, history."""
    bank, pic, history = float(depart), float(depart), []
    for day in sorted(days):
        gains = [d.get("gain") for d in days[day].get("decisions") or [] if d.get("joue")]
        if any(g is not None for g in gains):
            bank = round(bank + sum(g for g in gains if g is not None), 2)
            pic = max(pic, bank)
            history.append({"jour": day, "bankroll": bank})
    return bank, pic, history


def effective_profile(choisi: str, bank: float, pic: float) -> tuple[str, str | None]:
    names = list(PROFILS)
    if choisi not in PROFILS:
        choisi = DEFAUT
    if bank < FREIN * pic and choisi != names[0]:
        lower = names[names.index(choisi) - 1]
        return lower, (f"Frein de sécurité : la bankroll ({_fr(bank)} €) est sous {_pct(FREIN, 0)} de son plus haut "
                       f"({_fr(pic)} €), je joue en {PROFILS[lower]['titre'].lower()} jusqu'à ce qu'elle remonte.")
    return choisi, None


def decide(pronostic: dict, previous: dict | None, page: dict[str, dict], bank_start: float, profil: str,
           now: datetime) -> list[dict]:
    """The day's decisions: those already under way kept as they were, the others (re)made now."""
    p = PROFILS[profil]
    old = {d["id"]: d for d in (previous or {}).get("decisions") or []}
    frozen = [d for d in old.values() if any(_started(l, page, now) for l in d["jambes"])]
    decisions = [_result({**d, "fige": True}, page) for d in frozen]
    budget = p["max_jour"] * bank_start - sum(d["mise"] for d in frozen if d["joue"])
    exposed = {l["match_id"] for d in frozen if d["joue"] for l in d["jambes"]}

    fresh = [c for c in candidates(pronostic) if c["id"] not in {d["id"] for d in frozen}
             and not any(_started(l, page, now) for l in c["jambes"])]
    fresh.sort(key=lambda c: -min(p["kelly"] * c["kelly"], p["max_pari"]) * c["valeur"])
    for c in fresh:
        stake = round(min(p["kelly"] * c["kelly"], p["max_pari"]) * bank_start, 2)
        matches = {l["match_id"] for l in c["jambes"]}
        why = None
        if not all(l.get("valide", True) for l in c["jambes"]):
            why = "Contient un pari « non validé » par le backtest : je ne mise pas dessus."
        elif c["valeur"] < p["valeur_min"]:
            why = (f"Valeur {_pct(c['valeur'])} sous mon minimum de {_pct(p['valeur_min'], 0)} : "
                   "l'avantage est trop mince pour couvrir l'erreur du modèle.")
        elif c["proba"] < p["proba_min"]:
            why = f"Chance de {_pct(c['proba'])} sous mon minimum de {_pct(p['proba_min'], 0)}."
        elif matches & exposed:
            why = "Je suis déjà engagé sur ce match : je ne double pas le risque."
        elif budget < MISE_MIN:
            why = f"Budget du jour atteint ({_pct(p['max_jour'], 0)} de la bankroll au plus)."
        else:
            stake = round(min(stake, budget), 2)
            if stake < MISE_MIN:
                why = "La mise utile serait inférieure à 10 centimes."
        d = {k: c[k] for k in ("id", "type", "quoi", "jambes", "cote", "proba", "valeur", "kelly")}
        if why:
            decisions.append({**d, "joue": False, "mise": 0.0, "mise_pct": 0.0, "raison": why})
            continue
        budget -= stake
        exposed |= matches
        decisions.append({**d, "joue": True, "mise": stake, "mise_pct": round(stake / bank_start, 4),
                          "raison": (f"Chance {_pct(c['proba'], 0)} pour une cote de {_fr(c['cote'])} : valeur "
                                     f"{_pct(c['valeur'])}. Kelly conseille {_pct(c['kelly'])} de la bankroll, "
                                     f"j'en mise {_pct(stake / bank_start)} (profil {p['titre'].lower()}).")})
    for gone in set(old) - {d["id"] for d in decisions}:
        if old[gone].get("joue"):
            decisions.append({**old[gone], "joue": False, "mise": 0.0, "mise_pct": 0.0,
                              "raison": "Proposition retirée par l'analyse (cotes ou compositions) avant le match."})
    return decisions


def _summary(decisions: list[dict]) -> str:
    played = [d for d in decisions if d["joue"]]
    if not played:
        return "Rien ne vaut le coup pour l'instant : je ne mise rien."
    total = sum(d["mise"] for d in played)
    return (f"Je joue {len(played)} pari(s) pour {_fr(total)} € au total : "
            + " ; ".join(f"{d['quoi']} {_fr(d['mise'])} €" for d in played) + ".")


def simulate(depart: float, objectif: float, profil: dict, scenario: str, paths: int = 2000,
             years: int = 3, seed: int = 7) -> dict:
    """Monte Carlo of the player's rule over `years`, for a market like the one it sees.

    Each day about two proposals arrive, at odds of 1.6 to 3.2, with an estimated value of 0 to 12 %.
    Scenario "raison": the model is right; "moitie": the real value is half the estimate; "aucun": no real
    advantage, the bookmaker's margin (about 4 %) wins.
    """
    rng = np.random.default_rng(seed)
    bank = np.full(paths, float(depart))
    days = 365 * years
    reached, ruined, after_1y = np.zeros(paths, bool), np.zeros(paths, bool), None
    for day in range(days):
        n = rng.poisson(2.0, paths)
        spent = np.zeros(paths)
        start = bank.copy()
        for j in range(4):
            odds = rng.choice([1.6, 1.9, 2.5, 3.2], paths)
            est = rng.uniform(0.0, 0.12, paths)
            proba = (1 + est) / odds
            true = {"raison": est, "moitie": est / 2, "aucun": np.full(paths, -0.04)}[scenario]
            kelly = est / (odds - 1)
            frac = np.minimum(profil["kelly"] * kelly, profil["max_pari"])
            frac = np.minimum(frac, profil["max_jour"] - spent)
            play = ((n > j) & (est >= profil["valeur_min"]) & (proba >= profil["proba_min"]) & (frac > 0)
                    & ~ruined & ~reached)
            stake = np.where(play, frac * start, 0.0)
            spent += np.where(play, frac, 0.0)
            win = rng.random(paths) < (1 + true) / odds
            bank = bank + np.where(win, stake * (odds - 1), -stake)
        ruined |= bank < 0.1 * depart
        reached |= bank >= objectif
        if day == 364:
            after_1y = np.median(bank)
    return {"mediane_1an": round(float(after_1y), 2), "mediane_fin": round(float(np.median(bank)), 2),
            "chance_objectif": round(float(reached.mean()), 4), "risque_ruine": round(float(ruined.mean()), 4)}


SCENARIOS = {"raison": "Le modèle a raison", "moitie": "Le modèle surestime de moitié",
             "aucun": "Aucun avantage réel"}


def simulation(depart: float, objectif: float) -> dict:
    profils = {}
    for key, p in PROFILS.items():
        profils[key] = {"titre": p["titre"], "regles": {k: p[k] for k in p if k != "titre"},
                        "scenarios": {s: simulate(depart, objectif, p, s) for s in SCENARIOS}}
    return {
        "depart": depart, "objectif": objectif, "annees": 3, "scenarios": SCENARIOS,
        "hypotheses": ("2 000 parcours de 3 ans. Environ deux propositions par jour, cotes de 1,6 à 3,2, valeur estimée "
                       "de 0 à 12 %. « Ruine » : passer sous 10 % du départ. Chance de l'objectif : l'atteindre en 3 ans."),
        "profils": profils, "recommande": RECOMMANDE,
        "lecon": ("Miser plus n'accélère pas : au-delà de la mise de Kelly, la bankroll grossit moins vite en moyenne et le "
                  "risque de tout perdre explose. Ce qui rapproche vraiment de l'objectif, c'est un avantage réel prouvé "
                  "et un montant de départ plus élevé, pas un pourcentage de mise plus fort."),
    }


def plan(bank: float, objectif: float, sim: dict, profil: str) -> dict:
    mult = objectif / bank if bank > 0 else float("inf")
    rythme = {f"{y}_an{'s' if y > 1 else ''}": round(mult ** (1 / (365 * y)) - 1, 5) for y in (1, 3, 5)}
    s = sim["profils"][profil]["scenarios"]
    phrases = [
        f"Il faut multiplier la bankroll par {_fr(mult, 0)} pour atteindre {_fr(objectif, 0)} €.",
        f"Soit +{_pct(rythme['1_an'], 2)} par jour, tous les jours, pendant 1 an ; +{_pct(rythme['3_ans'], 2)} par "
        f"jour pendant 3 ans. Un très bon parieur professionnel vise plutôt quelques % par mois.",
        f"Mon profil {PROFILS[profil]['titre'].lower()}, si le modèle a raison : bankroll médiane "
        f"{_fr(s['raison']['mediane_1an'], 0)} € dans 1 an, {_pct(s['raison']['chance_objectif'])} de chances "
        f"d'atteindre l'objectif en 3 ans, {_pct(s['raison']['risque_ruine'])} de risque de ruine.",
        f"Sans avantage réel : médiane {_fr(s['aucun']['mediane_1an'], 0)} € dans 1 an. C'est pourquoi je "
        "recommande le profil prudent tant que le modèle n'a pas prouvé qu'il bat les bookmakers.",
    ]
    return {"multiplicateur": round(mult, 1), "rythme_par_jour": rythme, "phrases": phrases}


def write(page_folder: str | None, out: str, pronostic: dict, page: dict[str, dict], settings: dict,
          now: datetime) -> dict:
    """Write out/joueur/<day>.json and out/joueur/etat.json from the page copy; returns the day's document.

    The page copy must hold the `joueur` collection (earlier days, etat) and reglages/joueur.
    """
    import json
    from pathlib import Path

    folder = Path(page_folder or "/nonexistent")
    read = lambda p: json.loads(p.read_text()) if p.exists() else None
    past = {p.stem: json.loads(p.read_text()) for p in (folder / "joueur").glob("*.json") if p.stem != "etat"}
    doc, etat = run(pronostic, past.get(pronostic["jour"]), past, page, settings,
                    read(folder / "reglages" / "joueur.json"), read(folder / "joueur" / "etat.json"), now)
    (Path(out) / "joueur").mkdir(parents=True, exist_ok=True)
    for name, body in ((pronostic["jour"], doc), ("etat", etat)):
        (Path(out) / "joueur" / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc


def ticket_stakes(pronostic: dict, day: dict) -> dict:
    """Ticket stakes of "Mon pronostic" as the player decided them (0 when it passes)."""
    by_id = {d["id"]: d for d in day["decisions"]}
    for key, t in (pronostic.get("tickets") or {}).items():
        d = by_id.get(f"ticket:{key}")
        if t.get("jambes") and d:
            t["mise"], t["mise_pct"], t["joue"] = d["mise"], d["mise_pct"], d["joue"]
    pronostic["bankroll_du_jour"] = day["bankroll_du_jour"]
    return pronostic


def run(pronostic: dict, previous_day: dict | None, past_days: dict[str, dict], page: dict[str, dict],
        settings: dict, choix: dict | None, previous_etat: dict | None, now: datetime) -> tuple[dict, dict]:
    """The day's joueur/<day> and joueur/etat documents.

    past_days: the joueur documents of the earlier days; settings: reglages/bankroll (depart, objectif);
    choix: reglages/joueur ({profil}).
    """
    day = pronostic["jour"]
    depart, objectif = float(settings["depart"]), float(settings["objectif"])
    earlier = {d: v for d, v in past_days.items() if d < day}
    bank_start, pic, _ = replay(earlier, depart)
    choisi = (choix or {}).get("profil", DEFAUT)
    profil, frein = effective_profile(choisi, bank_start, pic)
    decisions = decide(pronostic, previous_day, page, bank_start, profil, now)

    journal = list((previous_day or {}).get("journal") or [])
    resume = _summary(decisions)
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    if frein and not any(e["texte"] == frein for e in journal):
        journal.append({"heure": heure, "texte": frein})
    if not journal or (previous_day or {}).get("resume") != resume:
        journal.append({"heure": heure, "texte": resume})
    old = {d["id"]: d for d in (previous_day or {}).get("decisions") or []}
    for d in decisions:
        if d.get("gain") is not None and old.get(d["id"], {}).get("gain") is None:
            word = {"gagne": "gagné", "perdu": "perdu", "rembourse": "remboursé"}[d["resultat"]]
            journal.append({"heure": heure, "texte": f"{d['quoi']} {word} : {'+' if d['gain'] >= 0 else ''}"
                                                     f"{_fr(d['gain'])} €."})

    doc = {"jour": day, "maj": now.isoformat(), "profil": profil, "profil_choisi": choisi,
           "bankroll_du_jour": round(bank_start, 2), "budget_max": round(PROFILS[profil]["max_jour"] * bank_start, 2),
           "decisions": decisions, "resume": resume, "journal": journal[-40:]}

    bank, pic, history = replay({**earlier, day: doc}, depart)
    prev_sim = (previous_etat or {}).get("simulation") or {}
    sim = (prev_sim if (prev_sim.get("depart"), prev_sim.get("objectif")) == (depart, objectif)
           and set(prev_sim.get("profils") or {}) == set(PROFILS) else simulation(depart, objectif))
    all_days = {**earlier, day: doc}
    etat = {
        "maj": now.isoformat(), "profil": profil, "profil_choisi": choisi, "recommande": RECOMMANDE,
        "profils_dispo": {k: {"titre": v["titre"], **{x: v[x] for x in v if x != "titre"}} for k, v in PROFILS.items()},
        "bankroll": {"depart": depart, "objectif": objectif, "actuelle": bank, "pic": pic,
                     "progression": round(bank / objectif, 6), "historique": history},
        "plan": plan(bank, objectif, sim, profil), "simulation": sim,
        "derniers_jours": [{"jour": d, "resume": all_days[d].get("resume"),
                            "mises": round(sum(x["mise"] for x in all_days[d]["decisions"] if x["joue"]), 2),
                            "gain": round(sum(x.get("gain") or 0 for x in all_days[d]["decisions"] if x["joue"]), 2)}
                           for d in sorted(all_days, reverse=True)[:14]],
        "avertissement": AVERTISSEMENT,
    }
    return doc, etat
