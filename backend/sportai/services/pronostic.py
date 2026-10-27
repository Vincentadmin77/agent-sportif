"""The "Mon pronostic" tab of the results page: the day's three best odds and three tickets.

Built at every pass from the bets the model priced today: every market of the odds feed that follows from the
final score, those the backtest has not validated marked "non validé" and taken with care (TRIAL_* below), and the
Champions League under its own rules; with the page's documents for the match status and the final scores. Nothing is filled in
for the sake of it: a ticket with no correct bets stays empty and says why.

- Top 3: the bets where the model sees the most value over the bookmaker, for reasonable odds.
Tickets, by the owner's rules (2026-10-11), each the first that differs from the ones before it (chances of the safe
and semi-safe tickets: the more cautious of the model's and the market's, see below):
- Safe: total odds above 1.50, the most likely combination (at least 50 %).
- Semi-safe: at most 2 bets, total odds above 1.90, the most likely combination (at least 30 %).
- Fun: at most 3 bets, total odds above 2.50, the most likely combination (at least 15 %).

A leg must have model value >= 0. The safe and semi-safe tickets are chosen on confidence: each bet counts for the
more cautious of the model's and the bookmakers' chances (owner, 2026-10-15: City at 55 % for the model but 48 % for
the market was the safe ticket of 2026-10-14, while Liverpool and Aston Villa, far more likely for both, were left out
because the model rated them over 10 points above the market). A model far from the market still rules a bet out
(it usually means information the model lacks), except above it on a bet the market makes the favourite. A ticket is fixed at the pass 45 min before its
first match, when the line-ups are known, then settled from the page's scores. Once an official ticket
is fixed, a later find of the same kind is a "bonus" ticket, shown apart (owner, 2026-10-11): since 2026-10-17 an
"idée" of "La boîte à idées" tab, with its own success rate, never with the three official tickets of the day.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.db.models import AnalysisRun, CandidateRow
from sportai.engine.football.markets import VALIDATED_MARKETS, is_quarter
from sportai.selection.engine import COTE_MIN, TRIAL_MARKETS as TRIAL_MARKETS_DEFAULT
from sportai.services.dashboard import (
    COMPETITION_NAMES,
    MARKET_NAMES,
    bet_label,
    doc_id,
    goal_label,
    goal_market_validated,
    NON_VALIDE,
    SCORER_MARKET,
    scorer_label,
    settle,
)
from sportai.services.buteurs import scorer_leg_info, settle_scorer
from sportai.services.prematch import LOCAL_TZ, journee
from sportai.services.revue import GOAL_FAMILIES, correct_leg
from sportai.services.runs import EUROPEAN_CUPS

TOP_MIN_PROBA, TOP_MAX_ODDS = 0.30, 4.0
# key, title, fewest bets, most bets, total odds above, combined probability at least, each bet at least.
# The safe ticket is always a single bet above 1.65 (owner, 2026-10-15); the others are combined bets ("combinés",
# owner 2026-10-11): two bets, three for the fun one; when no such combination is correct, the ticket falls back to
# fewer bets and says so.
# Since 2026-10-21 the three official tickets are shown as the "top idées" of the day (owner: "les meilleures
# confiances de toutes les idées prises pour la journée, et figées"), in the same tab as the other ideas.
TICKETS = (("safe", "Top idée safe", 1, 1, 1.65, 0.50, 0.55),
           ("semi_safe", "Top idée semi safe", 2, 2, 1.90, 0.30, 0.35),
           ("fun", "Top idée fun", 3, 3, 2.50, 0.15, 0.30))
FIX_BEFORE, FIX_SLACK = timedelta(minutes=45), timedelta(minutes=5)
MAX_BONUS = 3  # bonus tickets of each kind in a day
FIXED_NOTE = "Figée au passage 45 min avant son premier match (compositions connues)."
LATE_NOTE = ("Ajoutée au dernier passage, 15 min avant son premier match : la cote a passé le seuil après le "
             "passage de 45 min.")
IDEAS_NOTE = ("La boîte à idées : en tête, les trois top idées du jour (safe, semi safe, fun), celles de meilleure "
              "confiance, figées avant leur premier match ; puis les autres positions possibles trouvées ensuite. "
              "Tout le monde vient y piocher des idées. Les autres idées ne comptent pas dans le bilan des top idées "
              "et ont leur propre taux de réussite. Jamais de pari en direct.")
GOAL_MARKETS = {"btts": "Les deux équipes marquent", "totals": "Nombre de buts"}
WARNING = ("Probabilités estimées par le modèle. Celle d'un ticket multiplie celles de ses paris, comme si les "
           "matchs étaient indépendants. Seules les « sélections » des fiches matchs passent tous les contrôles "
           "de l'agent : ces propositions sont plus larges. Un pari « non validé » vient d'un marché que le backtest n'a pas "
           "encore validé : un seul par ticket, mise divisée par deux. Estimation, jamais une garantie.")

WARNING_PICKS = ("Probabilités estimées : la confiance mélange à parts égales le modèle et les bookmakers. Un pari "
                 "« non validé » vient d'un marché que le backtest n'a pas encore validé. Estimation, jamais une "
                 "garantie.")


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# Champions League in the tickets (owner, 2026-10-14), with care: the cross-league model is calibrated there but
# has no record against the market yet (docs/04-backtest.md). A home or away win (the draw is over-predicted) at odds
# between 1.15 and 2.50, and the ticket's stake halved. Since 2026-10-15 (owner: Liverpool with Aston Villa, over 1.5
# goals, both teams score), several Champions League matches may share a ticket and its other markets enter as
# "non validé" bets, under the same care as elsewhere (TRIAL_* below).
UCL_ODDS = (COTE_MIN, 2.50)  # 1.15 until 2026-10-22  # floor 1.30 until 2026-10-15: it kept Benfica (1.24) and Bournemouth (1.21) out of the safe ticket
# A bet under these odds adds next to nothing to a ticket's odds but still its own risk (under 5.5 goals at 1.07 padding the
# 2026-10-15 morning tickets): never a ticket leg.
TICKET_MIN_LEG_ODDS = COTE_MIN  # 1.15 until 2026-10-22
UCL_STAKE_FACTOR = 0.5
# Every other market the model prices (owner, 2026-10-14: any odds a bookmaker offers): a bet on a market the backtest
# has not validated is labelled "non validé" and enters with care: 3 % of value at least, a complete market to compare
# with (within the usual divergence limit), no quarter line (half won / half lost is not settled by the page), the
# 5 best of each match only (25 % of chance and odds of 4.00 at most), one such bet per ticket, and the ticket's
# stake halved (bilan.apply_stakes).
TRIAL_MIN_VALUE = 0.03
TRIAL_MIN_PROBA, TRIAL_MAX_ODDS = 0.25, 4.0  # no long shot (exact scores at 50.00 and the like)
TRIAL_PER_MATCH = 5
TRIAL_STAKE_FACTOR = 0.5
# Bets of one match kept for the combinations: the best trade-offs of chance and odds, at most this many.
COMBO_PER_MATCH = 6
# Goal bets (both teams score yes / no, over / under 1.5, 2.5, 3.5, a team's over / under 0.5 and 1.5), owner
# 2026-10-24: "il n'y a pas assez de « + de 1,5 but » ou « les deux équipes marquent »" (2026-10-23: Brest - Nice and
# Stuttgart - Gladbach both teams scored, Alavés - Málaga under 2.5, none proposed). They stay "non validé" (only
# over / under 1.5 and 3.5 of the enriched goals model are validated, docs/11-backtest-buts.md) but were crowded out:
# each match kept its 5 best unvalidated bets by value, nearly always exact goals, handicaps and combinations, and
# needed 3 % of value. They now have their own places (GOAL_PER_MATCH, not counted in TRIAL_PER_MATCH) and enter
# with a value above 0 after the review's correction (sportai/services/revue.py), at odds of 1.50 or more, the model
# within the usual limit of a complete market, and a chance of GOAL_MIN_PROBA at least on the more cautious of the
# model and the bookmakers. Over 1.5 goals of a whole match rarely reaches 1.50; a team's over 1.5 or a combination
# often does.
GOAL_PER_MATCH = 3
GOAL_MIN_VALUE = 0.0
GOAL_MIN_PROBA = 0.40
# Next match (owner, 2026-10-26: "s'ils vont devoir se retenir pour tout donner le match suivant"): a team whose
# rotation risk is "élevé" (contexte.prochain_match, docs/15-prochain-match.md) may rest players tonight. Its effect
# is not measured yet, so the model's chances are left alone; the tickets and the ideas only leave out the bets that
# need that team to win, to cover a handicap of 0 or less, or to score 2 goals or more (ROTATION_LEVEL).
ROTATION_LEVEL = "élevé"


# One bet per match (owner, 2026-10-27: "les agents vont chercher une seule cote valable par match"), shown on each
# match card and added to "Mon carnet" by whoever wants it; the day's single official pick, the "Top confiance du
# jour", is then the most likely of these bets at odds of 1.60 or more (owner, 2026-10-27: "1 seul ticket retenu qui
# devient la meilleure confiance, toujours au-dessus de 1.60"; it replaced 3 selections the same morning). From
# PER_MATCH_FROM on, no more combined tickets, bonus ideas or top 3: the review of 11-26 October (54 % of the bets won
# for 62 % announced) found the model too sure of itself whenever it strays from the bookmakers, the combined
# tickets and the odds above 2.20 losing most. So a match's bet must have:
# - odds of 1.60 or more and no upper limit (owner, 2026-10-27 12:52: "la cote mini est de 1.60 mais pas de cote
#   max ... si l'algorithme trouve une cote bien plus haute qui a de fortes probabilités il faut l'étudier aussi");
#   a high odd still has to pass the confidence and agreement rules below (3 bets won out of 20 between 2.20 and 3.00
#   in the review, when the model alone chose them);
# - the model no more than PICK_MAX_GAP above the bookmakers (over 5 points the bets won 51 % for 64 % announced);
# - a confidence of PICK_MIN_CHANCE at least, the confidence being half the model's chance and half the bookmakers'
#   (PICK_MODEL_WEIGHT), and value at that confidence;
# - not a team's "moins de X buts" (6 won out of 14 for 68 % announced), paused until it proves itself.
# Among those, the most likely; a match without one says why. Each bet is fixed 45 min before its kick-off.
PER_MATCH_FROM = "2026-10-01"  # owner 2026-10-27 09:31: "comme si nous avions fait comme ça depuis le début"
PICK_ODDS = (1.60, None)
PICK_MAX_GAP = 0.08
PICK_MIN_CHANCE = 0.50
PICK_MODEL_WEIGHT = 0.5
PICK_HIGH = 0.60  # "Confiance élevée" from here, "Confiance moyenne" below
# How the day's official pick is chosen among the match bets: "confiance" (most likely first, owner 2026-10-27)
# or "cote" (highest odds first); only bets at SELECTION_MIN_ODDS or more qualify.
SELECTION_RANK = "confiance"
SELECTIONS_PER_DAY = 1
SELECTION_MIN_ODDS = 1.60
SELECTION_MIN_CONF = PICK_HIGH  # owner 2026-10-27 14:56: the best single only among "Confiance élevée" bets
SELECTION_TITLE = "Top confiance du jour"
PICK_TITLE = "Probabilité retenue"
NO_PICK = ("Aucune cote valable sur ce match : il faudrait une cote d'au moins 1,60 où le modèle et les bookmakers "
           "s'accordent, avec au moins 50 % de chances et de la valeur. On ne force pas de pari.")
PICKS_NOTE = ("Sur chaque match, l'agent retient au plus une cote : celle où le modèle et les bookmakers s'accordent "
              "le mieux, à 1,60 ou plus. La meilleure confiance simple et la meilleure confiance combiné du jour sont "
              "choisies parmi elles. "
              "Ajoute celles que tu veux à ton carnet. Estimation, jamais une garantie.")


def blended(leg: dict) -> float:
    """The confidence of a match's bet: half the model's chance (after the review's correction), half the bookmakers'."""
    pm = leg.get("proba_marche")
    p = leg.get("_p_cmp", leg["proba"])
    return p if pm is None else PICK_MODEL_WEIGHT * p + (1 - PICK_MODEL_WEIGHT) * pm


def pick_ok(leg: dict) -> bool:
    pm = leg.get("proba_marche")
    if pm is None or leg.get("buteur") or leg["cote"] < PICK_ODDS[0] or (PICK_ODDS[1] and leg["cote"] > PICK_ODDS[1]):
        return False
    if " : moins de " in leg["pari"]:  # a team's under: paused (review of 2026-10-27)
        return False
    c = blended(leg)
    return leg.get("_p_cmp", leg["proba"]) - pm <= PICK_MAX_GAP and c >= PICK_MIN_CHANCE and c * leg["cote"] > 1


def _pick(leg: dict) -> dict:
    c = blended(leg)
    out = {k: v for k, v in leg.items() if not k.startswith("_")}
    out.update(titre=PICK_TITLE, confiance=round(c, 4), valeur_retenue=round(c * leg["cote"] - 1, 4),
               niveau="Confiance élevée" if c >= PICK_HIGH else "Confiance moyenne")
    return out


def build_picks(legs: list[dict], page: dict[str, dict], previous: dict | None, now: datetime, day,
                started, blocked) -> tuple[dict[str, dict], list[dict]]:
    """The bet of each match of the day (match_id -> bet, or a reason when none) and the day's official pick(s)."""
    previous = previous or {}
    old = previous.get("paris_retenus") or {}

    def frozen(p: dict) -> bool:
        return started(p) or datetime.fromisoformat(p["coup_envoi"]) - FIX_BEFORE <= now + FIX_SLACK

    by_match: dict[str, list[dict]] = {}
    for l in legs:
        by_match.setdefault(l["match_id"], []).append(l)
    ids = set(by_match) | set(old) | {k for k, m in page.items() if m.get("jour") == f"{day:%Y-%m-%d}"}
    picks: dict[str, dict] = {}
    for mid in sorted(ids):
        prev = old.get(mid)
        if prev and prev.get("pari") and (prev.get("fige") or frozen(prev)):
            picks[mid] = _settle_leg({**prev, "fige": True}, page)
            continue
        ok = [l for l in by_match.get(mid, []) if not started(l) and _correct(l) and pick_ok(l) and not blocked(l)]
        if ok:
            best = max(ok, key=lambda l: (blended(l), l["cote"]))
            picks[mid] = {**_pick(best), "fige": frozen(best)}
            continue
        m = page.get(mid) or {}
        doc = (by_match.get(mid) or [{}])[0]
        match = doc.get("match") or (f"{m.get('domicile')} - {m.get('exterieur')}" if m else mid)
        ko = doc.get("coup_envoi") or m.get("coup_envoi")
        under_way = (m.get("statut", "a_venir") != "a_venir") or (ko and datetime.fromisoformat(ko) <= now)
        picks[mid] = {"match_id": mid, "match": match, "titre": PICK_TITLE, "pari": None,
                      "raison": "Pas de cote retenue avant le coup d'envoi." if under_way and not prev else
                      (prev or {}).get("raison") if under_way else NO_PICK}
    # the official pick: once fixed it stays, otherwise it goes to the best bet not yet fixed at 1.60 or more
    rank = (lambda p: (p["cote"], p["confiance"])) if SELECTION_RANK == "cote" else (lambda p: (p["confiance"], p["cote"]))
    kept = [s for s in previous.get("selections") or [] if (picks.get(s["match_id"]) or {}).get("fige")
            and (picks[s["match_id"]].get("pari") == s.get("pari"))]
    kept = [{**picks[s["match_id"]], "rang": s.get("rang")} for s in kept]
    free = sorted((p for p in picks.values() if p.get("pari") and not p.get("fige") and p["cote"] >= SELECTION_MIN_ODDS
                   and p["confiance"] >= SELECTION_MIN_CONF and p["match_id"] not in {s["match_id"] for s in kept}), key=rank, reverse=True)
    selections = kept + [dict(p) for p in free[:max(0, SELECTIONS_PER_DAY - len(kept))]]
    for i, s in enumerate(sorted(selections, key=lambda s: s["coup_envoi"]), 1):
        s["rang"] = i
        s["titre"] = SELECTION_TITLE if SELECTIONS_PER_DAY == 1 else f"Sélection {i}"
    return picks, sorted(selections, key=lambda s: s["rang"])


# Combos of 2 (owner, 2026-10-27 09:24: "combiné de deux max à côté, ... juste pas retenu dans la confiance du jour,
# il garde son ratio"): the retained bets of two different matches (the odds of two bets on the same match are not
# the product of their odds), never the match of the Top confiance and never a bet in two combos. The most confident
# bets are paired first. Since 2026-10-27 12:52 (owner: "la meilleure confiance simple et la meilleure confiance
# combiné") one combo a day, the most confident pair, and no upper limit on its odds (COMBO_MAX_ODDS None).
# A combo is fixed with its first match (45 min before kick-off) and both its bets stay as they were then.
COMBO_MAX_ODDS = None
COMBOS_PER_DAY = 1  # the selected one(s): "meilleure confiance combiné" (retenu: True)
# Owner 2026-10-27 15:27: "dans combiné : tous les paris doubles et à côté les totaux": every other pair is kept too.
COMBO_TITLE = "Combiné de 2"


def build_combos(picks: dict[str, dict], selections: list[dict], previous: dict | None, now: datetime,
                 page: dict[str, dict], started) -> list[dict]:
    def frozen_at(ko: str) -> bool:
        return datetime.fromisoformat(ko) - FIX_BEFORE <= now + FIX_SLACK

    def combo(legs: list[dict], fixed: bool) -> dict:
        legs = sorted(legs, key=lambda l: l["coup_envoi"])
        out = _settle_ticket({"titre": COMBO_TITLE, "jambes": legs, "cote_totale": round(legs[0]["cote"] * legs[1]["cote"], 2),
                              "proba_totale": round(legs[0]["confiance"] * legs[1]["confiance"], 4), "fige": fixed}, page)
        if out.get("resultat") == "en_cours":
            out.pop("resultat")
        return out

    kept = []
    for c in (previous or {}).get("combines") or []:
        legs = c.get("jambes") or []
        if len(legs) == 2 and (c.get("fige") or any(started(l) or frozen_at(l["coup_envoi"]) for l in legs)):
            kept.append({**combo([{**l, "fige": True} for l in legs], True), "retenu": bool(c.get("retenu"))})
    used = {l["match_id"] for c in kept for l in c["jambes"]} | {s["match_id"] for s in selections}
    free = sorted((p for p in picks.values() if p.get("pari") and not p.get("fige") and p["match_id"] not in used),
                  key=lambda p: (p["confiance"], p["cote"]), reverse=True)
    fresh = []
    while free:
        first = free.pop(0)
        mate = next((p for p in free if not COMBO_MAX_ODDS or first["cote"] * p["cote"] <= COMBO_MAX_ODDS), None)
        if mate:
            free.remove(mate)
            fresh.append({**combo([first, mate], False), "retenu": False})
    room = COMBOS_PER_DAY - sum(1 for c in kept if c["retenu"])
    for c in fresh[:max(0, room)]:
        c["retenu"] = True
    return kept + fresh


def needs_effort(market: str, selection: str, line: float | None) -> str | None:
    """"home" or "away" when the bet needs that team to win, to cover a handicap of 0 or less (draw no bet
    included) or to score 2 goals or more; None for every other bet (double chance, match goals, both teams score,
    a team's under, clean sheet...)."""
    side = selection if selection in ("home", "away") else None
    if market in ("1x2", "dnb"):
        return side
    if market == "asian_handicap" and side and line is not None:
        return side if (line if side == "home" else -line) <= 0 else None
    if market == "euro_handicap" and side and line is not None:  # the line is the home team's
        return side if (line <= 0 if side == "home" else line >= 0) else None
    if market in ("result_total", "result_btts"):
        res = selection.split("_")[0]
        return res if res in ("home", "away") else None
    if market in ("win_to_nil_home", "win_to_nil_away") and selection == "yes":
        return market.rsplit("_", 1)[1]
    if market in ("team_total_home", "team_total_away") and selection == "over" and (line or 0) >= 1.5:
        return market.rsplit("_", 1)[1]
    if market in ("exact_goals_home", "exact_goals_away") and int(selection.rstrip("+")) >= 2:
        return market.rsplit("_", 1)[1]
    if market == "exact_score":
        hg, ag = (int(x) for x in selection.split(":"))
        return "home" if hg > ag else "away" if ag > hg else None
    return None


def rotation_block(leg: dict, page: dict[str, dict]) -> dict | None:
    """The next-match entry (contexte.prochain_match) that keeps this bet out of the tickets and ideas, if any."""
    side = leg.get("_exige")
    if not side:
        return None
    ctx = (page.get(leg["match_id"]) or {}).get("contexte") or {}
    nxt = (ctx.get("prochain_match") or {}).get("domicile" if side == "home" else "exterieur") or {}
    return nxt if nxt.get("risque_rotation") == ROTATION_LEVEL else None


def _goal(leg: dict) -> bool:
    """A goal bet (both teams score, numbers of goals, a team's goals): its own rules above."""
    return leg.get("famille") in GOAL_FAMILIES if leg.get("famille") else leg.get("marche") in GOAL_MARKETS.values()


def legs_from_db(db: DB, day, ucl: bool = True, corrections: dict | None = None) -> list[dict]:
    """Every validated-market bet priced today, from the latest run that analysed each match.

    ucl: also the Champions League 1X2 bets, marked `ucl: True` and validated only within the limits above.
    corrections: the daily review's correction of each family of bets (revue.build_revue "corrections"): the model's
    chance and value of each bet are corrected before any rule is applied; the raw ones stay as "proba_brute" and
    "valeur_brute"."""
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.status == "completed", AnalysisRun.kind == "prematch")
                      .order_by(AnalysisRun.started_at))
    latest: dict[str, tuple[AnalysisRun, dict]] = {}
    # Markets beyond 1X2 / double chance / draw no bet: with The Odds API their odds were only fetched 45 min before,
    # so the last run that has them is kept for them.
    goals_run: dict[str, int] = {}
    for run in runs:
        if (run.competition in EUROPEAN_CUPS and not ucl) or journee(_aware(run.started_at)) != day:
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
        paper = run.competition in EUROPEAN_CUPS  # Champions League rules, Europa League too (owner 2026-10-15)
        if paper:
            enabled = ("1x2",)
        # Goal markets enter the tickets at the owner's request (2026-10-11). Validated (fragile): over/under
        # 1.5 and 3.5 when priced by the enriched goals model (docs/11-backtest-buts.md); btts and 2.5 stay
        # "non validé". Their odds exist only after a pass run with --goals (45 min before kick-off).
        goals = cfg.get("info_markets", ("totals", "btts"))
        home, _, away = f["match"].partition(" - ")
        ko = datetime.fromisoformat(f["kickoff"])
        rows = list(db.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id,
                                                          CandidateRow.match_id == match_id)))
        if goals_run.get(match_id, run.id) != run.id:
            rows = [c for c in rows if c.market in VALIDATED_MARKETS] + list(db.scalars(select(CandidateRow).where(
                CandidateRow.run_id == goals_run[match_id], CandidateRow.match_id == match_id,
                CandidateRow.market.not_in(VALIDATED_MARKETS))))
        div = cfg.get("max_market_divergence", 0.10)
        trial_ok = cfg.get("trial_markets", TRIAL_MARKETS_DEFAULT)
        trials, goal_trials = [], []
        for c in rows:
            if c.odds < COTE_MIN:  # never proposed (owner, 2026-10-22)
                continue
            fix = correct_leg(c.market, c.selection, c.line, c.p_win, c.p_push, c.odds, c.market_fair_prob, corrections)
            p_win, ev, fam = fix["proba"], fix["valeur"], fix["famille"]
            goal = fam in GOAL_FAMILIES
            if c.market in enabled:
                pari, marche = bet_label(c.market, c.selection, home, away), MARKET_NAMES.get(c.market, c.market)
            elif c.market in goals and c.market in GOAL_MARKETS:
                pari, marche = goal_label(c.market, c.selection, c.line), GOAL_MARKETS[c.market]
            elif c.market in trial_ok:
                pari, marche = bet_label(c.market, c.selection, home, away, c.line), MARKET_NAMES.get(c.market, c.market)
            else:
                continue
            valide = (c.market in enabled if c.market not in GOAL_MARKETS
                      else not paper and goal_market_validated(c.market, c.line, f.get("goals_model")))
            if paper and c.market in enabled:  # Champions League result: a win within the odds limits only
                valide = c.selection != "draw" and UCL_ODDS[0] <= c.odds <= UCL_ODDS[1]
            # A de-vigged two-way price estimates the chance of winning when the bet is not refunded.
            p_cmp = fix["p_cmp"]
            if not valide and not (paper and c.market in enabled):
                if goal and not paper:  # goal bets: their own rules (GOAL_* above)
                    if (is_quarter(c.line) or ev <= GOAL_MIN_VALUE or c.market_fair_prob is None
                            or min(p_cmp, c.market_fair_prob) < GOAL_MIN_PROBA or c.odds > TRIAL_MAX_ODDS
                            or abs(p_cmp - c.market_fair_prob) > div):
                        continue
                elif (is_quarter(c.line) or ev < TRIAL_MIN_VALUE or c.market_fair_prob is None
                        or p_win < TRIAL_MIN_PROBA or c.odds > TRIAL_MAX_ODDS
                        or abs(p_cmp - c.market_fair_prob) > div):
                    continue
            leg = {
                "match_id": doc_id(ko, home, away), "match": f["match"],
                "competition": COMPETITION_NAMES.get(run.competition, run.competition), "coup_envoi": ko.isoformat(),
                "pari": pari, "marche": marche,
                "cote": c.odds, "bookmaker": c.bookmaker, "proba": round(p_win, 4),
                "proba_marche": None if c.market_fair_prob is None else round(c.market_fair_prob, 4),
                "valeur": round(ev, 4),
                # goal legs: only over/under 1.5 and 3.5, and only when the enriched goals model priced them
                "valide": valide,
                "_divergence_max": div, "_p_cmp": round(p_cmp, 4),
                **({"_exige": side} if (side := needs_effort(c.market, c.selection, c.line)) else {}),
                **({"famille": fam} if fam else {}),
                **({"ucl": True} if paper else {}),
            }
            if fix["ratio"] != 1.0:  # the review's correction, shown with the raw figures (revue.py)
                leg.update(proba_brute=round(c.p_win, 4), valeur_brute=round(c.ev, 4), correction=fix["ratio"])
            if fix["ratio_marche"] != 1.0 and fix["proba_marche"] is not None:
                leg["proba_marche_corrigee"] = fix["proba_marche"]
            if not leg["valide"] and not (paper and c.market in enabled):
                leg["validation"] = NON_VALIDE
                (goal_trials if goal and not paper else trials).append(leg)
            else:
                legs.append(leg)
        # Anytime goalscorer (owner, 2026-10-15: "buteur Haaland ou Mbappé"): never backtested, so on trial with the
        # same care; the market's chance is 1/odds, margin included (a scorer list has no "no" side to de-vig).
        for r in f.get("buteurs_cotes") or []:
            p, pm = r["proba"], r["proba_marche"]
            if (r["valeur"] < TRIAL_MIN_VALUE or p < TRIAL_MIN_PROBA or r["cote"] > TRIAL_MAX_ODDS
                    or abs(p - pm) > div):
                continue
            trials.append({
                "match_id": doc_id(ko, home, away), "match": f["match"],
                "competition": COMPETITION_NAMES.get(run.competition, run.competition), "coup_envoi": ko.isoformat(),
                "pari": scorer_label(r["joueur"]), "marche": SCORER_MARKET, "cote": r["cote"],
                "bookmaker": r["bookmaker"], "proba": round(p, 4), "proba_marche": round(pm, 4),
                "proba_marche_brute": True, "valeur": round(r["valeur"], 4), "valide": False,
                "validation": NON_VALIDE, "buteur": scorer_leg_info(r), "_divergence_max": div, "_p_cmp": round(p, 4)})
        # the best few unvalidated bets of the match, by value; the goal bets in their own places (owner 2026-10-24)
        legs += sorted(trials, key=lambda l: -l["valeur"])[:TRIAL_PER_MATCH]
        legs += sorted(goal_trials, key=lambda l: -l["valeur"])[:GOAL_PER_MATCH]
    return legs


