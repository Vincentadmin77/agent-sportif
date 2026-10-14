"""The "Mon pronostic" tab of the results page: the day's three best odds and three tickets.

Built at every pass from the bets the model priced today: every market of the odds feed that follows from the
final score, those the backtest has not validated marked "non validé" and taken with care (TRIAL_* below), and the
Champions League under its own rules; with the page's documents for the match status and the final scores. Nothing is filled in
for the sake of it: a ticket with no correct bets stays empty and says why.

- Top 3: the bets where the model sees the most value over the bookmaker, for reasonable odds.
Tickets, by the owner's rules (2026-10-11), each the first that differs from the ones before it:
- Safe: total odds above 1.50, the most likely combination (at least 50 %).
- Semi-safe: at most 2 bets, total odds above 1.90, the most likely combination (at least 30 %).
- Fun: at most 3 bets, total odds above 2.50, the most likely combination (at least 15 %).

A leg must have model value >= 0 and agree with the market within the selection engine's divergence
limit (a larger gap usually means missing information). A ticket is fixed at the pass 45 min before its
first match, when the line-ups are known, then settled from the page's scores. Once an official ticket
is fixed, a later find of the same kind is a "bonus" ticket, shown apart (owner, 2026-10-11).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.db.models import AnalysisRun, CandidateRow
from sportai.engine.football.markets import VALIDATED_MARKETS, is_quarter
from sportai.selection.engine import TRIAL_MARKETS as TRIAL_MARKETS_DEFAULT
from sportai.services.dashboard import (
    COMPETITION_NAMES,
    MARKET_NAMES,
    bet_label,
    doc_id,
    goal_label,
    goal_market_validated,
    NON_VALIDE,
    settle,
)
from sportai.services.prematch import LOCAL_TZ, journee

TOP_MIN_PROBA, TOP_MAX_ODDS = 0.30, 4.0
# key, title, fewest bets, most bets, total odds above, combined probability at least, each bet at least.
# Tickets are combined bets ("combinés", owner 2026-10-11): two bets at least, three for the fun one; when no
# such combination is correct, the ticket falls back to fewer bets and says so.
TICKETS = (("safe", "Ticket safe", 2, 3, 1.50, 0.50, 0.55),
           ("semi_safe", "Ticket semi safe", 2, 2, 1.90, 0.30, 0.35),
           ("fun", "Ticket fun", 3, 3, 2.50, 0.15, 0.30))
FIX_BEFORE, FIX_SLACK = timedelta(minutes=45), timedelta(minutes=5)
MAX_BONUS = 3  # bonus tickets of each kind in a day
FIXED_NOTE = "Ticket figé au passage 45 min avant son premier match (compositions connues)."
LATE_NOTE = ("Ticket ajouté au dernier passage, 15 min avant son premier match : la cote a passé le seuil après le "
             "passage de 45 min.")
GOAL_MARKETS = {"btts": "Les deux équipes marquent", "totals": "Nombre de buts"}
WARNING = ("Probabilités estimées par le modèle. Celle d'un ticket multiplie celles de ses paris, comme si les "
           "matchs étaient indépendants. Seules les « sélections » des fiches matchs passent tous les contrôles "
           "de l'agent : ces propositions sont plus larges. Un pari « non validé » vient d'un marché que le backtest n'a pas "
           "encore validé : un seul par ticket, mise divisée par deux. Estimation, jamais une garantie.")


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# Champions League in the tickets (owner, 2026-10-14), with care: the cross-league model is calibrated there but
# has no record against the market yet (docs/04-backtest.md). Only a home or away win (the draw is over-predicted),
# odds between 1.30 and 2.50, one Champions League bet per ticket at most, and the ticket's stake halved.
UCL_ODDS = (1.30, 2.50)
UCL_STAKE_FACTOR = 0.5
# Every other market the model prices (owner, 2026-10-14: any odds a bookmaker offers): a bet on a market the backtest
# has not validated is labelled "non validé" and enters with care: 3 % of value at least, a complete market to compare
# with (within the usual divergence limit), no quarter line (half won / half lost is not settled by the page), the
# 5 best of each match only, one such bet per ticket, and the ticket's stake halved (bilan.apply_stakes).
TRIAL_MIN_VALUE = 0.03
TRIAL_PER_MATCH = 5
TRIAL_STAKE_FACTOR = 0.5
# Bets of one match kept for the combinations: the best trade-offs of chance and odds, at most this many.
COMBO_PER_MATCH = 6


def legs_from_db(db: DB, day, ucl: bool = True) -> list[dict]:
    """Every validated-market bet priced today, from the latest run that analysed each match.

    ucl: also the Champions League 1X2 bets, marked `ucl: True` and validated only within the limits above."""
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.status == "completed", AnalysisRun.kind == "prematch")
                      .order_by(AnalysisRun.started_at))
    latest: dict[str, tuple[AnalysisRun, dict]] = {}
    # Markets beyond 1X2 / double chance / draw no bet: with The Odds API their odds were only fetched 45 min before,
    # so the last run that has them is kept for them.
    goals_run: dict[str, int] = {}
    for run in runs:
        if (run.competition == "UCL" and not ucl) or journee(_aware(run.started_at)) != day:
            continue
        priced = set(db.scalars(select(CandidateRow.match_id).where(
            CandidateRow.run_id == run.id, CandidateRow.market.not_in(VALIDATED_MARKETS))))
        for f in run.fixtures or []:
            if not f.get("skipped_reason"):
                latest[f["match_id"]] = (run, f)
                if f["match_id"] in priced:
                    goals_run[f["match_id"]] = run.id
    legs = []
    for match_id, (run, f) in latest.items():
        cfg = run.config or {}
        enabled = cfg.get("enabled_markets", ("1x2", "double_chance", "dnb"))
        paper = run.competition == "UCL"
        if paper:
            enabled = ("1x2",)
        # Goal markets enter the tickets at the owner's request (2026-10-11). Validated (fragile): over/under
        # 1.5 and 3.5 when priced by the enriched goals model (docs/11-backtest-buts.md); btts and 2.5 stay
        # "non validé". Their odds exist only after a pass run with --goals (45 min before kick-off).
        goals = () if paper else cfg.get("info_markets", ("totals", "btts"))
        home, _, away = f["match"].partition(" - ")
        ko = datetime.fromisoformat(f["kickoff"])
        rows = list(db.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id,
                                                          CandidateRow.match_id == match_id)))
        if goals_run.get(match_id, run.id) != run.id:
            rows = [c for c in rows if c.market in VALIDATED_MARKETS] + list(db.scalars(select(CandidateRow).where(
                CandidateRow.run_id == goals_run[match_id], CandidateRow.match_id == match_id,
                CandidateRow.market.not_in(VALIDATED_MARKETS))))
        div = cfg.get("max_market_divergence", 0.10)
        trial_ok = () if paper else cfg.get("trial_markets", TRIAL_MARKETS_DEFAULT)
        trials = []
        for c in rows:
            if c.market in enabled:
                pari, marche = bet_label(c.market, c.selection, home, away), MARKET_NAMES.get(c.market, c.market)
            elif c.market in goals and c.market in GOAL_MARKETS:
                pari, marche = goal_label(c.market, c.selection, c.line), GOAL_MARKETS[c.market]
            elif c.market in trial_ok:
                pari, marche = bet_label(c.market, c.selection, home, away, c.line), MARKET_NAMES.get(c.market, c.market)
            else:
                continue
            valide = (c.market in enabled if c.market not in GOAL_MARKETS
                      else goal_market_validated(c.market, c.line, f.get("goals_model")))
            # A de-vigged two-way price estimates the chance of winning when the bet is not refunded.
            p_cmp = c.p_win / (1 - c.p_push) if c.p_push and c.p_push < 1 else c.p_win
            if not paper and not valide:
                if (is_quarter(c.line) or c.ev < TRIAL_MIN_VALUE or c.market_fair_prob is None
                        or abs(p_cmp - c.market_fair_prob) > div):
                    continue
            leg = {
                "match_id": doc_id(ko, home, away), "match": f["match"],
                "competition": COMPETITION_NAMES.get(run.competition, run.competition), "coup_envoi": ko.isoformat(),
                "pari": pari, "marche": marche,
                "cote": c.odds, "bookmaker": c.bookmaker, "proba": round(c.p_win, 4),
                "proba_marche": None if c.market_fair_prob is None else round(c.market_fair_prob, 4),
                "valeur": round(c.ev, 4),
                # goal legs: only over/under 1.5 and 3.5, and only when the enriched goals model priced them
                "valide": valide,
                "_divergence_max": div, "_p_cmp": round(p_cmp, 4),
                **({"valide": c.selection != "draw" and UCL_ODDS[0] <= c.odds <= UCL_ODDS[1], "ucl": True}
                   if paper else {}),
            }
            if not leg["valide"] and not paper:
                leg["validation"] = NON_VALIDE
                trials.append(leg)
            else:
                legs.append(leg)
        # the best few unvalidated bets of the match, by value
        legs += sorted(trials, key=lambda l: -l["valeur"])[:TRIAL_PER_MATCH]
    return legs


def _trial(leg: dict) -> bool:
    """A bet on a market the backtest has not validated (outside the Champions League's own rules)."""
    return not leg.get("ucl") and not leg.get("valide", True)


def _correct(leg: dict) -> bool:
    if leg.get("ucl") and not leg["valide"]:  # Champions League: only within the limits above
        return False
    return (leg["valeur"] >= (TRIAL_MIN_VALUE if _trial(leg) else 0) and leg["proba_marche"] is not None
            and abs(leg.get("_p_cmp", leg["proba"]) - leg["proba_marche"]) <= leg["_divergence_max"])


def _ticket(legs: list[dict], title: str, message: str | None = None) -> dict:
    if not legs:
        return {"titre": title, "jambes": [], "cote_totale": None, "proba_totale": None, "message": message}
    return {"titre": title, "jambes": legs, "cote_totale": round(math.prod(l["cote"] for l in legs), 2),
            "proba_totale": round(math.prod(l["proba"] for l in legs), 4), "message": message}


def _front(legs: list[dict], keep: int = COMBO_PER_MATCH) -> list[dict]:
    """The bets of each match that no other bet of the same match beats on both chance and odds (a ticket never
    wants a dominated bet), at most `keep` per match spread along that front."""
    by_match: dict[str, list[dict]] = {}
    for l in legs:
        by_match.setdefault(l["match_id"], []).append(l)
    out = []
    for group in by_match.values():
        group = sorted(group, key=lambda l: (-l["proba"], -l["cote"]))
        front, best_odds = [], 0.0
        for l in group:
            if l["cote"] > best_odds:
                front.append(l)
                best_odds = l["cote"]
        if len(front) > keep:
            step = (len(front) - 1) / (keep - 1)
            front = [front[round(k * step)] for k in range(keep)]
        out += front
    return out


def _combos(legs: list[dict], min_legs: int, max_legs: int, min_odds: float, min_total: float, min_leg: float):
    pool = _front([l for l in legs if l["proba"] >= min_leg])
    for n in range(min_legs, max_legs + 1):
        for combo in combinations(pool, n):
            if (len({l["match_id"] for l in combo}) == n and sum(bool(l.get("ucl")) for l in combo) <= 1
                    and sum(_trial(l) for l in combo) <= 1
                    and math.prod(l["cote"] for l in combo) > min_odds
                    and math.prod(l["proba"] for l in combo) >= min_total):
                yield list(combo)


def _settle_leg(leg: dict, page: dict[str, dict]) -> dict:
    leg = dict(leg)
    doc = page.get(leg["match_id"]) or {}
    if doc.get("statut") == "termine" and doc.get("score"):
        hg, ag = (int(x) for x in doc["score"].split(" - "))
        leg["resultat"] = settle(leg["pari"], doc["domicile"], doc["exterieur"], hg, ag)
    return leg


def _settle_ticket(ticket: dict, page: dict[str, dict]) -> dict:
    legs = [_settle_leg(leg, page) for leg in ticket["jambes"]]
    out = {**ticket, "jambes": legs}
    results = [l.get("resultat") for l in legs]
    if legs:
        out["resultat"] = ("perdu" if "perdu" in results else "en_cours" if None in results
                           else "gagne" if "gagne" in results else "rembourse")
    return out


def build_pronostic(legs: list[dict], page: dict[str, dict], previous: dict | None, now: datetime,
                    passage: str, day=None) -> dict:
    """The day's "pronostic" document; legs already played stay as published earlier."""
    day = day or journee(now)
    previous = previous or {}

    def started(leg: dict) -> bool:
        status = (page.get(leg["match_id"]) or {}).get("statut", "a_venir")
        return status != "a_venir" or datetime.fromisoformat(leg["coup_envoi"]) <= now

    open_legs = [l for l in legs if not started(l) and _correct(l)]
    notes = []

    # Top 3: keep what was shown for matches under way, fill the rest from the matches to come.
    kept = [_settle_leg({**l, "verrouille": True}, page) for l in previous.get("top3") or [] if started(l)]
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

    def fixed(ticket_legs: list[dict]) -> bool:
        # Official tickets are fixed at the pass 45 min before their first match, line-ups known (owner, 2026-10-11).
        first = min(datetime.fromisoformat(l["coup_envoi"]) for l in ticket_legs)
        return first - FIX_BEFORE <= now + FIX_SLACK

    def build(rules: tuple, old: dict | None, taken: list[set], empty_message: bool,
              pool: list[dict] | None = None) -> dict | None:
        _, title, min_legs, max_legs, min_odds, min_total, min_leg = rules
        if old and old.get("jambes") and (old.get("fige") or any(started(l) for l in old["jambes"])):
            frozen = {**old, "fige": True, "jambes": [{**l, "verrouille": True} if started(l) else l
                                                      for l in old["jambes"]]}
            return _settle_ticket(frozen, page)
        for fewest in range(min_legs, 0, -1):
            combos = [c for c in _combos(open_legs if pool is None else pool, fewest, max_legs, min_odds, min_total, min_leg)
                      if {(l["match_id"], l["pari"]) for l in c} not in taken]
            if combos:
                break
        if combos:
            # The target odds reached with the best chance; the fewer bets the better on a tie.
            best = sorted(max(combos, key=lambda c: (math.prod(l["proba"] for l in c), -len(c))),
                          key=lambda l: l["coup_envoi"])
            short = None if len(best) >= min_legs else (
                f"Pas de combiné de {min_legs} paris correct pour l'instant : "
                + ("un seul pari" if len(best) == 1 else f"{len(best)} paris") + " atteint la cote visée.")
            ticket = _ticket(best, title, short)
            if fixed(best):
                # an official ticket still empty at the 45-min pass can be filled by a later one, then fixed at once
                late = now > min(datetime.fromisoformat(l["coup_envoi"]) for l in best) - FIX_BEFORE + FIX_SLACK
                ticket["fige"] = True
                ticket["message"] = " ".join(filter(None, [LATE_NOTE if late else FIXED_NOTE, short]))
            else:
                ticket["fige"] = False
            return ticket
        if not empty_message:
            return None
        most = "un seul pari" if max_legs == 1 else f"{max_legs} paris au plus"
        return _ticket([], title, (
            f"Pas de {title.lower()} correct : il faudrait une cote totale au-dessus de "
            f"{min_odds:.2f}".replace(".", ",") + f" avec {most}, chacun à {round(min_leg * 100)} % ou plus "
            f"et de valeur positive, pour une chance combinée d'au moins {round(min_total * 100)} %."))

    def bets(ticket: dict) -> set:
        return {(l["match_id"], l["pari"]) for l in ticket["jambes"]}

    tickets: dict[str, dict] = {}
    for rules in TICKETS:
        tickets[rules[0]] = build(rules, (previous.get("tickets") or {}).get(rules[0]),
                                [bets(t) for t in tickets.values()], True)
    # Once an official ticket is fixed, later finds of the same kind are bonus tickets, shown apart: the ones
    # already fixed stay, plus the best new one, until the next pass 45 min before its first match fixes it.
    bonus: dict[str, list[dict]] = {}
    for key, title, *rest in TICKETS:
        # only from the pass after the official ticket was fixed: a bonus is a later find, not a near copy
        if not (tickets[key].get("fige") and ((previous.get("tickets") or {}).get(key) or {}).get("fige")):
            continue
        rules = (key, title.replace("Ticket", "Ticket bonus"), *rest)
        # a bonus shares no bet with the official ticket of its kind: other bets, not a variant of it
        official = bets(tickets[key])
        kept = [build(rules, b, [], False) for b in (previous.get("bonus") or {}).get(key) or []
                if (b.get("fige") or any(started(l) for l in b["jambes"])) and not bets(b) & official]
        taken = [bets(t) for t in [*tickets.values(), *kept, *(b for group in bonus.values() for b in group)]]
        new = build(rules, None, taken, False,
                    [l for l in open_legs if (l["match_id"], l["pari"]) not in official])
        bonus[key] = kept + ([new] if new and len(kept) < MAX_BONUS else [])

    filled = [t["titre"] for t in tickets.values() if t["jambes"]]
    if filled:
        notes.append(f"{len(top3)} cote(s) proposée(s). Prêt : " + ", ".join(filled).lower() + ".")
    else:
        notes.append("Aucun ticket correct pour l'instant, on n'en invente pas.")
    clean = lambda l: {k: v for k, v in l.items() if not k.startswith("_")}
    for t in [*tickets.values(), *(b for group in bonus.values() for b in group)]:
        t["jambes"] = [clean(l) for l in t["jambes"]]
    return {"jour": f"{day:%Y-%m-%d}", "maj": now.isoformat(), "passage": passage, "message": " ".join(notes),
            "top3": [clean(l) for l in top3], "tickets": tickets, "bonus": bonus, "avertissement": WARNING}
