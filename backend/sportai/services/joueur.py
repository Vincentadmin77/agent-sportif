"""The professional player: decides which of the day's proposals to play and how much to stake.

It works on the page's virtual bankroll only and never places a real bet. Each pass it reads the day's
"Mon pronostic" document (the tickets and the three best odds), keeps what is already under way, and for
the rest stakes a capped fraction of the Kelly stake (the stake that grows a bankroll fastest when the
probabilities are right), by the profile chosen on the page (reglages/joueur). It passes when nothing is
worth it, plays the markets the backtest has not validated (anytime goalscorer included) only on trial, and takes one
position per match at most before kick-off. During the matches (live) it may add one live bet per match, on trial
too (LIVE_RULE).

Documents written for the page:
- joueur/<day>: the day's decisions (played or passed, and why), the stakes, the results, a journal.
- joueur/etat: the profile, the player's bankroll and its history, the plan towards the target, and a
  simulation of the three profiles showing what bigger stakes buy (speed) and cost (risk of ruin).
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np

from sportai.engine.football.scorers import live_scorer_proba
from sportai.services.buteurs import player_state, settle_scorer
from sportai.services.prematch import LOCAL_TZ
from sportai.services.dashboard import NON_VALIDE, settle
from sportai.services.pronostic import _settle_ticket, agrees, cautious_proba

# Probabilities: each bet is sized on the model's chance capped at the divergence limit above the market's
# (pronostic.cautious_proba), never on a model far more confident than the bookmakers alone.
# Fraction of the Kelly stake (on schedule, and at most when far behind the deadline), most of the bankroll on one
# bet and on one day, least value and chance to play. Never above the full Kelly stake: beyond it the bankroll
# grows more slowly on average and the risk of ruin soars.
PROFILS = {
    "prudent": {"titre": "Prudent", "kelly": 0.125, "kelly_max": 0.25, "max_pari": 0.02, "max_jour": 0.05,
                "valeur_min": 0.03, "proba_min": 0.40},
    "equilibre": {"titre": "Équilibré", "kelly": 0.25, "kelly_max": 0.5, "max_pari": 0.05, "max_jour": 0.10,
                  "valeur_min": 0.02, "proba_min": 0.30},
    "agressif": {"titre": "Agressif", "kelly": 0.5, "kelly_max": 1.0, "max_pari": 0.10, "max_jour": 0.20,
                 "valeur_min": 0.01, "proba_min": 0.20},
}
# The owner's deadline (2026-10-11): this season only, 100 000 by the end of May 2027.
DEBUT, ECHEANCE = date(2026, 10, 11), date(2027, 5, 31)
RETARD_PLEIN = 0.5  # half the target path behind: stakes at the profile's most
# For comparison only, never played: twice the Kelly stake.
DOUBLE_KELLY = {"titre": "Deux fois Kelly (comparaison, jamais joué)", "kelly": 2.0, "kelly_max": 2.0, "max_pari": 0.5,
                "max_jour": 0.8, "valeur_min": 0.01, "proba_min": 0.20}
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


def candidates(pronostic: dict, legs: list[dict] | None = None) -> list[dict]:
    """What the player may play today: each ticket, each of the three best odds as a single bet, and every other
    validated bet the model priced today with a positive value (`legs`, from pronostic.legs_from_db), as a single.

    The singles matter: on 2026-10-12 Atalanta at 1.49 (model 74 %, value +9.7 %) left the safe ticket when its odds
    fell under the ticket's 1.50 floor, and the player, which only saw the tickets and the top 3, never saw it again."""
    out = []
    for key in TICKET_ORDER:
        t = (pronostic.get("tickets") or {}).get(key) or {}
        if t.get("jambes"):
            out.append({"id": f"ticket:{key}", "type": "ticket", "quoi": t.get("titre", key), "jambes": t["jambes"],
                        "cote": t["cote_totale"], "proba": round(math.prod(cautious_proba(l) for l in t["jambes"]), 4)})
    for leg in pronostic.get("top3") or []:
        out.append({"id": f"top3:{leg['match_id']}:{leg['pari']}", "type": "top3",
                    "quoi": f"{leg['pari']} ({leg['match']})", "jambes": [leg], "cote": leg["cote"],
                    "proba": cautious_proba(leg)})
    seen = {(l["match_id"], l["pari"]) for c in out if c["type"] == "top3" for l in c["jambes"]}
    for leg in legs or []:
        if (leg["match_id"], leg["pari"]) in seen or not _leg_ok(leg):
            continue
        seen.add((leg["match_id"], leg["pari"]))
        clean = {k: v for k, v in leg.items() if not k.startswith("_")}
        out.append({"id": f"simple:{leg['match_id']}:{leg['pari']}", "type": "simple",
                    "quoi": f"{leg['pari']} ({leg['match']})", "jambes": [clean], "cote": leg["cote"],
                    "proba": cautious_proba(leg)})
    for c in out:
        c["valeur"] = round(c["proba"] * c["cote"] - 1, 4)
        c["kelly"] = round(max(c["valeur"], 0) / (c["cote"] - 1), 4) if c["cote"] > 1 else 0.0
    return out


UCL_RULE = ("Coupes d'Europe : mes paris passés sur ces compétitions perdent trop, je ne mise plus, je les note "
            "pour juger plus tard s'ils auraient payé.")
# Champions League (owner 2026-10-14: the player must stake on it too), in singles and in the tickets that hold one. The cross-league model is calibrated
# (runs.UCL_CALIBRATION) but has no validated record against the odds yet, so the player stakes half of what it
# would elsewhere, asks one more point of value, and stops (back to paper) as soon as its own record there turns bad.
UCL_MISE = 0.5
UCL_MARGE = 0.01
UCL_ARRET = (20, -0.10)  # 20 settled Champions League bets losing more than 10 % per euro: back to paper
# Any odds the model prices (owner 2026-10-14: "n'importe quelles cotes"): a market the backtest has not validated
# (both teams score, over/under 2.5, a Champions League draw...) is played on trial like the Champions League: half
# the stake, one more point of value, its own record per market, and back to paper when that record loses.
ESSAI_RULE = "{nom} : mes paris passés sur ce marché perdent trop, je ne mise plus, je les note pour juger plus tard."


def _leg_ok(leg: dict) -> bool:
    """A single the player may consider: value >= 0 (validated market or on trial), and the model within the limit of
    the market (a bigger gap usually means information the model lacks), or above it on a market favourite, then
    sized on the model's chance capped near the market's (pronostic.cautious_proba): Liverpool at LASK on 2026-10-14,
    84 % for the model and 69 % for the market, was left out by the old limit and won 6-3 (owner, 2026-10-15)."""
    return leg.get("valeur", -1) >= 0 and agrees(leg)


def _ucl(d: dict) -> bool:
    return any(l.get("ucl") for l in d["jambes"])


def _essais(d: dict) -> set[str]:
    """The trial groups a bet belongs to: the Champions League, each market the backtest has not validated, and every
    bet taken during a match ("Direct", whatever its market: the in-play model is its own trial)."""
    return {DIRECT if l.get("en_direct") else "ucl" if l.get("ucl") else l.get("marche") or l.get("pari")
            for l in d["jambes"] if l.get("en_direct") or l.get("ucl") or not l.get("valide", True)}


