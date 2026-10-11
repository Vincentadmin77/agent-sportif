"""Documents for the results page (claude.ai artifact, collection "matchs").

One document per match of the day, in French, built from stored runs: the
3 h and 45 min passes, their selections or the reasons nothing was selected,
the market movement between passes, then the live score and the settled
result. Pure functions over the database rows and the page's current
documents, so a pass never erases what an earlier pass wrote.
"""

from __future__ import annotations

import math
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
GOAL_WARNING = ("Marchés non validés pour ce match : probabilités du modèle Dixon-Coles seul, qui ne fait pas mieux "
                "qu'une fréquence moyenne sur ces marchés. Chiffres donnés pour information, jamais proposés comme "
                "sélection.")
GOAL_WARNING_ENRICHED = ("Modèle de buts enrichi (forme, xG, tirs). Plus/moins de 1,5 et de 3,5 buts : validés, mais "
                         "de justesse (fragile), par le test hors échantillon sur 2 583 matchs. Les deux équipes "
                         "marquent et plus/moins de 2,5 buts : non validés (pas d'avantage prouvé sur une fréquence "
                         "moyenne). Faire mieux qu'une moyenne n'est pas battre les bookmakers.")
# Goal markets validated by docs/11-backtest-buts.md, only when priced by the enriched goals model.
VALIDATED_GOAL_LINES = (1.5, 3.5)


def goal_market_validated(market: str, line: float | None, goals_model: str | None) -> bool:
    return (market == "totals" and line in VALIDATED_GOAL_LINES
            and bool(goals_model) and goals_model.startswith("goals-logit"))
SCORER_NOTE = ("Compositions officielles et chances de marquer de chaque titulaire publiées au passage 45 min avant "
               "le match (les compositions sortent environ 1 h avant). Aucun bookmaker français ne propose le marché "
               "buteur chez notre fournisseur de cotes : pas de cote à comparer.")
GOAL_ODDS_LATER = ("Cotes de ce marché chargées au passage 45 min avant le match seulement : chaque match coûte 2 crédits "
                   "sur notre forfait de cotes (500 par mois).")
GOAL_ODDS_NONE = "Aucun bookmaker français ne propose ces cotes pour ce match chez notre fournisseur."
SKIP_REASONS = {
    "insufficient match history for one team": "Historique insuffisant pour une des deux équipes (souvent un promu).",
    "already kicked off": "Le match avait déjà commencé au moment du passage.",
    "no odds observed": "Aucune cote disponible chez les bookmakers français.",
}


UCL_INFO = ("Ligue des champions : estimation du modèle entraîné sur les cinq grands championnats et la coupe "
            "d'Europe ensemble. Comparer des clubs de championnats différents n'est pas encore validé par un test sur "
            "matchs passés : information seulement, aucune sélection.")


def ucl_model_line(f: dict, cands: list[CandidateRow], home: str, away: str) -> str | None:
    p = f.get("probabilities") or {}
    if "1x2:home" not in p:
        return None
    line = (f"Modèle : {home} {_whole(p['1x2:home'])}, nul {_whole(p.get('1x2:draw'))}, "
            f"{away} {_whole(p.get('1x2:away'))}")
    market = {c.selection: c.market_fair_prob for c in cands if c.market == "1x2" and c.market_fair_prob is not None}
    if {"home", "draw", "away"} <= market.keys():
        line += (f" ; bookmakers : {home} {_whole(market['home'])}, nul {_whole(market['draw'])}, "
                 f"{away} {_whole(market['away'])}")
    return line + "."


def _whole(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.0f} %"