def _ucl_result(leg: dict) -> bool:
    return bool(leg.get("ucl")) and leg.get("marche") == MARKET_NAMES.get("1x2")


def _trial(leg: dict) -> bool:
    """A bet on a market the backtest has not validated (the Champions League result has its own rules)."""
    return not leg.get("valide", True) and not _ucl_result(leg)


def confidence(leg: dict) -> float:
    """The more cautious of the model's and the market's chances: what the safe tickets are chosen on."""
    pm = leg.get("proba_marche")
    return leg["proba"] if pm is None else min(leg.get("_p_cmp", leg["proba"]), pm)


def cautious_proba(leg: dict) -> float:
    """The model's chance, but never more than the selection engine's divergence limit above the market's: what a
    stake is sized on, so a model far more confident than the bookmakers does not size it alone."""
    pm = leg.get("proba_marche")
    return leg["proba"] if pm is None else min(leg["proba"], pm + leg.get("_divergence_max", 0.10))


# A model far above the market is trusted only on a bet the market itself makes the favourite: Aston Villa (72 % for the
# model, 57 % for the market) or Liverpool (84 % / 69 %) on 2026-10-14; a 60 % / 45 % coin flip still means the model
# lacks something.
FAVOURITE = 0.50


def agrees(leg: dict) -> bool:
    """The model within the divergence limit of the market, or above it on a bet the market makes the favourite."""
    pm = leg.get("proba_marche")
    if pm is None:
        return True
    p, div = leg.get("_p_cmp", leg["proba"]), leg.get("_divergence_max", 0.10)
    return pm - p <= div and (p - pm <= div or pm >= FAVOURITE)