def _essai_nom(g: str) -> str:
    return "Coupes d'Europe" if g == "ucl" else g  # Champions League and, since 2026-10-15, Europa League


def _kind(d: dict) -> str:
    """Learning groups: combined tickets, single bets (top 3 or any other single), Champions League bets, and each
    family of markets on trial on its own ("essai:Buteur"...): what goal bets taught says nothing about scorers."""
    if _ucl(d):
        return "ucl"
    e = sorted(_essais(d))
    return f"essai:{e[0]}" if len(e) == 1 else "essai" if e else "ticket" if d["type"] == "ticket" else "top3"


def _corrige(p: float, r: float) -> float:
    """The chance corrected by the record r (wins seen / wins announced), applied to the odds p / (1 - p) rather than
    to p itself: 0.8 x 82 % would make a clear favourite (Rennes on 2026-10-15) a 65 % bet, when such a record mostly
    says the long shots were overrated."""
    return round(min(p * r / (p * r + 1 - p), 0.99), 4) if 0 < p < 1 else p


# A bet on trial (a market the backtest has not validated) is only a "confiance" when the bookmakers also make it more
# likely than not (docs/13-lecons.md, rule 1): on 2026-10-15 the player's one bet was Dion Beljo to score at 2.25
# (model 46 %, bookmakers 44 %) while it left every favourite around 1.50.
ESSAI_MARCHE_MIN = 0.50


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


def pace(bank: float, depart: float, objectif: float, today: date, debut: date = DEBUT,
         echeance: date = ECHEANCE) -> dict:
    """Where the bankroll stands against a steady path from the start to the target on the deadline."""
    total, done = max((echeance - debut).days, 1), min(max((today - debut).days, 0), (echeance - debut).days)
    left = max((echeance - today).days, 0)
    cible = depart * (objectif / depart) ** (done / total)
    ecart = bank / cible - 1
    need = (objectif / bank) ** (1 / left) - 1 if left and bank > 0 else None
    return {"debut": f"{debut}", "echeance": f"{echeance}", "jours_restants": left, "cible_aujourdhui": round(cible, 2),
            "ecart": round(ecart, 4), "rythme_jour_necessaire": None if need is None else round(need, 5),
            "statut": "en_avance" if ecart > 0.05 else "en_retard" if ecart < -0.05 else "dans_les_temps"}


def kelly_fraction(p: dict, ecart: float, frein: bool) -> float:
    """The profile's Kelly fraction, raised towards its ceiling as the bankroll falls behind the deadline path.
    Never raised while the safety brake is on."""
    if frein:
        return p["kelly"]
    behind = min(max(-ecart, 0.0) / RETARD_PLEIN, 1.0)
    return round(p["kelly"] + (p["kelly_max"] - p["kelly"]) * behind, 4)


def decide(pronostic: dict, previous: dict | None, page: dict[str, dict], bank_start: float, profil: str,
           now: datetime, kelly: float | None = None, ajust: dict | None = None,
           legs: list[dict] | None = None) -> list[dict]:
    """The day's decisions: those already under way kept as they were, the others (re)made now.

    ajust (from `learn`): the correction of the model's chances by kind of bet ("ratio") and the least value to
    play ("valeur_min"), both learnt from the player's own results."""
    p = dict(PROFILS[profil])
    accel = kelly is not None and kelly > p["kelly"]
    if kelly is not None:
        p["kelly"] = kelly
    ajust = ajust or {}
    if ajust.get("valeur_min") is not None:
        p["valeur_min"] = ajust["valeur_min"]
    old = {d["id"]: d for d in (previous or {}).get("decisions") or []}
    frozen = [d for d in old.values() if any(_started(l, page, now) for l in d["jambes"])]
    decisions = [_result({**d, "fige": True}, page) for d in frozen]
    budget = p["max_jour"] * bank_start - sum(d["mise"] for d in frozen if d["joue"])
    exposed = {l["match_id"] for d in frozen if d["joue"] for l in d["jambes"]}

    fresh = [c for c in candidates(pronostic, legs) if c["id"] not in {d["id"] for d in frozen}
             and not any(_started(l, page, now) for l in c["jambes"])]
    w = (ajust.get("confiance") or {}).get("poids_modele", 1.0)
    for c in fresh:  # the model's chance corrected by what the player has seen so far
        if w < 1.0:  # mixed with the bookmakers' as much as the record says they saw better
            c["proba"] = round(math.prod(_trust(l, w) for l in c["jambes"]), 4)
            c["valeur"] = round(c["proba"] * c["cote"] - 1, 4)
        r = (ajust.get("ratio") or {}).get(_kind(c), 1.0)
        # The odds watched pass after pass, up to kick-off.
        hist = list((old.get(c["id"]) or {}).get("cotes") or [])
        if not hist or hist[-1]["cote"] != c["cote"]:
            hist.append({"heure": now.isoformat(), "cote": c["cote"]})
        c["cotes"] = hist[-12:]
        c["proba_modele"], c["valeur_modele"] = c["proba"], c["valeur"]
        c["proba"] = _corrige(c["proba"], r)
        c["valeur"] = round(c["proba"] * c["cote"] - 1, 4)
        c["kelly"] = round(max(c["valeur"], 0) / (c["cote"] - 1), 4) if c["cote"] > 1 else 0.0
        c["correction"] = r
    def share(c: dict) -> float:  # part of the bankroll on this bet, halved on trial (Champions League, unvalidated)
        f = UCL_MISE if _essais(c) else 1.0
        return min(p["kelly"] * f * c["kelly"], p["max_pari"] * f)

    fresh.sort(key=lambda c: -share(c) * c["valeur"])
    for c in fresh:
        stake = round(share(c) * bank_start, 2)
        matches = {l["match_id"] for l in c["jambes"]}
        essais = _essais(c)
        stopped = sorted(essais & set(ajust.get("essais_arret") or ()))
        if ajust.get("ucl_arret") and "ucl" in essais and "ucl" not in stopped:
            stopped.append("ucl")
        vmin = p["valeur_min"] + (UCL_MARGE if essais else 0.0)
        why, motif = None, None
        if stopped:
            why, motif = (UCL_RULE if stopped == ["ucl"] else ESSAI_RULE.format(nom=_essai_nom(stopped[0]))), "non_valide"
        elif essais - {"ucl"} and any((l.get("proba_marche") or 1 / l["cote"]) < ESSAI_MARCHE_MIN for l in c["jambes"]
                                      if not l.get("valide", True)):
            why, motif = ("Pari à l'essai que les bookmakers donnent à moins de 50 % : ce n'est pas une vraie confiance, "
                          "je ne le joue pas."), "proba"
        elif c["valeur"] < vmin:
            why, motif = (f"Valeur {_pct(c['valeur'])} sous mon minimum de {_pct(vmin, 1)}"
                          + (" (un point de plus sur un pari à l'essai)" if essais else "")
                          + " : l'avantage est trop mince pour couvrir l'erreur du modèle."), "valeur"
        elif c["proba"] < p["proba_min"]:
            why, motif = f"Chance de {_pct(c['proba'])} sous mon minimum de {_pct(p['proba_min'], 0)}.", "proba"
        elif matches & exposed:
            why, motif = "Je suis déjà engagé sur ce match : je ne double pas le risque.", "expose"
        elif budget < MISE_MIN:
            why, motif = f"Budget du jour atteint ({_pct(p['max_jour'], 0)} de la bankroll au plus).", "budget"
        else:
            stake = round(min(stake, budget), 2)
            if stake < MISE_MIN:
                why, motif = "La mise utile serait inférieure à 10 centimes.", "mise_min"
        if why and c["correction"] != 1.0 and motif in ("valeur", "proba"):
            why += f" (chance du modèle {_pct(c['proba_modele'])}, corrigée d'après mes résultats passés)"
        d = {k: c[k] for k in ("id", "type", "quoi", "jambes", "cote", "proba", "valeur", "kelly", "proba_modele",
                               "valeur_modele", "correction", "cotes")}
        if why:
            decisions.append({**d, "joue": False, "mise": 0.0, "mise_pct": 0.0, "raison": why, "motif": motif})
            continue
        budget -= stake
        exposed |= matches
        decisions.append({**d, "joue": True, "mise": stake, "mise_pct": round(stake / bank_start, 4),
                          "raison": (f"Chance {_pct(c['proba'], 0)} pour une cote de {_fr(c['cote'])} : valeur "
                                     f"{_pct(c['valeur'])}. Kelly conseille {_pct(c['kelly'])} de la bankroll, "
                                     f"j'en mise {_pct(stake / bank_start)} (profil {p['titre'].lower()}"
                                     + (", mises relevées pour l'échéance" if accel else "")
                                     + (", mise divisée par deux : " + ", ".join(_essai_nom(g) for g in sorted(essais))
                                        + " à l'essai, pas encore éprouvé face aux cotes" if essais else "") + ").")})
    for gone in set(old) - {d["id"] for d in decisions}:
        if old[gone].get("joue"):
            decisions.append({**old[gone], "joue": False, "mise": 0.0, "mise_pct": 0.0, "motif": "retire",
                              "raison": "Proposition retirée par l'analyse (cotes ou compositions) avant le match."})
    return decisions


