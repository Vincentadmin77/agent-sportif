"""Documents for the results page (claude.ai artifact, collection "matchs").

One document per match of the day, in French, built from stored runs: the
3 h and 45 min passes, their selections or the reasons nothing was selected,
the market movement between passes, then the live score and the settled
result. Pure functions over the database rows and the page's current
documents, so a pass never erases what an earlier pass wrote.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.connectors.teams import normalise
from sportai.db.models import AnalysisRun, CandidateRow, Selection
from sportai.services.prematch import LOCAL_TZ

COMPETITION_NAMES = {"FR1": "Ligue 1", "EN1": "Premier League", "ES1": "LaLiga", "DE1": "Bundesliga",
                     "IT1": "Serie A", "UCL": "Ligue des champions", "BR1": "Brasileirão"}
MARKET_NAMES = {"1x2": "Résultat du match", "double_chance": "Double chance", "dnb": "Remboursé si nul"}
MOVE_KIND = {"confirmation": "confirme", "against": "contre", "stable": "stable"}
GOAL_WARNING = ("Marché non validé : le test sur 10 000 matchs passés n'a pas montré d'avantage du modèle sur "
                "ce marché. Chiffres donnés pour information, jamais proposés comme sélection.")
SCORER_NOTE = ("Analyse buteur indisponible : aucun bookmaker français ne propose ce marché chez notre fournisseur "
               "de cotes, et les données joueurs (buts, tirs, temps de jeu, composition) demandent l'offre payante "
               "d'API-Football.")
SKIP_REASONS = {
    "insufficient match history for one team": "Historique insuffisant pour une des deux équipes (souvent un promu).",
    "already kicked off": "Le match avait déjà commencé au moment du passage.",
    "no odds observed": "Aucune cote disponible chez les bookmakers français.",
}


def doc_id(kickoff: datetime, home: str, away: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{normalise(home)} {normalise(away)}").strip("-")
    return f"{kickoff.astimezone(LOCAL_TZ):%Y-%m-%d}-{slug}"


def pass_name(started_at: datetime, kickoff: datetime) -> str:
    """Which of the two passes (3 h or 45 min before kick-off) a run belongs to."""
    return "h3" if kickoff - started_at >= timedelta(minutes=105) else "m45"


def bet_label(market: str, selection: str, home: str, away: str) -> str:
    return {
        ("1x2", "home"): f"Victoire {home}", ("1x2", "away"): f"Victoire {away}", ("1x2", "draw"): "Match nul",
        ("double_chance", "1X"): f"{home} ou nul", ("double_chance", "X2"): f"{away} ou nul",
        ("double_chance", "12"): "Pas de match nul",
        ("dnb", "home"): f"{home} (remboursé si nul)", ("dnb", "away"): f"{away} (remboursé si nul)",
    }.get((market, selection), f"{market} {selection}")


def _pct(x: float) -> str:
    return f"{x * 100:+.1f} %".replace(".", ",")


def goal_label(market: str, selection: str, line: float | None) -> str:
    if market == "btts":
        return "Les deux équipes marquent : " + ("oui" if selection == "yes" else "non")
    side = "Plus de" if selection == "over" else "Moins de"
    return f"{side} {line:g} buts".replace(".", ",")


def goals_block(f: dict, cands: list[CandidateRow], info_markets) -> dict | None:
    """Model probabilities for "both teams to score" and the number of goals, with the best odds when
    the bookmakers offer them. Information only (markets not validated by the backtest)."""
    if not f.get("expected_goals"):
        return None
    priced = {(c.market, c.selection, c.line): c for c in cands if c.market in info_markets}
    rows = []
    for label, p in (f.get("probabilities") or {}).items():
        market, _, rest = label.partition(":")
        if market not in info_markets:
            continue
        selection, _, line = rest.partition("@")
        line_v = float(line) if line else None
        c = priced.get((market, selection, line_v))
        row = {"pari": goal_label(market, selection, line_v), "proba": p}
        if c is not None:
            row.update(cote=c.odds, bookmaker=c.bookmaker, valeur=round(c.ev, 4))
            if c.market_fair_prob is not None:
                row["proba_marche"] = round(c.market_fair_prob, 4)
        rows.append(row)
    lam, mu = f["expected_goals"]
    return {"buts_attendus": round(lam + mu, 2), "marches": rows, "avertissement": GOAL_WARNING}


def rejection_text(c: CandidateRow, home: str, away: str, cfg: dict) -> str | None:
    """Why one bet was not kept, in plain French; None for markets switched off."""
    if c.market not in cfg.get("enabled_markets", ()):
        return None
    bet = f"{bet_label(c.market, c.selection, home, away)} à {c.odds:.2f}".replace(".", ",")
    reasons = " ".join(c.reasons)
    if "divergence" in reasons and c.market_fair_prob is not None:
        why = (f"le modèle donne {c.p_win * 100:.0f} %, le marché {c.market_fair_prob * 100:.0f} % : écart trop grand, "
               "il manque sans doute une information (blessure, composition)")
    elif "outside" in reasons:
        why = "cote hors de la plage retenue"
    elif c.ev < cfg.get("min_ev", 0.03):
        why = f"cote trop basse, avantage {_pct(c.ev)}"
    else:
        why = f"avantage pas assez sûr (version prudente {_pct(c.ev_low)})"
    return f"{bet} : {why}."


def build_match_docs(db: DB, run: AnalysisRun, current: dict[str, dict]) -> dict[str, dict]:
    """Documents to write for one completed pre-match run, merged into the page's current documents."""
    out: dict[str, dict] = {}
    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
    today = started.astimezone(LOCAL_TZ).date()
    cands = list(db.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id)))
    sels = {s.candidate_id: s for s in db.scalars(select(Selection).where(Selection.run_id == run.id))}
    for f in run.fixtures or []:
        ko = datetime.fromisoformat(f["kickoff"])
        if ko.astimezone(LOCAL_TZ).date() != today:
            continue
        home, _, away = f["match"].partition(" - ")
        key = doc_id(ko, home, away)
        reason = f.get("skipped_reason")
        if reason and (reason.startswith("status") or reason == "already kicked off"):
            # Under way: keep what the earlier passes published, only mark it live.
            if key in current and current[key].get("statut", "a_venir") == "a_venir":
                out[key] = {**current[key], "statut": "en_direct"}
            continue
        doc = dict(current.get(key) or {})
        doc.update(jour=f"{today:%Y-%m-%d}", coup_envoi=ko.isoformat(), competition=COMPETITION_NAMES.get(run.competition, run.competition),
                   domicile=home, exterieur=away)
        doc.setdefault("statut", "a_venir")
        if reason and reason.startswith("more than"):
            out[key] = doc  # listed, analysed at a later pass
            continue
        which = pass_name(started, ko)
        doc["passages"] = {**(doc.get("passages") or {}), which: started.isoformat()}
        if reason:
            if not doc.get("selections"):
                doc["verdict"] = "non_analyse"
                doc["rejets"] = [SKIP_REASONS.get(reason, reason)]
            out[key] = doc
            continue
        mine = [c for c in cands if c.match_id == f["match_id"]]
        moves = f.get("market_moves") or {}
        previous = {s.get("pari"): s for s in doc.get("selections") or []}
        selections, rejets = [], []
        for c in sorted(mine, key=lambda c: -c.score):
            label = bet_label(c.market, c.selection, home, away)
            if c.id in sels:
                s = dict(previous.get(label) or {})
                s.update(pari=label, marche=MARKET_NAMES.get(c.market, c.market), proba=round(c.p_win, 4),
                         valeur=round(c.ev, 4), bookmaker=c.bookmaker, raison=sels[c.id].rationale)
                s["cote_3h" if which == "h3" else "cote_45"] = c.odds
                mv = moves.get(f"{c.market}:{c.selection}" + (f"@{c.line:g}" if c.line is not None else ""))
                if mv:
                    s["mouvement"] = MOVE_KIND[mv["kind"]]
                    s["mouvement_detail"] = (f"Marché {mv['before'] * 100:.0f} % au passage précédent, "
                                             f"{mv['after'] * 100:.0f} % maintenant (modèle {c.p_win * 100:.0f} %).")
                selections.append(s)
            else:
                text = rejection_text(c, home, away, run.config or {})
                if text:
                    rejets.append(text)
        for label in set(previous) - {s["pari"] for s in selections}:
            rejets.insert(0, f"{label} : retenue à H−3 mais plus à H−45.")
        doc["selections"] = selections
        doc["rejets"] = rejets
        goals = goals_block(f, mine, (run.config or {}).get("info_markets", ("totals", "btts")))
        if goals:
            doc["buts"] = goals
        doc["buteurs"] = {"raison": SCORER_NOTE}
        doc["verdict"] = "selection" if selections else "aucune"
        out[key] = doc
    return out