def _correct(leg: dict) -> bool:
    if _ucl_result(leg) and not leg["valide"]:  # Champions League result: only within the limits above
        return False
    if _trial(leg) and _goal(leg):  # goal bets: a value above 0 is enough (owner, 2026-10-24)
        return leg["valeur"] > GOAL_MIN_VALUE and leg["proba_marche"] is not None and agrees(leg)
    return (leg["valeur"] >= (TRIAL_MIN_VALUE if _trial(leg) else 0) and leg["proba_marche"] is not None
            and agrees(leg))


def _ticket(legs: list[dict], title: str, message: str | None = None) -> dict:
    if not legs:
        return {"titre": title, "jambes": [], "cote_totale": None, "proba_totale": None, "message": message}
    return {"titre": title, "jambes": legs, "cote_totale": round(math.prod(l["cote"] for l in legs), 2),
            "proba_totale": round(math.prod(l["proba"] for l in legs), 4),
            "confiance": round(math.prod(confidence(l) for l in legs), 4), "message": message}


def _model(leg: dict) -> float:
    return leg["proba"]


def _front(legs: list[dict], keep: int = COMBO_PER_MATCH, chance=confidence) -> list[dict]:
    """The bets of each match that no other bet of the same match beats on both chance and odds (a ticket never
    wants a dominated bet), at most `keep` per match spread along that front."""
    by_match: dict[str, list[dict]] = {}
    for l in legs:
        by_match.setdefault(l["match_id"], []).append(l)
    out = []
    for group in by_match.values():
        group = sorted(group, key=lambda l: (-chance(l), -l["cote"]))
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