# What the player learns from its own record (owner's request 2026-10-12: fully autonomous, finds and corrects its
# own mistakes). Every figure is recomputed from the settled decisions of the earlier days, nothing else is stored.
PRIOR_GAINS = 10.0  # the model is trusted as if it had already been right on 10 expected wins
SEUIL_PREUVES = {"equilibre": 30, "agressif": 100}  # settled bets needed before the player raises its own profile
VALEUR_MIN_BORNES = (0.01, 0.06)


MOTIFS = {"valeur": "valeur trop faible", "proba": "chance trop faible", "non_valide": "pari non validé",
          "expose": "déjà engagé sur ce match", "budget": "budget du jour atteint", "mise_min": "mise trop petite",
          "retire": "retiré avant le match"}


def _motif(d: dict) -> str | None:
    """Why a decision was passed (inferred from its text for the decisions written before the code existed)."""
    if d.get("joue"):
        return None
    if d.get("motif"):
        return d["motif"]
    r = d.get("raison") or ""
    for key, start in (("non_valide", "Contient un pari"), ("valeur", "Valeur "), ("proba", "Chance de "),
                       ("expose", "Je suis déjà"), ("budget", "Budget"), ("mise_min", "La mise utile"),
                       ("retire", "Proposition retirée")):
        if r.startswith(start):
            return key
    return None


# Whom to believe when the model and the bookmakers disagree (owner 2026-10-15: learn from the errors of the evening
# before). Each settled bet with a market price is a test: which of the model's chance, the market's, or a mix,
# would have announced its result best (log loss). Thin evidence moves the player only part of the way.
CONFIANCE_GRILLE = (0.0, 0.25, 0.5, 0.75, 1.0)
CONFIANCE_PREUVES = 30


def _trust(leg: dict, w: float) -> float:
    """The leg's chance with weight w on the model (cautious) and 1 - w on the market."""
    p, pm = cautious_proba(leg), leg.get("proba_marche")
    return p if pm is None else w * p + (1 - w) * pm


def _confiance(settled: list[dict]) -> dict:
    tests = {}
    for d in settled:
        for l in d["jambes"]:
            if l.get("proba_marche") is not None and l.get("resultat") in ("gagne", "perdu"):
                tests[(l["match_id"], l["pari"])] = (cautious_proba(l), l["proba_marche"], l["resultat"] == "gagne")
    n = len(tests)
    if not n:
        return {"poids_modele": 1.0, "n": 0, "raison": None}

    def loss(w: float) -> float:
        return sum(-math.log(min(max(w * p + (1 - w) * m if y else 1 - (w * p + (1 - w) * m), 1e-6), 1))
                   for p, m, y in tests.values())
    best = min(CONFIANCE_GRILLE, key=loss)
    w = round(1 - (1 - best) * min(n / CONFIANCE_PREUVES, 1), 3)
    if w >= 0.99:
        why = f"Sur {n} pari(s) réglé(s), le modèle a vu au moins aussi juste que les bookmakers : je garde sa chance."
    else:
        why = (f"Sur {n} pari(s) réglé(s), les bookmakers ont vu plus juste que le modèle quand ils n'étaient pas "
               f"d'accord : je ne crois le modèle qu'à {_pct(w, 0)} et les bookmakers pour le reste"
               + (f" (il faudra {CONFIANCE_PREUVES} paris pour aller jusqu'au bout de cette leçon)" if n < CONFIANCE_PREUVES else "")
               + ".")
    return {"poids_modele": w, "n": n, "meilleur": best, "raison": why}


def _lecon(d: dict) -> str:
    """Why a lost bet was lost, as far as the numbers tell: whom the player believed."""
    parts = []
    for l in d["jambes"]:
        pm = l.get("proba_marche")
        if l.get("resultat") == "perdu" and pm is not None:
            parts.append(f"{l['pari']} : modèle {_pct(l['proba'], 0)}, bookmakers {_pct(pm, 0)}"
                         + (", j'ai suivi le plus optimiste" if l["proba"] > pm + 0.03 else ""))
    return "; ".join(parts)


def _unit_return(d: dict) -> float | None:
    """Gain for 1 € staked on a settled decision (played or not)."""
    if d.get("resultat") not in ("gagne", "perdu", "rembourse"):
        return None
    if d["resultat"] == "perdu":
        return -1.0
    return math.prod(l["cote"] for l in d["jambes"] if l.get("resultat") == "gagne") - 1


def _stats(items: list[dict]) -> dict:
    rets = [_unit_return(d) for d in items]
    n = len(rets)
    wins = sum(d["resultat"] == "gagne" for d in items)
    expected = sum(d.get("proba_modele", d["proba"]) for d in items)
    roi = sum(rets) / n if n else None
    sd = (sum((r - roi) ** 2 for r in rets) / (n - 1)) ** 0.5 if n > 1 else None
    return {"n": n, "gagnes": wins, "attendus": round(expected, 2), "roi": None if roi is None else round(roi, 4),
            "roi_bas": None if sd is None else round(roi - 1.28 * sd / n ** 0.5, 4)}