def settle(bet: str, home: str, away: str, hg: int, ag: int) -> str:
    if bet == f"Victoire {home}":
        return "gagne" if hg > ag else "perdu"
    if bet == f"Victoire {away}":
        return "gagne" if ag > hg else "perdu"
    if bet == "Match nul":
        return "gagne" if hg == ag else "perdu"
    if bet == f"{home} ou nul":
        return "gagne" if hg >= ag else "perdu"
    if bet == f"{away} ou nul":
        return "gagne" if ag >= hg else "perdu"
    if bet == "Pas de match nul":
        return "gagne" if hg != ag else "perdu"
    if bet.endswith("(remboursé si nul)"):
        if hg == ag:
            return "rembourse"
        return "gagne" if (hg > ag) == bet.startswith(home) else "perdu"
    raise ValueError(f"cannot settle {bet!r}")


def apply_scores(events: list[dict], current: dict[str, dict], now: datetime) -> dict[str, dict]:
    """Live score and final result from The Odds API /scores, for the page's documents."""
    out = {}
    for ev in events:
        ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        key = doc_id(ko, ev["home_team"], ev["away_team"])
        if key not in current or not ev.get("scores"):
            continue
        goals = {normalise(s["name"]): int(s["score"]) for s in ev["scores"]}
        hg, ag = goals.get(normalise(ev["home_team"])), goals.get(normalise(ev["away_team"]))
        if hg is None or ag is None:
            continue
        doc = dict(current[key])
        doc["score"] = f"{hg} - {ag}"
        doc["passages"] = {**(doc.get("passages") or {}), "direct": now.isoformat()}
        if ev.get("completed"):
            doc["statut"] = "termine"
            doc["selections"] = [{**s, "resultat": settle(s["pari"], doc["domicile"], doc["exterieur"], hg, ag)}
                                 for s in doc.get("selections") or []]
        else:
            doc["statut"] = "en_direct"
        out[key] = doc
    return out
