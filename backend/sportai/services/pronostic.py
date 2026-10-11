"""The "Mon pronostic" tab of the results page: the day's three best odds and two combined tickets.

Built at every pass from the bets the model priced today (validated markets only, Champions League
excluded), with the page's documents for the match status and the final scores. Nothing is filled in
for the sake of it: a ticket with no correct bets stays empty and says why.

- Top 3: the bets where the model sees the most value over the bookmaker, for reasonable odds.
- Safe ticket: 2 or 3 bets, the combination the model finds most likely to come in.
- Semi-safe ticket: 2 or 3 bets, the best value whose combined probability stays at least 25 %.

A leg must have model value >= 0 and agree with the market within the selection engine's divergence
limit (a larger gap usually means missing information). A ticket whose first match has kicked off is
frozen for the rest of the day, and settled from the page's scores.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.db.models import AnalysisRun, CandidateRow
from sportai.services.dashboard import (
    COMPETITION_NAMES,
    MARKET_NAMES,
    bet_label,
    doc_id,
    settle,
)
from sportai.services.prematch import LOCAL_TZ

TOP_MIN_PROBA, TOP_MAX_ODDS = 0.30, 4.0
SAFE_MIN_PROBA, SAFE_MIN_TOTAL = 0.55, 0.40
SEMI_MIN_PROBA, SEMI_MIN_TOTAL = 0.35, 0.25
WARNING = ("Probabilités estimées par le modèle. Celle d'un ticket multiplie celles de ses paris, comme si les "
           "matchs étaient indépendants. Seules les « sélections » des fiches matchs passent tous les contrôles "
           "de l'agent : ces propositions sont plus larges. Estimation, jamais une garantie.")


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def legs_from_db(db: DB, day) -> list[dict]:
    """Every validated-market bet priced today, from the latest run that analysed each match."""
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.status == "completed", AnalysisRun.kind == "prematch")
                      .order_by(AnalysisRun.started_at))
    latest: dict[str, tuple[AnalysisRun, dict]] = {}
    for run in runs:
        if run.competition == "UCL" or _aware(run.started_at).astimezone(LOCAL_TZ).date() != day:
            continue
        for f in run.fixtures or []:
            if not f.get("skipped_reason"):
                latest[f["match_id"]] = (run, f)
    legs = []
    for match_id, (run, f) in latest.items():
        cfg = run.config or {}
        enabled = cfg.get("enabled_markets", ("1x2", "double_chance", "dnb"))
        home, _, away = f["match"].partition(" - ")
        ko = datetime.fromisoformat(f["kickoff"])
        for c in db.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id, CandidateRow.match_id == match_id)):
            if c.market not in enabled:
                continue
            legs.append({
                "match_id": doc_id(ko, home, away), "match": f["match"],
                "competition": COMPETITION_NAMES.get(run.competition, run.competition), "coup_envoi": ko.isoformat(),
                "pari": bet_label(c.market, c.selection, home, away), "marche": MARKET_NAMES.get(c.market, c.market),
                "cote": c.odds, "bookmaker": c.bookmaker, "proba": round(c.p_win, 4),
                "proba_marche": None if c.market_fair_prob is None else round(c.market_fair_prob, 4),
                "valeur": round(c.ev, 4), "valide": True,
                "_divergence_max": cfg.get("max_market_divergence", 0.10),
            })
    return legs


def _correct(leg: dict) -> bool:
    return (leg["valeur"] >= 0 and leg["proba_marche"] is not None
            and abs(leg["proba"] - leg["proba_marche"]) <= leg["_divergence_max"])


def _ticket(legs: list[dict], title: str, message: str | None = None) -> dict:
    if not legs:
        return {"titre": title, "jambes": [], "cote_totale": None, "proba_totale": None, "message": message}
    return {"titre": title, "jambes": legs, "cote_totale": round(math.prod(l["cote"] for l in legs), 2),
            "proba_totale": round(math.prod(l["proba"] for l in legs), 4), "message": message}


def _combos(legs: list[dict], min_proba: float, min_total: float):
    pool = [l for l in legs if l["proba"] >= min_proba]
    for n in (2, 3):
        for combo in combinations(pool, n):
            if len({l["match_id"] for l in combo}) == n and math.prod(l["proba"] for l in combo) >= min_total:
                yield list(combo)


def _settle_ticket(ticket: dict, page: dict[str, dict]) -> dict:
    legs = []
    for leg in ticket["jambes"]:
        leg = dict(leg)
        doc = page.get(leg["match_id"]) or {}
        if doc.get("statut") == "termine" and doc.get("score"):
            hg, ag = (int(x) for x in doc["score"].split(" - "))
            leg["resultat"] = settle(leg["pari"], doc["domicile"], doc["exterieur"], hg, ag)
        legs.append(leg)
    out = {**ticket, "jambes": legs}
    results = [l.get("resultat") for l in legs]
    if legs:
        out["resultat"] = ("perdu" if "perdu" in results else "en_cours" if None in results
                           else "gagne" if "gagne" in results else "rembourse")
    return out


def build_pronostic(legs: list[dict], page: dict[str, dict], previous: dict | None, now: datetime,
                    passage: str, day=None) -> dict:
    """The day's "pronostic" document; legs already played stay as published earlier."""
    day = day or now.astimezone(LOCAL_TZ).date()
    previous = previous or {}

    def started(leg: dict) -> bool:
        status = (page.get(leg["match_id"]) or {}).get("statut", "a_venir")
        return status != "a_venir" or datetime.fromisoformat(leg["coup_envoi"]) <= now

    open_legs = [l for l in legs if not started(l) and _correct(l)]
    notes = []

    # Top 3: keep what was shown for matches under way, fill the rest from the matches to come.
    kept = [{**l, "verrouille": True} for l in previous.get("top3") or [] if started(l)]
    fresh = sorted((l for l in open_legs if l["proba"] >= TOP_MIN_PROBA and l["cote"] <= TOP_MAX_ODDS),
                   key=lambda l: -l["valeur"])
    top3 = kept[:]
    for leg in fresh:
        if len(top3) == 3:
            break
        if leg["match_id"] not in {t["match_id"] for t in top3}:
            top3.append(leg)
    if not top3:
        notes.append("Aucune cote correcte pour l'instant : le modèle ne voit d'avantage sur aucun pari à venir "
                     "sans contredire nettement le marché.")

    tickets = {}
    for key, title, pick, min_p, min_t, empty in (
        ("safe", "Ticket safe", lambda c: (math.prod(l["proba"] for l in c), len(c) == 2), SAFE_MIN_PROBA,
         SAFE_MIN_TOTAL, ("Pas de ticket safe correct : il faudrait au moins deux paris à {p} % ou plus chacun, avec "
                          "une valeur positive, pour une chance combinée d'au moins {t} %.")),
        ("semi_safe", "Ticket semi safe",
         lambda c: (math.prod(l["proba"] for l in c) * math.prod(l["cote"] for l in c), len(c) == 2), SEMI_MIN_PROBA,
         SEMI_MIN_TOTAL, ("Pas de ticket semi safe correct : il faudrait au moins deux paris à {p} % ou plus, avec une "
                          "valeur positive, pour une chance combinée d'au moins {t} %.")),
    ):
        old = (previous.get("tickets") or {}).get(key)
        if old and old.get("jambes") and any(started(l) for l in old["jambes"]):
            frozen = {**old, "jambes": [{**l, "verrouille": True} if started(l) else l for l in old["jambes"]],
                      "message": "Ticket figé : son premier match a commencé."}
            tickets[key] = _settle_ticket(frozen, page)
            continue
        combos = list(_combos(open_legs, min_p, min_t))
        if key == "semi_safe" and tickets.get("safe", {}).get("jambes"):
            same = {(l["match_id"], l["pari"]) for l in tickets["safe"]["jambes"]}
            combos = [c for c in combos if {(l["match_id"], l["pari"]) for l in c} != same]
        if combos:
            best = max(combos, key=pick)
            tickets[key] = _ticket(sorted(best, key=lambda l: l["coup_envoi"]), title)
        else:
            tickets[key] = _ticket([], title, empty.format(p=round(min_p * 100), t=round(min_t * 100)))

    filled = [t["titre"] for t in tickets.values() if t["jambes"]]
    if filled:
        notes.append(f"{len(top3)} cote(s) proposée(s). Prêt : " + " et ".join(filled).lower() + ".")
    else:
        notes.append("Aucun ticket correct pour l'instant, on n'en invente pas.")
    clean = lambda l: {k: v for k, v in l.items() if not k.startswith("_")}
    for t in tickets.values():
        t["jambes"] = [clean(l) for l in t["jambes"]]
    return {"jour": f"{day:%Y-%m-%d}", "maj": now.isoformat(), "passage": passage, "message": " ".join(notes),
            "top3": [clean(l) for l in top3], "tickets": tickets, "avertissement": WARNING}