def learn(past_days: dict[str, dict], plafond: str = "agressif") -> dict:
    """The player's review of its own record: hit rate against what the model announced, return per 1 € staked,
    what the bets it passed would have given, and the corrections and profile it draws from them."""
    settled = [(day, d) for day in sorted(past_days) for d in past_days[day].get("decisions") or []
               if _unit_return(d) is not None]
    played = [d for _, d in settled if d["joue"]]
    joues = _stats(played)
    # The model's record is judged on every validated proposal it priced, played or not: a bet passed is evidence
    # too, and the player learns three times faster than from its own stakes alone.
    # Champions League bets have their own record (below), kept out of the one that sets the profile.
    g = _stats([d for _, d in settled if _motif(d) != "non_valide" and not _essais(d)])
    # Correction of the model's chances: wins seen against wins announced, pulled towards 1 while evidence is thin.
    ratio = {}
    for kind in ("ticket", "top3", "ucl", "essai", *sorted({_kind(d) for _, d in settled} - {"ticket", "top3", "ucl", "essai"})):
        items = [d for _, d in settled if _kind(d) == kind and (kind.startswith(("ucl", "essai")) or _motif(d) != "non_valide")]
        st = _stats(items)
        ratio[kind] = round(min(max((PRIOR_GAINS + st["gagnes"]) / (PRIOR_GAINS + st["attendus"]), 0.6), 1.3), 3)
    # Least value: lowered if the bets passed for too thin a value would have paid, raised if those played lose.
    passed = _stats([d for _, d in settled if not d["joue"] and _motif(d) == "valeur" and not _essais(d)])
    base = PROFILS["prudent"]["valeur_min"]
    valeur_min, why_min = None, None
    if passed["n"] >= 20 and passed["roi"] > 0:
        valeur_min, why_min = base - 0.01, (f"Les {passed['n']} paris passés pour valeur trop faible auraient rapporté "
                                            f"{_pct(passed['roi'])} par euro : je baisse mon minimum de valeur.")
    elif joues["n"] >= 20 and joues["roi"] < -0.10:
        valeur_min, why_min = base + 0.01, (f"Mes {joues['n']} paris joués perdent {_pct(-joues['roi'])} par euro : je "
                                            "deviens plus exigeant sur la valeur.")
    if valeur_min is not None:
        valeur_min = round(min(max(valeur_min, VALEUR_MIN_BORNES[0]), VALEUR_MIN_BORNES[1]), 4)
    # Profile chosen by the player from its evidence, never above the owner's ceiling.
    order = list(PROFILS)
    if g["n"] >= SEUIL_PREUVES["agressif"] and g["roi_bas"] is not None and g["roi_bas"] > 0:
        auto, why = "agressif", f"{g['n']} propositions réglées et un gain par euro sûrement positif ({_pct(g['roi'])})."
    elif g["n"] >= SEUIL_PREUVES["equilibre"] and g["roi"] > 0:
        auto, why = "equilibre", (f"{g['n']} propositions réglées, gain par euro positif ({_pct(g['roi'])}), pas encore "
                                  "prouvé.")
    elif g["n"] < SEUIL_PREUVES["equilibre"]:
        auto, why = "prudent", (f"Seulement {g['n']} proposition(s) réglée(s), jouées ou passées : il en faut "
                                f"{SEUIL_PREUVES['equilibre']} avant que je prenne plus de risque.")
    else:
        auto, why = "prudent", (f"{g['n']} propositions réglées mais elles perdent {_pct(-g['roi'])} par euro : je reste "
                                "prudent.")
    if plafond in order and order.index(auto) > order.index(plafond):
        auto, why = plafond, why + f" Plafond fixé par Vincent : {PROFILS[plafond]['titre'].lower()}."
    exams = []
    for day in sorted(past_days)[-14:]:
        ds = [d for d in past_days[day].get("decisions") or [] if _unit_return(d) is not None]
        if not ds:
            continue
        j = [d for d in ds if d["joue"]]
        missed = [d for d in ds if not d["joue"] and d["resultat"] == "gagne"]
        dodged = [d for d in ds if not d["joue"] and d["resultat"] == "perdu"]
        text = (f"{len(j)} pari(s) joué(s), {sum(d['resultat'] == 'gagne' for d in j)} gagné(s), "
                f"{'+' if sum(d.get('gain') or 0 for d in j) >= 0 else ''}{_fr(sum(d.get('gain') or 0 for d in j))} €. ")
        lost = [d for d in j if d["resultat"] == "perdu" and _lecon(d)]
        if lost:
            text += "Perdus : " + " | ".join(f"{d['quoi']} ({_lecon(d)})" for d in lost) + ". "
        if missed:
            text += "Passés mais gagnés : " + ", ".join(f"{d['quoi']} ({MOTIFS.get(_motif(d), 'passé')})" for d in missed) + ". "
        if dodged:
            text += f"{len(dodged)} pari(s) évité(s) à raison. "
        exams.append({"jour": day, "texte": text.strip()})
    # Champions League, played or passed: the player stops staking there once its record clearly loses.
    ucl = _stats([d for _, d in settled if _ucl(d)])
    arret = ucl["n"] >= UCL_ARRET[0] and ucl["roi"] < UCL_ARRET[1]
    ucl["mise"] = "papier" if arret else "reduite"
    ucl["raison"] = (f"Mes {ucl['n']} paris de coupes d'Europe perdent {_pct(-ucl['roi'])} par euro : je ne mise "
                     "plus dessus, je les note." if arret else
                     f"Je mise en coupes d'Europe avec des mises divisées par deux ; j'arrête si, après "
                     f"{UCL_ARRET[0]} paris réglés, ils perdent plus de {_pct(-UCL_ARRET[1], 0)} par euro "
                     f"(aujourd'hui {ucl['n']} réglé(s)).")
    # Every other market on trial, played or passed, with the same rule.
    essais = {}
    for grp in sorted({x for _, d in settled for x in _essais(d)} - {"ucl"}):
        st = _stats([d for _, d in settled if grp in _essais(d)])
        stop = st["n"] >= UCL_ARRET[0] and st["roi"] < UCL_ARRET[1]
        essais[grp] = {**st, "mise": "papier" if stop else "reduite", "raison": (
            f"{grp} : mes {st['n']} paris perdent {_pct(-st['roi'])} par euro, je ne mise plus dessus." if stop else
            f"{grp} : à l'essai, mises divisées par deux ({st['n']} réglé(s) ; j'arrête après {UCL_ARRET[0]} s'ils "
            f"perdent plus de {_pct(-UCL_ARRET[1], 0)} par euro).")}
    return {"joues": joues, "propositions": g, "passes_valeur": passed, "ucl_sur_papier": ucl, "ucl_arret": arret,
            "confiance": _confiance([d for _, d in settled]),
            "essais": essais, "essais_arret": [k for k, v in essais.items() if v["mise"] == "papier"],
            "correction": ratio, "valeur_min": valeur_min,
            "valeur_min_raison": why_min, "profil_auto": auto, "profil_raison": why, "plafond": plafond,
            "examens": exams[::-1]}


def _summary(decisions: list[dict]) -> str:
    played = [d for d in decisions if d["joue"]]
    if not played:
        return "Rien ne vaut le coup pour l'instant : je ne mise rien."
    total = sum(d["mise"] for d in played)
    return (f"Je joue {len(played)} pari(s) pour {_fr(total)} € au total : "
            + " ; ".join(f"{d['quoi']} {_fr(d['mise'])} €" for d in played) + ".")