def _combos(legs: list[dict], min_legs: int, max_legs: int, min_odds: float, min_total: float, min_leg: float,
            chance=confidence):
    pool = _front([l for l in legs if chance(l) >= min_leg and l["cote"] >= TICKET_MIN_LEG_ODDS], chance=chance)
    for n in range(min_legs, max_legs + 1):
        for combo in combinations(pool, n):
            # one bet on trial per ticket at most; the match's goals do not count (owner, 2026-10-15: two "both teams
            # score" make a good ticket), a team's goals neither since 2026-10-24
            if (len({l["match_id"] for l in combo}) == n
                    and sum(_trial(l) and not _goal(l) for l in combo) <= 1
                    and math.prod(l["cote"] for l in combo) > min_odds
                    and math.prod(chance(l) for l in combo) >= min_total):
                yield list(combo)


def _settle_leg(leg: dict, page: dict[str, dict]) -> dict:
    leg = dict(leg)
    doc = page.get(leg["match_id"]) or {}
    if leg.get("buteur"):  # a scorer bet: from the match sheet, refunded when the player did not play
        res = settle_scorer(leg, doc)
        if res:
            leg["resultat"] = res
        return leg
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
    # a team that may rest players tonight for its next match: the bets needing it to win or score are kept out of
    # the tickets and the ideas (docs/15-prochain-match.md); the top 3 odds still show them
    blocked: dict[str, dict] = {}
    for l in open_legs:
        nxt = rotation_block(l, page)
        if nxt:
            blocked.setdefault(nxt.get("equipe") or l["match"], nxt)
    ticket_legs = [l for l in open_legs if not rotation_block(l, page)]
    for team, nxt in blocked.items():
        notes.append((nxt.get("phrase") or f"{team} : risque de rotation élevé.")
                     + f" Les paris qui demandent à {team} de gagner ou de marquer beaucoup sont écartés des idées.")

    if f"{day:%Y-%m-%d}" >= PER_MATCH_FROM:  # one bet per match, the day's top pick among them (owner, 2026-10-27)
        picks, selections = build_picks(legs, page, previous, now, day, started,
                                        lambda l: rotation_block(l, page) is not None)
        n = sum(1 for p in picks.values() if p.get("pari"))
        notes.append(f"{n} match(s) avec une cote retenue sur {len(picks)}." if picks else
                     "Aucun match analysé pour l'instant.")
        return {"jour": f"{day:%Y-%m-%d}", "maj": now.isoformat(), "passage": passage, "message": " ".join(notes),
                "paris_retenus": picks, "selections": selections,
                "combines": build_combos(picks, selections, previous, now, page, started), "regle": PICKS_NOTE,
                "classement_selections": SELECTION_RANK, "avertissement": WARNING_PICKS}

    # Top 3: keep what was shown for matches under way, fill the rest from the matches to come.
    kept = [_settle_leg({**l, "verrouille": True}, page) for l in previous.get("top3") or [] if started(l)]
    fresh = sorted((l for l in open_legs if confidence(l) >= TOP_MIN_PROBA and l["cote"] <= TOP_MAX_ODDS),
                   key=lambda l: -(cautious_proba(l) * l["cote"] - 1))
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
        key, title, min_legs, max_legs, min_odds, min_total, min_leg = rules
        # safe and semi-safe on confidence (owner, 2026-10-15); the fun ticket stays on the model's chances
        chance = _model if key == "fun" else confidence
        if old and old.get("jambes") and (old.get("fige") or any(started(l) for l in old["jambes"])):
            frozen = {**old, "fige": True, "jambes": [{**l, "verrouille": True} if started(l) else l
                                                      for l in old["jambes"]]}
            return _settle_ticket(frozen, page)
        # A bet goes in one ticket only: if it loses, it must not sink the other tickets too (owner, 2026-10-15).
        used = set().union(*taken)
        # any market may make the safe single, if its value is positive and its chance strong (owner, 2026-10-15):
        # 55 % at least on the more cautious of the model and the bookmakers
        candidates = ticket_legs if pool is None else pool
        for fewest in range(min_legs, 0, -1):
            combos = [c for c in _combos(candidates, fewest, max_legs, min_odds, min_total, min_leg, chance)
                      if not {(l["match_id"], l["pari"]) for l in c} & used]
            if combos:
                break
        if combos:
            # The target odds reached with the best chance; the fewer bets the better on a tie.
            best = sorted(max(combos, key=lambda c: (math.prod(chance(l) for l in c), -len(c))),
                          key=lambda l: l["coup_envoi"])
            short = None if len(best) >= min_legs else (
                f"Pas de combiné de {min_legs} paris correct pour l'instant : "
                + ("un seul pari atteint" if len(best) == 1 else f"{len(best)} paris atteignent") + " la cote visée.")
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
        if max_legs == 1:
            return _ticket([], title, (
                f"Pas de {title.lower()} correcte : il faudrait un pari simple à une cote au-dessus de "
                f"{min_odds:.2f}".replace(".", ",") + f", avec au moins {round(max(min_leg, min_total) * 100)} % de "
                "chances et de la valeur" + (" (le plus prudent du modèle et des bookmakers)." if key != "fun" else ".")))
        most = f"{max_legs} paris au plus"
        return _ticket([], title, (
            f"Pas de {title.lower()} correcte : il faudrait une cote totale au-dessus de "
            f"{min_odds:.2f}".replace(".", ",") + f" avec {most}, chacun à {round(min_leg * 100)} % ou plus "
            f"et de valeur positive, pour une chance combinée d'au moins {round(min_total * 100)} %"
            + (" (la plus prudente du modèle et des bookmakers)." if key != "fun" else ".")))

    def bets(ticket: dict) -> set:
        return {(l["match_id"], l["pari"]) for l in ticket["jambes"]}

    # A ticket fixed at an earlier pass keeps its bets: the tickets built before it in this pass must not take
    # them again (2026-10-16: the fun ticket, fixed first, and the semi safe shared "Plus de 1,5 buts").
    locked = {k: bets(t) for k, t in (previous.get("tickets") or {}).items()
              if t and t.get("jambes") and (t.get("fige") or any(started(l) for l in t["jambes"]))}
    # a bonus fixed earlier keeps its bets too: 2026-10-17 the semi safe, still open, took the Lens bet of a fixed
    # bonus fun ticket
    locked_bonus = [bets(b) for group in (previous.get("bonus") or {}).values() for b in group or []
                    if b.get("jambes") and (b.get("fige") or any(started(l) for l in b["jambes"]))]
    tickets: dict[str, dict] = {}
    for rules in TICKETS:
        tickets[rules[0]] = build(rules, (previous.get("tickets") or {}).get(rules[0]),
                                [bets(t) for t in tickets.values()]
                                + [b for k, b in locked.items() if k != rules[0]] + locked_bonus, True)
    # Once an official ticket is fixed, later finds of the same kind are bonus tickets, shown apart: the ones
    # already fixed stay, plus the best new one, until the next pass 45 min before its first match fixes it.
    bonus: dict[str, list[dict]] = {}
    for key, title, *rest in TICKETS:
        # only from the pass after the official ticket was fixed: a bonus is a later find, not a near copy
        if not (tickets[key].get("fige") and ((previous.get("tickets") or {}).get(key) or {}).get("fige")):
            continue
        # bonus tickets are "idées" for "La boîte à idées" tab, never with the official tickets (owner, 2026-10-17)
        rules = (key, title.replace("Top idée", "Idée"), *rest)
        # a bonus shares no bet with the official ticket of its kind: other bets, not a variant of it
        official = bets(tickets[key])
        kept = [build(rules, b, [], False) for b in (previous.get("bonus") or {}).get(key) or []
                if (b.get("fige") or any(started(l) for l in b["jambes"])) and not bets(b) & official]
        # bonus tickets of the kinds built later in this loop count too (2026-10-17: a new bonus semi safe took
        # "Victoire AS Roma" from a fixed bonus fun ticket)
        taken = [bets(t) for t in [*tickets.values(), *kept, *(b for group in bonus.values() for b in group)]]
        taken += locked_bonus
        new = build(rules, None, taken, False,
                    [l for l in ticket_legs if (l["match_id"], l["pari"]) not in official])
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
            "top3": [clean(l) for l in top3], "tickets": tickets, "bonus": bonus, "boite_a_idees": IDEAS_NOTE,
            "avertissement": WARNING}