def doc_id(kickoff: datetime, home: str, away: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{normalise(home)} {normalise(away)}").strip("-")
    return f"{kickoff.astimezone(LOCAL_TZ):%Y-%m-%d}-{slug}"


def pass_name(started_at: datetime, kickoff: datetime) -> str:
    """Which pass a run belongs to: the 8 am pass ("matin"), 3 h or 45 min before kick-off."""
    if kickoff - started_at >= timedelta(hours=3, minutes=30):
        return "matin"
    return "h3" if kickoff - started_at >= timedelta(minutes=105) else "m45"


ODDS_FIELD = {"matin": "cote_matin", "h3": "cote_3h", "m45": "cote_45"}


def scenario(f: dict, home: str, away: str) -> str | None:
    """How the match should unfold according to the model, without picking a winner: goals expected from
    each side (home advantage of the league included) and the chance that each side scores at least once."""
    if not f.get("expected_goals"):
        return None
    lam, mu = f["expected_goals"]
    p_h, p_a = 1 - math.exp(-lam), 1 - math.exp(-mu)
    total = lam + mu
    tempo = "match ouvert" if total >= 3.0 else "match fermé" if total < 2.3 else "match équilibré en buts"
    n = lambda x: f"{x:.1f}".replace(".", ",")
    text = (f"Buts attendus : {home} {n(lam)}, {away} {n(mu)} ({tempo}, {n(total)} au total). "
            f"Chance de marquer au moins un but : {home} {p_h * 100:.0f} %, {away} {p_a * 100:.0f} %. ")
    weaker, p_weak = (home, p_h) if lam < mu else (away, p_a)
    if abs(lam - mu) >= 0.4 and p_weak >= 0.6:
        side = "à domicile" if weaker == home else "à l'extérieur"
        text += f"{weaker}, moins fort sur le papier, garde une bonne chance de marquer ({side}). "
    text += "L'avantage du terrain du championnat est compté. Estimation du modèle, pas un pari."
    return text


def likely_score(f: dict) -> dict | None:
    """The most likely final score (Dixon-Coles); runs stored before it existed fall back to Poisson."""
    if f.get("likely_score"):
        hg, ag, p = f["likely_score"]
    elif f.get("expected_goals"):
        from sportai.engine.football.dixon_coles import score_matrix

        m = score_matrix(*f["expected_goals"], 0.0)
        hg, ag = (int(x) for x in divmod(int(m.argmax()), m.shape[1]))
        p = round(float(m[hg, ag]), 4)
    else:
        return None
    return {"score": f"{hg} - {ag}", "domicile": hg, "exterieur": ag, "proba": p}


def settle_score(doc: dict, hg: int, ag: int) -> None:
    """Mark the estimated final score right ("reussi") or wrong ("rate") once the match is over."""
    if doc.get("score_estime"):
        est = doc["score_estime"]
        doc["score_estime"] = {**est, "resultat": "reussi" if (est["domicile"], est["exterieur"]) == (hg, ag) else "rate"}


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


def goals_block(f: dict, cands: list[CandidateRow], info_markets, odds_asked: bool = True) -> dict | None:
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
        row = {"pari": goal_label(market, selection, line_v), "proba": p,
               "valide": goal_market_validated(market, line_v, f.get("goals_model"))}
        if c is not None:
            row.update(cote=c.odds, bookmaker=c.bookmaker, valeur=round(c.ev, 4))
            if c.market_fair_prob is not None:
                row["proba_marche"] = round(c.market_fair_prob, 4)
        rows.append(row)
    lam, mu = f["expected_goals"]
    enriched = goal_market_validated("totals", 1.5, f.get("goals_model"))
    block = {"buts_attendus": round(lam + mu, 2), "marches": rows,
             "avertissement": GOAL_WARNING_ENRICHED if enriched else GOAL_WARNING,
             "modele": f.get("goals_model") or "dixon-coles"}
    if not any("cote" in r for r in rows):  # say why the odds columns are empty instead of dashes only
        block["cotes_info"] = GOAL_ODDS_NONE if odds_asked else GOAL_ODDS_LATER
    return block


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
        doc.update(jour=f"{today:%Y-%m-%d}", coup_envoi=ko.isoformat(), competition=COMPETITION_NAMES.get(run.competition, run.competition))
        doc.setdefault("domicile", home)  # keep the spelling first published
        doc.setdefault("exterieur", away)
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
                s[ODDS_FIELD[which]] = c.odds
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
        if run.competition == "UCL":
            rejets = [UCL_INFO] + [t for t in (ucl_model_line(f, mine, home, away),) if t]
        doc["selections"] = selections
        doc["rejets"] = rejets
        info = (run.config or {}).get("info_markets", ("totals", "btts"))
        goals = goals_block(f, mine, info, odds_asked=any(c.market in info for c in cands))
        if goals:
            doc["buts"] = goals
        if not (doc.get("buteurs") or {}).get("joueurs"):  # keep the line-up estimates once published
            doc["buteurs"] = {"raison": SCORER_NOTE}
        est = likely_score(f)
        if est:
            doc["score_estime"] = est
        p = f.get("probabilities") or {}
        if {"1x2:home", "1x2:draw", "1x2:away"} <= p.keys():
            doc["proba_1x2"] = {"domicile": p["1x2:home"], "nul": p["1x2:draw"], "exterieur": p["1x2:away"]}
        text = scenario(f, doc["domicile"], doc["exterieur"])
        if text:
            doc["scenario"] = text
        doc["verdict"] = "selection" if selections else "aucune"
        out[key] = doc
    return out


def settle(bet: str, home: str, away: str, hg: int, ag: int) -> str:
    if bet.startswith("Les deux équipes marquent : "):
        return "gagne" if (hg > 0 and ag > 0) == bet.endswith("oui") else "perdu"
    m = re.fullmatch(r"(Plus|Moins) de ([0-9]+(?:,5)?) buts", bet)
    if m:
        line = float(m.group(2).replace(",", "."))
        if hg + ag == line:
            return "rembourse"
        return "gagne" if (hg + ag > line) == (m.group(1) == "Plus") else "perdu"
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
            settle_score(doc, hg, ag)
        else:
            doc["statut"] = "en_direct"
        out[key] = doc
    return out