def simulate(depart: float, objectif: float, profil: dict, scenario: str, paths: int = 2000,
             days: int = 365 * 3, seed: int = 7) -> dict:
    """Monte Carlo of the player's rule over `days`, stakes at the profile's ceiling (the player is behind the
    deadline path almost at once), for a market like the one it sees.

    Each day about two proposals arrive, at odds of 1.6 to 3.2, with an estimated value of 0 to 12 %.
    Scenario "raison": the model is right; "moitie": the real value is half the estimate; "aucun": no real
    advantage, the bookmaker's margin (about 4 %) wins.
    """
    rng = np.random.default_rng(seed)
    bank = np.full(paths, float(depart))
    reached, ruined = np.zeros(paths, bool), np.zeros(paths, bool)
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
            frac = np.minimum(profil["kelly_max"] * kelly, profil["max_pari"])
            frac = np.minimum(frac, profil["max_jour"] - spent)
            play = ((n > j) & (est >= profil["valeur_min"]) & (proba >= profil["proba_min"]) & (frac > 0)
                    & ~ruined & ~reached)
            stake = np.where(play, frac * start, 0.0)
            spent += np.where(play, frac, 0.0)
            win = rng.random(paths) < (1 + true) / odds
            bank = bank + np.where(win, stake * (odds - 1), -stake)
        ruined |= bank < 0.1 * depart
        reached |= bank >= objectif
    med = round(float(np.median(bank)), 2)
    # mediane_1an kept for the page until it reads mediane_echeance: both are the median on the deadline.
    return {"mediane_echeance": med, "mediane_1an": med, "mediane_fin": med,
            "chance_objectif": round(float(reached.mean()), 4), "risque_ruine": round(float(ruined.mean()), 4)}


SCENARIOS = {"raison": "Le modèle a raison", "moitie": "Le modèle surestime de moitié",
             "aucun": "Aucun avantage réel"}


def simulation(depart: float, objectif: float, jours: int, echeance: str = f"{ECHEANCE}") -> dict:
    """From the bankroll as it is now to the deadline. `depart` here is the current bankroll."""
    jours = max(jours, 1)
    profils = {}
    for key, p in PROFILS.items():
        profils[key] = {"titre": p["titre"], "regles": {k: p[k] for k in p if k != "titre"},
                        "scenarios": {s: simulate(depart, objectif, p, s, days=jours) for s in SCENARIOS}}
    return {
        "depart": depart, "objectif": objectif, "jours": jours, "echeance": echeance, "scenarios": SCENARIOS,
        "hypotheses": (f"2 000 parcours de la bankroll actuelle jusqu'au {_date_fr(echeance)} ({jours} jours). Environ "
                       "deux propositions par jour, cotes de 1,6 à 3,2, valeur estimée de 0 à 12 %. Mises au plus haut "
                       "du profil, puisque le joueur est en retard sur l'échéance. « Ruine » : passer sous 10 % de la "
                       "bankroll actuelle. Chance de l'objectif : l'atteindre avant l'échéance."),
        "profils": profils, "recommande": RECOMMANDE,
        "comparaison": {"titre": DOUBLE_KELLY["titre"],
                        "scenarios": {s: simulate(depart, objectif, DOUBLE_KELLY, s, days=jours) for s in SCENARIOS}},
        "lecon": ("Miser plus n'accélère pas : au-delà de la mise de Kelly, la bankroll grossit moins vite en moyenne et le "
                  "risque de tout perdre explose. Ce qui rapproche vraiment de l'objectif, c'est un avantage réel prouvé "
                  "et un montant de départ plus élevé, pas un pourcentage de mise plus fort."),
    }


MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
        "décembre")


def _date_fr(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def plan(bank: float, objectif: float, sim: dict, profil: str, rythme: dict, kelly: float) -> dict:
    mult = objectif / bank if bank > 0 else float("inf")
    s, c = sim["profils"][profil]["scenarios"], sim["comparaison"]["scenarios"]
    when = _date_fr(rythme["echeance"])
    ecart = rythme["ecart"]
    where = (f"J'ai {_pct(ecart, 0)} d'avance" if rythme["statut"] == "en_avance" else
             f"J'ai {_pct(-ecart, 0)} de retard" if rythme["statut"] == "en_retard" else "Je suis dans les temps")
    need = rythme["rythme_jour_necessaire"]
    phrases = [
        f"Échéance : {when}, fin de la saison. Il reste {rythme['jours_restants']} jours pour multiplier la bankroll "
        f"par {_fr(mult, 0)} et atteindre {_fr(objectif, 0)} €.",
        (f"Il faudrait gagner +{_pct(need, 2)} par jour, tous les jours. Un très bon parieur professionnel fait "
         "quelques % par mois." if need is not None else "L'échéance est passée."),
        f"{where} sur la trajectoire régulière (objectif du jour : {_fr(rythme['cible_aujourdhui'])} €). En retard, "
        f"je relève mes mises jusqu'au plafond de mon profil, jamais au-delà de la mise de Kelly. Aujourd'hui : "
        f"{_pct(kelly, 1)} de Kelly.",
        f"Profil {PROFILS[profil]['titre'].lower()}, si le modèle a raison : {_pct(s['raison']['chance_objectif'])} de "
        f"chances d'y être le {when} (bankroll médiane {_fr(s['raison']['mediane_echeance'], 0)} €), "
        f"{_pct(s['raison']['risque_ruine'])} de risque de ruine. Sans avantage réel : "
        f"{_pct(s['aucun']['risque_ruine'])} de risque de ruine.",
        f"Pour comparer, miser deux fois Kelly : {_pct(c['raison']['chance_objectif'])} de chances si le modèle a raison, "
        f"mais {_pct(c['raison']['risque_ruine'], 0)} de ruine même dans ce cas et {_pct(c['aucun']['risque_ruine'], 0)} "
        "sans avantage réel. Je ne le fais pas.",
    ]
    return {"multiplicateur": round(mult, 1), "echeance": rythme, "kelly_du_jour": kelly, "phrases": phrases}


def write(page_folder: str | None, out: str, pronostic: dict, page: dict[str, dict], settings: dict,
          now: datetime, legs: list[dict] | None = None) -> dict:
    """Write out/joueur/<day>.json and out/joueur/etat.json from the page copy; returns the day's document.

    The page copy must hold the `joueur` collection (earlier days, etat) and reglages/joueur.
    """
    import json
    from pathlib import Path

    folder = Path(page_folder or "/nonexistent")
    read = lambda p: json.loads(p.read_text()) if p.exists() else None
    past = {p.stem: json.loads(p.read_text()) for p in (folder / "joueur").glob("*.json") if p.stem != "etat"}
    doc, etat = run(pronostic, past.get(pronostic["jour"]), past, page, settings,
                    read(folder / "reglages" / "joueur.json"), read(folder / "joueur" / "etat.json"), now, legs)
    (Path(out) / "joueur").mkdir(parents=True, exist_ok=True)
    for name, body in ((pronostic["jour"], doc), ("etat", etat)):
        (Path(out) / "joueur" / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return doc


def ticket_stakes(pronostic: dict, day: dict) -> dict:
    """The player's decision on each ticket of "Mon pronostic", kept apart from the ticket's own stake.

    Since 2026-10-12 (owner) the tickets have their own bankroll (the gauge on the right of the page) with their own
    stakes (`mise`, `mise_pct`, `bankroll_du_jour`, set by bilan.apply_stakes); the player's are `mise_joueur`,
    `joue_joueur` and `bankroll_joueur`."""
    by_id = {d["id"]: d for d in day["decisions"]}
    for key, t in (pronostic.get("tickets") or {}).items():
        d = by_id.get(f"ticket:{key}")
        if t.get("jambes") and d:
            t["mise_joueur"], t["joue_joueur"] = d["mise"], d["joue"]
    pronostic["bankroll_joueur"] = day["bankroll_du_jour"]
    return pronostic


# Live bets (owner, 2026-10-15: "le joueur pro, qui a l'occasion de jouer en direct, aurait pu saisir des cotes comme
# buteur Haaland ou Mbappé ou, sur un match analysé confiance comme Liverpool, mettre un buteur"). The in-play model has
# no record against the odds yet, so live bets are one more trial group, "Direct": half the stake, one more point of
# value, their own record, back to paper after 20 settled bets losing more than 10 % per euro (UCL_ARRET). Only the
# prices the live page calls "opportunite" (services/live_page.py: 5 % of value at least, within 10 points of the
# market, never against the match: a side a man down or outplayed, after the 80th minute, odds above 6), at most one
# live bet per match and per day, counted in the day's budget like any other decision. A live decision is taken once:
# it keeps its odds and stake at every later 2-minute cycle and is never decided again.
DIRECT = "Direct"
LIVE_RULE = ("En direct, à l'essai : je ne mise que sur une cote que le modèle du direct juge sous-évaluée d'au moins "
             "5 %, jamais contre le cours du match (infériorité numérique, adversaire qui domine, après la 80e minute, "
             "cote au-dessus de 6), un seul pari en direct par match, mise divisée par deux et un point de valeur de plus. Il faut du neuf "
             "sur le terrain (un but, ou 15 minutes jouées) et des bookmakers qui donnent le pari à 50 % au moins. "
             "J'arrête (retour sur papier) si, après 20 paris réglés, ils perdent plus de 10 % par euro.")
LIVE_MAX_AGE = 300  # seconds: live data older than this is never bet on
LIVE_ONE_PER_MATCH = "Déjà un pari en direct sur ce match : un seul par match."
# What the live bets of 2026-10-15 taught (5 bets, −3,14 €): all taken in the first 4 minutes at 0-0, all with the
# in-play model 5 to 13 points above bookmakers who made them the underdog (34-47 %). Nothing had happened yet, so the
# pre-match analysis, which had passed on them, still held. A live bet now needs something new on the pitch (a goal,
# or 15 minutes played), the bookmakers at 50 % or more, the model within the usual limit of them, and the chance
# mixed with theirs as much as the record says (same rules as before the match).
LIVE_MINUTE_MIN = 15

def leg_live_chance(leg: dict, m: dict) -> float | None:
    """Chance that a leg wins from the match document as it stands (None when the live model cannot say)."""
    if not m:
        return None
    hg, ag = (int(x) for x in m["score"].split(" - ")) if m.get("score") else (0, 0)
    if m.get("statut") == "termine" and leg.get("buteur"):
        res = settle_scorer(leg, m)
        return None if res in (None, "rembourse") else 1.0 if res == "gagne" else 0.0
    if m.get("statut") == "termine" and m.get("score"):
        return 1.0 if settle(leg["pari"], m["domicile"], m["exterieur"], hg, ag) == "gagne" else 0.0
    pari, home, away = leg["pari"], m.get("domicile", ""), m.get("exterieur", "")
    if pari.startswith(("Plus de ", "Moins de ")):
        line = float(pari.split()[2].replace(",", "."))
        if hg + ag > line:
            return 1.0 if pari.startswith("Plus") else 0.0
        return None
    if pari.startswith("Les deux équipes marquent"):
        if hg and ag:
            return 1.0 if pari.endswith("oui") else 0.0
        return None
    if leg.get("buteur"):
        return _scorer_live_chance(leg, m)
    p = (m.get("direct") or {}).get("proba")
    if not p:
        return None
    dom, nul, ext = p["domicile"], p["nul"], p["exterieur"]
    table = {f"Victoire {home}": dom, f"Victoire {away}": ext, "Match nul": nul, f"{home} ou nul": dom + nul,
             f"{away} ou nul": ext + nul, "Pas de match nul": dom + ext, f"{home} (remboursé si nul)": dom,
             f"{away} (remboursé si nul)": ext}
    return table.get(pari)


def _scorer_live_chance(leg: dict, m: dict) -> float | None:
    """A scorer bet during the match: 1 once he scored, 0 when he is off the pitch (substituted, sent off, not on),
    else 1 - exp(-goals still expected for his side x his share x time left on the pitch)."""
    b = leg["buteur"]
    st = player_state(b, m)
    if st["buts"]:
        return 1.0
    rem = (m.get("direct") or {}).get("buts_restants")
    if st["titulaire"] is None and not st["entre"]:
        return None
    if not (st["titulaire"] or st["entre"]) or st["sorti"]:
        return 0.0
    if not rem or b.get("part") is None:
        return None
    side = 0 if b.get("cote_equipe") == "domicile" else 1
    return live_scorer_proba(rem[side], b["part"], float(m.get("minute") or 0), b.get("minutes_par_titularisation"),
                             True, came_on=bool(st["entre"]))


def _fresh(m: dict, now: datetime) -> bool:
    maj = (m.get("direct") or {}).get("maj")
    try:
        return bool(maj) and (now - datetime.fromisoformat(maj)).total_seconds() <= LIVE_MAX_AGE
    except (TypeError, ValueError):
        return False


def _live_stakes(day: dict, page: dict[str, dict], now: datetime, past_days: dict[str, dict] | None) -> list[dict]:
    """The live bets of this cycle (decisions added to `day`), and the opportunities seen but left, with why."""
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    p = PROFILS[day.get("profil") if day.get("profil") in PROFILS else DEFAUT]
    bank = float(day.get("bankroll_du_jour") or 0.0)
    kelly = float(day.get("kelly_du_jour") or p["kelly"])
    lrn = learn({d: v for d, v in past_days.items() if d < day["jour"]}) if past_days is not None else {}
    stopped = DIRECT in (lrn.get("essais_arret") or ())
    ratio = (lrn.get("correction") or {}).get(f"essai:{DIRECT}", 1.0)
    w = (lrn.get("confiance") or {}).get("poids_modele", 1.0)
    vmin = (lrn["valeur_min"] if lrn.get("valeur_min") is not None else p["valeur_min"]) + UCL_MARGE
    budget = round(p["max_jour"] * bank - sum(d["mise"] for d in day["decisions"] if d.get("joue")), 2)
    taken = {l["match_id"] for d in day["decisions"] if d.get("type") == "direct" for l in d["jambes"]}
    left = []
    for key, m in sorted(page.items()):
        if m.get("jour") != day["jour"] or m.get("statut") != "en_direct":
            continue
        rows = [r for r in (m.get("direct") or {}).get("cotes_direct") or [] if r.get("statut") == "opportunite"]
        match, minute, score = f"{m['domicile']} - {m['exterieur']}", m.get("minute"), m.get("score")
        for r in sorted(rows, key=lambda r: -r["valeur"]):
            pm = r.get("proba_marche")
            test = {"proba": r["proba"], "proba_marche": pm, "_divergence_max": r.get("_divergence_max", 0.10)}
            proba = _corrige(_trust(test, w), ratio)
            cote = r["cote"]
            valeur = round(proba * cote - 1, 4)
            k = round(max(valeur, 0) / (cote - 1), 4) if cote > 1 else 0.0
            stake = round(min(min(kelly * UCL_MISE * k, p["max_pari"] * UCL_MISE) * bank, max(budget, 0.0)), 2)
            why = None
            if key in taken:
                why = LIVE_ONE_PER_MATCH
            elif not _fresh(m, now):
                why = "Données du direct trop anciennes pour miser dessus."
            elif float(minute or 0) < LIVE_MINUTE_MIN and not any(c.isdigit() and c != "0" for c in str(score or "")):
                why = (f"{minute or 0}e minute, toujours {score or '0 - 0'} : rien de neuf depuis l'avant-match, dont "
                       "l'analyse tient toujours.")
            elif (pm if pm is not None else 1 / cote) < ESSAI_MARCHE_MIN:
                why = "Les bookmakers le donnent à moins de 50 % : ce n'est pas une vraie confiance."
            elif not agrees(test):
                why = "Le modèle du direct s'écarte trop des bookmakers : il lui manque sans doute une information."
            elif not stopped and valeur < vmin:
                why = (f"Valeur {_pct(valeur)} sous mon minimum de {_pct(vmin, 1)} (un point de plus en direct, à "
                       "l'essai).")
            elif not stopped and proba < p["proba_min"]:
                why = f"Chance de {_pct(proba)} sous mon minimum de {_pct(p['proba_min'], 0)}."
            elif not stopped and budget < MISE_MIN:
                why = f"Budget du jour atteint ({_pct(p['max_jour'], 0)} de la bankroll au plus)."
            elif not stopped and stake < MISE_MIN:
                why = "La mise utile serait inférieure à 10 centimes."
            if why:
                left.append({"match_id": key, "match": match, "minute": minute, "score": score, "pari": r["pari"],
                             "cote": cote, "proba": r["proba"], "valeur": r["valeur"], "joue": False, "raison": why})
                continue
            leg = {"match_id": key, "match": match, "competition": m.get("competition"),
                   "coup_envoi": m.get("coup_envoi") or now.isoformat(), "pari": r["pari"],
                   "marche": r.get("marche") or DIRECT, "cote": cote, "proba": r["proba"],
                   "proba_marche": r.get("proba_marche"), "valeur": r["valeur"], "valide": False,
                   "validation": NON_VALIDE, "verrouille": True,
                   "en_direct": {"minute": minute, "score": score, "heure": now.isoformat()},
                   **({"buteur": r["buteur"]} if r.get("buteur") else {})}
            d = {"id": f"direct:{key}:{r['pari']}", "type": "direct", "quoi": f"{r['pari']} ({match}, en direct)",
                 "jambes": [leg], "cote": cote, "proba": proba, "valeur": valeur, "kelly": k,
                 "proba_modele": r["proba"], "valeur_modele": r["valeur"], "correction": ratio,
                 "cotes": [{"heure": now.isoformat(), "cote": cote}], "fige": True}
            where = f"En direct {minute}e, {score}"
            if stopped:  # back to paper: the bet is noted, one per match, to keep measuring the live model
                d.update(joue=False, mise=0.0, mise_pct=0.0, motif="non_valide", raison=ESSAI_RULE.format(nom=DIRECT))
                text = f"{where} : j'aurais pris {r['pari']} ({match}) à {_fr(cote)}, noté sur papier. {d['raison']}"
            else:
                budget = round(budget - stake, 2)
                d.update(joue=True, mise=stake, mise_pct=round(stake / bank, 4) if bank else 0.0, raison=(
                    f"{where} : chance {_pct(proba, 0)} pour une cote de {_fr(cote)}, valeur {_pct(valeur)}. Kelly "
                    f"conseille {_pct(k)} de la bankroll, j'en mise {_pct(stake / bank if bank else 0)} (profil "
                    f"{p['titre'].lower()}, mise divisée par deux : direct à l'essai, pas encore éprouvé face aux cotes)."))
                text = (f"{where} : je prends {r['pari']} ({match}) à {_fr(cote)} (modèle {_pct(proba, 0)}"
                        + (f", marché {_pct(pm, 0)}" if pm is not None else "")
                        + f", valeur {_pct(valeur)}) : {_fr(stake)} € misés, mise divisée par deux (direct à l'essai).")
            day["decisions"].append(d)
            if not any(x["texte"] == LIVE_RULE for x in day["journal"]):  # the rule once, before the first live bet
                day["journal"].append({"heure": heure, "texte": LIVE_RULE})
            day["journal"].append({"heure": heure, "texte": text})
            taken.add(key)
            left.append({"match_id": key, "match": match, "minute": minute, "score": score, "pari": r["pari"],
                         "cote": cote, "proba": r["proba"], "valeur": r["valeur"], "joue": d["joue"],
                         "raison": "Joué en direct : voir mes décisions." if d["joue"] else d["raison"]})
    return left


def live(day: dict, page: dict[str, dict], now: datetime, past_days: dict[str, dict] | None = None) -> dict:
    """The day's player document during the matches: where each bet played stands, minute by minute, what changed
    (journal), the live bets it takes (LIVE_RULE) and the in-play opportunities it saw and why it left them.

    past_days: the earlier days' documents, for the live trial's own record (back to paper when it loses)."""
    day = {**day, "decisions": [dict(d) for d in day.get("decisions") or []], "journal": list(day.get("journal") or [])}
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    lines = []
    for d in day["decisions"]:
        docs = [page.get(l["match_id"]) or {} for l in d["jambes"]]
        if not d["joue"] or not any(x.get("statut") in ("en_direct", "termine") for x in docs):
            continue
        legs, chances = [], []
        for l, m in zip(d["jambes"], docs):
            c = leg_live_chance(l, m) if m.get("statut") in ("en_direct", "termine") else l["proba"]
            chances.append(l["proba"] if c is None else c)
            legs.append({"match": l["match"], "pari": l["pari"], "statut": m.get("statut", "a_venir"),
                         "score": m.get("score"), "minute": m.get("minute"),
                         "chance": None if c is None else round(c, 4)})
        chance = round(math.prod(chances), 4)
        old = d.get("direct") or {}
        scores = [x["score"] for x in legs]
        d["direct"] = {"maj": now.isoformat(), "chance": chance, "chance_avant": d["proba"], "jambes": legs,
                       "etat": "termine" if all(x["statut"] == "termine" for x in legs) else "en_cours"}
        if scores != old.get("scores_vus") or abs(chance - old.get("chance", d["proba"])) >= 0.15:
            where = ", ".join(f"{x['match']} {x['score'] or ''}" + (f" ({x['minute']}e)" if x["statut"] == "en_direct"
                                                                   and x["minute"] else "") for x in legs
                              if x["statut"] != "a_venir")
            mood = ("en bonne voie" if chance >= max(d["proba"], 0.5) else "en danger" if chance < d["proba"] / 2
                    else "incertain")
            day["journal"].append({"heure": heure, "texte": f"{d['quoi']} ({_fr(d['mise'])} €) : {where}. Chance de "
                                   f"gagner {_pct(chance, 0)} (avant le match {_pct(d['proba'], 0)}), {mood}."})
        d["direct"]["scores_vus"] = scores
        lines.append(f"{d['quoi']} {_pct(chance, 0)}")

    seen = {(o["match_id"], o["pari"]) for o in day.get("direct_vus") or []}
    vus = list(day.get("direct_vus") or [])
    for entry in _live_stakes(day, page, now, past_days):
        if (entry["match_id"], entry["pari"]) in seen:
            continue
        seen.add((entry["match_id"], entry["pari"]))
        vus.append(entry)  # listed apart (direct_vus), not in the journal: dozens a day would drown the decisions
    day["direct_vus"] = vus[-30:]
    day["journal"] = day["journal"][-60:]
    if lines:
        day["en_direct"] = {"maj": now.isoformat(), "resume": "En direct : " + " ; ".join(lines) + "."}
    return day


def live_write(page_folder: str | None, out: str, page: dict[str, dict], now: datetime) -> list[str]:
    """Update out/joueur/<day>.json for each day with a match under way; returns the days written."""
    import json
    from pathlib import Path

    folder = Path(page_folder or "/nonexistent") / "joueur"
    days = {m.get("jour") for m in page.values() if m.get("statut") in ("en_direct", "termine")}
    written = []
    past = {p.stem: json.loads(p.read_text()) for p in folder.glob("*.json") if p.stem != "etat"}
    for name in sorted(d for d in days if d and (folder / f"{d}.json").exists()):
        doc = live(json.loads((folder / f"{name}.json").read_text()), page, now, past)
        (Path(out) / "joueur").mkdir(parents=True, exist_ok=True)
        (Path(out) / "joueur" / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        written.append(name)
    return written


def run(pronostic: dict, previous_day: dict | None, past_days: dict[str, dict], page: dict[str, dict],
        settings: dict, choix: dict | None, previous_etat: dict | None, now: datetime,
        legs: list[dict] | None = None) -> tuple[dict, dict]:
    """The day's joueur/<day> and joueur/etat documents.

    past_days: the joueur documents of the earlier days; settings: reglages/bankroll (depart, objectif);
    choix: reglages/joueur ({profil}).
    """
    day = pronostic["jour"]
    depart, objectif = float(settings["depart"]), float(settings["objectif"])
    earlier = {d: v for d, v in past_days.items() if d < day}
    bank_start, pic, _ = replay(earlier, depart)
    # Autonomous (owner 2026-10-12): the player picks its own profile from its record; the profile set on the page
    # is only the most risk the owner accepts.
    plafond = (choix or {}).get("profil") or "agressif"
    lrn = learn(earlier, plafond if plafond in PROFILS else "agressif")
    choisi = lrn["profil_auto"]
    profil, frein = effective_profile(choisi, bank_start, pic)
    echeance = date.fromisoformat((choix or {}).get("echeance") or f"{ECHEANCE}")
    rythme = pace(bank_start, depart, objectif, date.fromisoformat(day), echeance=echeance)
    kelly = kelly_fraction(PROFILS[profil], rythme["ecart"], bool(frein))
    ajust = {"ratio": lrn["correction"], "valeur_min": lrn["valeur_min"], "ucl_arret": lrn["ucl_arret"],
             "essais_arret": lrn["essais_arret"], "confiance": lrn["confiance"]}
    decisions = decide(pronostic, previous_day, page, bank_start, profil, now, kelly, ajust, legs)

    journal = list((previous_day or {}).get("journal") or [])
    resume = _summary(decisions)
    heure = f"{now.astimezone(LOCAL_TZ):%H:%M}"
    if not journal and lrn["examens"]:
        e = lrn["examens"][0]
        journal.append({"heure": heure, "texte": f"Examen du {e['jour']} : {e['texte']}"})
    if not journal or not any(x["texte"].startswith("Mon profil") for x in journal):
        journal.append({"heure": heure, "texte": f"Mon profil aujourd'hui : {PROFILS[choisi]['titre'].lower()}. "
                                                 f"{lrn['profil_raison']}"})
        if lrn["valeur_min_raison"]:
            journal.append({"heure": heure, "texte": lrn["valeur_min_raison"]})
        if lrn["confiance"]["raison"]:
            journal.append({"heure": heure, "texte": lrn["confiance"]["raison"]})
    if any(_ucl(d) for d in decisions) and not any(x["texte"] == lrn["ucl_sur_papier"]["raison"] for x in journal):
        journal.append({"heure": heure, "texte": lrn["ucl_sur_papier"]["raison"]})
    if any(_essais(d) - {"ucl"} for d in decisions) and not any(x["texte"].startswith("À l'essai") for x in journal):
        journal.append({"heure": heure, "texte": "À l'essai : je mise aussi sur les marchés que le backtest n'a pas "
                                                 "validés, avec des mises divisées par deux, et j'arrête chaque marché "
                                                 "dont le bilan perd."})
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
        o = old.get(d["id"])
        if o and not d.get("fige") and o.get("cote") != d["cote"] and o.get("joue") != d["joue"]:
            move = f"La cote de {d['quoi']} est passée de {_fr(o['cote'])} à {_fr(d['cote'])}"
            journal.append({"heure": heure, "texte": (
                f"{move} : valeur {_pct(d['valeur'])}, je joue {_fr(d['mise'])} €." if d["joue"] else
                f"{move} : {d['raison'][0].lower()}{d['raison'][1:]} Je retire ma mise.")})

    doc = {"jour": day, "maj": now.isoformat(), "profil": profil, "profil_choisi": choisi,
           "bankroll_du_jour": round(bank_start, 2), "kelly_du_jour": kelly, "budget_max": round(PROFILS[profil]["max_jour"] * bank_start, 2),
           "decisions": decisions, "resume": resume, "journal": journal[-60:]}
    for key in ("direct_vus", "en_direct"):  # written by the live loop, kept across the pre-match passes
        if (previous_day or {}).get(key):
            doc[key] = previous_day[key]

    bank, pic, history = replay({**earlier, day: doc}, depart)
    rythme = {**pace(bank, depart, objectif, date.fromisoformat(day), echeance=echeance), "statut_matin": rythme["statut"]}
    prev_sim = (previous_etat or {}).get("simulation") or {}
    key = (round(bank, 2), objectif, rythme["jours_restants"], f"{echeance}")
    sim = (prev_sim if (prev_sim.get("depart"), prev_sim.get("objectif"), prev_sim.get("jours"),
                        prev_sim.get("echeance")) == key and set(prev_sim.get("profils") or {}) == set(PROFILS)
           else simulation(bank, objectif, rythme["jours_restants"], f"{echeance}"))
    all_days = {**earlier, day: doc}
    etat = {
        "maj": now.isoformat(), "profil": profil, "profil_choisi": choisi, "recommande": RECOMMANDE,
        "profils_dispo": {k: {"titre": v["titre"], **{x: v[x] for x in v if x != "titre"}} for k, v in PROFILS.items()},
        "bankroll": {"depart": depart, "objectif": objectif, "actuelle": bank, "pic": pic,
                     "progression": round(bank / objectif, 6), "historique": history},
        "plan": plan(bank, objectif, sim, profil, rythme, kelly), "echeance": rythme, "simulation": sim,
        "apprentissage": lrn, "autonome": True,
        "derniers_jours": [{"jour": d, "resume": all_days[d].get("resume"),
                            "mises": round(sum(x["mise"] for x in all_days[d]["decisions"] if x["joue"]), 2),
                            "gain": round(sum(x.get("gain") or 0 for x in all_days[d]["decisions"] if x["joue"]), 2)}
                           for d in sorted(all_days, reverse=True)[:14]],
        "avertissement": AVERTISSEMENT,
    }
    return doc, etat
