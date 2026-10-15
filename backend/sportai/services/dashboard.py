"""Documents for the results page (claude.ai artifact, collection "matchs").

One document per match of the day, in French, built from stored runs: the
3 h and 45 min passes, their selections or the reasons nothing was selected,
the market movement between passes, then the live score and the settled
result. Pure functions over the database rows and the page's current
documents, so a pass never erases what an earlier pass wrote.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from sportai.connectors.teams import normalise, same_team
from sportai.db.models import AnalysisRun, CandidateRow, OddsObservation, Selection
from sportai.engine.football.scorers import MARKET_NOTE as SCORER_MARKET_NOTE
from sportai.services.lineups import scenario_with_squads
from sportai.services.position import position
from sportai.services.prematch import LOCAL_TZ, journee

COMPETITION_NAMES = {"FR1": "Ligue 1", "EN1": "Premier League", "ES1": "LaLiga", "DE1": "Bundesliga",
                     "IT1": "Serie A", "UCL": "Ligue des champions", "UEL": "Ligue Europa", "BR1": "Brasileirão"}
MARKET_NAMES = {"1x2": "Résultat du match", "double_chance": "Double chance", "dnb": "Remboursé si nul",
                "totals": "Nombre de buts", "btts": "Les deux équipes marquent",
                "team_total_home": "Buts de l'équipe à domicile", "team_total_away": "Buts de l'équipe à l'extérieur",
                "asian_handicap": "Handicap asiatique", "euro_handicap": "Handicap européen",
                "result_total": "Résultat et nombre de buts", "result_btts": "Résultat et les deux équipes marquent",
                "total_btts": "Nombre de buts et les deux équipes marquent",
                "clean_sheet_home": "Domicile sans but encaissé", "clean_sheet_away": "Extérieur sans but encaissé",
                "win_to_nil_home": "Domicile gagne sans encaisser", "win_to_nil_away": "Extérieur gagne sans encaisser",
                "odd_even": "Buts pair ou impair", "odd_even_home": "Buts du domicile pair ou impair",
                "odd_even_away": "Buts de l'extérieur pair ou impair", "exact_score": "Score exact",
                "exact_goals": "Nombre exact de buts", "exact_goals_home": "Nombre exact de buts du domicile",
                "exact_goals_away": "Nombre exact de buts de l'extérieur", "goal_range": "Tranche de buts",
                "scoring_draw": "Match nul avec buts"}
NON_VALIDE = "non validé"
ALL_MARKETS_NOTE = ("Toutes les cotes du fournisseur (API-Football, tous ses bookmakers, meilleure cote retenue) : "
                    "chaque pari qui se décide sur le score final est chiffré par le modèle. Seuls résultat du match, "
                    "double chance et remboursé si nul sont validés par le backtest ; les autres sont « non validés » : "
                    "estimation à manier avec prudence, retenue seulement avec des seuils relevés et des mises réduites. "
                    "Buteur à tout moment : chiffré par le modèle buteur (non validé). Les paris de mi-temps, corners, "
                    "cartons, premier buteur, minutes... ne se déduisent pas du score final : listés avec leurs cotes, "
                    "« non analysés ».")
MAX_MARKET_ROWS = 150
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
               "le match (les compositions sortent environ 1 h avant). Cotes « buteur à tout moment » des bookmakers "
               "d'API-Football chiffrées par le modèle quand elles existent (marché non validé).")
SCORER_MARKET = "Buteur"
GOAL_ODDS_LATER = ("Cotes de ce marché chargées au passage 45 min avant le match seulement : chaque match coûte 2 crédits "
                   "sur notre forfait de cotes (500 par mois).")
GOAL_ODDS_NONE = "Aucun bookmaker français ne propose ces cotes pour ce match chez notre fournisseur."
SKIP_REASONS = {
    "insufficient match history for one team": "Historique insuffisant pour une des deux équipes (souvent un promu).",
    "already kicked off": "Le match avait déjà commencé au moment du passage.",
    "no odds observed": "Aucune cote disponible chez les bookmakers français.",
}


UCL_INFO = ("Ligue des champions : estimation du modèle entraîné ensemble sur 30 championnats européens et "
            "les trois coupes d'Europe, calibré sur cinq saisons de Ligue des champions. Pas encore d'historique face aux "
            "cotes : il entre dans les tickets avec prudence (victoire de 1,15 à 2,50, autres marchés « non validé », "
            "mise divisée par deux).")


UEL_INFO = ("Ligue Europa : même modèle que la Ligue des champions (30 championnats européens et les trois coupes "
            "d'Europe ensemble), avec le réglage mesuré sur la Ligue des champions, pas encore vérifié sur la Ligue "
            "Europa. Mêmes règles prudentes dans les tickets (victoire de 1,15 à 2,50, autres marchés « non validé », "
            "mise divisée par deux).")


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
    return f"{journee(kickoff):%Y-%m-%d}-{slug}"


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


SHOTS_FILE = Path(os.getenv("SHOTS_FILE", "/mnt/project-files/agent-sportif/data/tirs-equipes-2026.json"))
SHOTS_NOTE = ("Tirs totaux par match cette saison (API-Football), à domicile pour l'équipe qui reçoit et à l'extérieur "
              "pour l'autre ; l'écart-type mesure la régularité. Pour information : le test du 11/10 montre que les tirs "
              "n'améliorent pas la prévision des buts, et aucun bookmaker de notre fournisseur ne propose ce marché.")


def shots_block(competition: str, home: str, away: str, table: dict | None = None) -> dict | None:
    """Each team's average total shots for and against (home team at home, away team away), when known."""
    if table is None:
        try:
            table = json.loads(SHOTS_FILE.read_text())
        except (OSError, ValueError):
            return None
    teams = (table.get("leagues") or {}).get(competition) or {}

    def find(name: str) -> dict | None:
        return next((v for k, v in teams.items() if same_team(k, name)), None)

    h, a = find(home), find(away)
    if not h or not a:
        return None

    def side(t: dict, venue: str) -> dict:
        f, c = t[f"{venue}_for"], t[f"{venue}_against"]
        return {"pour": f["mean"], "contre": c["mean"], "ecart_type_pour": f["sd"], "matchs": f["n"]}

    return {"domicile": side(h, "home"), "exterieur": side(a, "away"), "maj": table.get("snapshot"), "note": SHOTS_NOTE}


def likely_score(f: dict) -> dict | None:
    """The estimated final score: the most likely outcome (home win, draw, away win), then the most likely score
    inside it (sportai.engine.football.markets.likely_score). Runs stored before 2026-10-11 kept the single most
    likely exact score, often a 1-1 even with a clear favourite: when that score disagrees with the run's own 1X2
    probabilities, it is recomputed from the expected goals (Poisson). Older runs without a score do the same."""
    from sportai.engine.football.dixon_coles import score_matrix
    from sportai.engine.football.markets import likely_score as decide

    p1x2 = [(f.get("probabilities") or {}).get(f"1x2:{s}") for s in ("home", "draw", "away")]
    best = max(range(3), key=lambda k: (p1x2[k], k != 1)) if None not in p1x2 else None
    stored = f.get("likely_score")
    if stored and (best is None or outcome(stored[0], stored[1]) == ("domicile", "nul", "exterieur")[best]):
        hg, ag, p = stored
    elif f.get("expected_goals"):
        m = score_matrix(*f["expected_goals"], 0.0)
        if best is not None:  # the run's own outcome probabilities (Dixon-Coles, with its low-score correction) decide
            i, j = np.indices(m.shape)
            m = np.where((i > j, i == j, i < j)[best], m, 0.0)
        hg, ag, p = decide(m)
    elif stored:
        hg, ag, p = stored
    else:
        return None
    return {"score": f"{hg} - {ag}", "domicile": hg, "exterieur": ag, "proba": p}


def outcome(hg: int, ag: int) -> str:
    return "domicile" if hg > ag else "exterieur" if ag > hg else "nul"


def settle_score(doc: dict, hg: int, ag: int) -> None:
    """Judge the estimated final score on the outcome (home win, draw, away win), not the exact score (owner,
    2026-10-11: estimated 1-1, final 0-0 is a draw called right). The exact score is recorded apart."""
    if doc.get("score_estime"):
        est = doc["score_estime"]
        right = outcome(est["domicile"], est["exterieur"]) == outcome(hg, ag)
        doc["score_estime"] = {**est, "resultat": "reussi" if right else "rate",
                               "score_exact": (est["domicile"], est["exterieur"]) == (hg, ag)}


def _n(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _signed(x: float) -> str:
    return ("+" if x > 0 else "") + _n(x)


def _goals(n: int) -> str:
    return f"{n} but" + ("s" if n > 1 else "")


def bet_label(market: str, selection: str, home: str, away: str, line: float | None = None) -> str:
    """The bet in plain French, as shown on the page and settled from it (settle)."""
    side = {"home": home, "away": away}
    if market in ("totals", "btts"):
        return goal_label(market, selection, line)
    if market in ("team_total_home", "team_total_away"):
        team = home if market.endswith("home") else away
        return f"{team} : {'plus' if selection == 'over' else 'moins'} de {_n(line)} buts"
    if market == "asian_handicap":
        return f"{side[selection]} handicap asiatique {_signed(line if selection == 'home' else -line)}"
    if market == "euro_handicap":
        what = {"home": f"victoire {home}", "draw": "nul", "away": f"victoire {away}"}[selection]
        return f"Handicap {home} {_signed(line)} : {what}"
    if market in ("result_total", "result_btts"):
        res, rest = selection.split("_")
        first = {"home": f"Victoire {home}", "draw": "Match nul", "away": f"Victoire {away}"}[res]
        if market == "result_total":
            return f"{first} et {'plus' if rest == 'over' else 'moins'} de {_n(line)} buts"
        return f"{first} et les deux équipes marquent : {'oui' if rest == 'yes' else 'non'}"
    if market == "total_btts":
        tot, yn = selection.split("_")
        return (f"{'Plus' if tot == 'over' else 'Moins'} de {_n(line)} buts et les deux équipes marquent : "
                f"{'oui' if yn == 'yes' else 'non'}")
    if market in ("clean_sheet_home", "clean_sheet_away"):
        team = home if market.endswith("home") else away
        return f"{team} ne prend pas de but : {'oui' if selection == 'yes' else 'non'}"
    if market in ("win_to_nil_home", "win_to_nil_away"):
        team = home if market.endswith("home") else away
        return f"{team} gagne sans prendre de but : {'oui' if selection == 'yes' else 'non'}"
    if market in ("odd_even", "odd_even_home", "odd_even_away"):
        whose = {"odd_even": "", "odd_even_home": f" de {home}", "odd_even_away": f" de {away}"}[market]
        return f"Nombre de buts{whose} {'impair' if selection == 'odd' else 'pair'}"
    if market == "exact_score":
        hg, ag = selection.split(":")
        return f"Score exact {hg} - {ag}"
    if market in ("exact_goals", "exact_goals_home", "exact_goals_away"):
        n = int(selection.rstrip("+"))
        what = f"{_goals(n)} ou plus" if selection.endswith("+") else f"exactement {_goals(n)}"
        if market == "exact_goals":
            return f"{what[0].upper()}{what[1:]} dans le match"
        return f"{home if market.endswith('home') else away} marque {what}"
    if market == "goal_range":
        return {"0-1": "0 ou 1 but dans le match", "2-3": "2 ou 3 buts dans le match",
                "4+": "4 buts ou plus dans le match"}[selection]
    if market == "scoring_draw":
        return f"Match nul avec buts : {'oui' if selection == 'yes' else 'non'}"
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


def _label_keys(home: str, away: str) -> dict:
    """Every bet label the page can show for this match, with its market key: settles the markets beyond 1X2."""
    from sportai.engine.football.markets import MarketKey

    cached = _LABELS.get((home, away))
    if cached is not None:
        return cached
    lines = [x / 4 for x in range(-40, 41)]
    half = [x + 0.5 for x in range(0, 10)]
    keys = []
    for m in ("totals", "team_total_home", "team_total_away"):
        keys += [MarketKey(m, s, ln) for ln in lines if ln > 0 for s in ("over", "under")]
    keys += [MarketKey("asian_handicap", s, ln) for ln in lines for s in ("home", "away")]
    keys += [MarketKey("euro_handicap", s, float(ln)) for ln in range(-6, 7) for s in ("home", "draw", "away")]
    keys += [MarketKey("result_total", f"{r}_{t}", ln) for ln in half for r in ("home", "draw", "away")
             for t in ("over", "under")]
    keys += [MarketKey("total_btts", f"{t}_{y}", ln) for ln in half for t in ("over", "under") for y in ("yes", "no")]
    keys += [MarketKey("result_btts", f"{r}_{y}") for r in ("home", "draw", "away") for y in ("yes", "no")]
    keys += [MarketKey(m, s) for m in ("btts", "clean_sheet_home", "clean_sheet_away", "win_to_nil_home",
                                         "win_to_nil_away", "scoring_draw") for s in ("yes", "no")]
    keys += [MarketKey(m, s) for m in ("odd_even", "odd_even_home", "odd_even_away") for s in ("odd", "even")]
    keys += [MarketKey("exact_score", f"{h}:{a}") for h in range(11) for a in range(11)]
    keys += [MarketKey(m, f"{n}{p}") for m in ("exact_goals", "exact_goals_home", "exact_goals_away")
             for n in range(11) for p in ("", "+")]
    keys += [MarketKey("goal_range", s) for s in ("0-1", "2-3", "4+")]
    out = {bet_label(k.market, k.selection, home, away, k.line): k for k in keys}
    if len(_LABELS) > 512:
        _LABELS.clear()
    _LABELS[(home, away)] = out
    return out


_LABELS: dict[tuple[str, str], dict] = {}


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


def scorer_label(player: str) -> str:
    return f"{player} buteur"


def all_markets_block(f: dict, cands: list[CandidateRow], home: str, away: str, cfg: dict) -> dict | None:
    """Every market of the odds feed for one match: those the model priced (with the best odds, its bookmaker, the
    model's chance and value, and "validé" / "non validé"), the best value first, and the bets it cannot price
    ("non analysé"), with their best odds."""
    others = f.get("non_analyses") or []
    if not cands and not others and not f.get("buteurs_cotes"):
        return None
    enabled = cfg.get("enabled_markets", ())
    rows = []
    for c in sorted(cands, key=lambda c: -c.ev):
        ok = c.market in enabled
        row = {"pari": bet_label(c.market, c.selection, home, away, c.line),
               "marche": MARKET_NAMES.get(c.market, c.market), "cote": c.odds, "bookmaker": c.bookmaker,
               "proba": round(c.p_win, 4), "valeur": round(c.ev, 4), "valide": ok,
               "validation": "validé" if ok else NON_VALIDE, "retenu": bool(c.accepted)}
        if c.p_push:
            row["rembourse"] = round(c.p_push, 4)
        if c.market_fair_prob is not None:
            row["proba_marche"] = round(c.market_fair_prob, 4)
        rows.append(row)
    # Anytime goalscorer (owner, 2026-10-15): priced by the scorer model, never backtested, so "non validé"; the
    # market's chance is 1/odds, margin included (a one-sided list cannot be de-vigged).
    scorers = [{"pari": scorer_label(r["joueur"]), "marche": SCORER_MARKET, "cote": r["cote"], "bookmaker": r["bookmaker"],
                "proba": r["proba"], "valeur": r["valeur"], "valide": False, "validation": NON_VALIDE, "retenu": False,
                "proba_marche": r["proba_marche"], "proba_marche_brute": True, "equipe": r.get("equipe")}
               for r in f.get("buteurs_cotes") or []]
    if scorers:
        rows = sorted(rows + scorers, key=lambda r: -r["valeur"])
    block = {"analyses": rows[:MAX_MARKET_ROWS], "n_analyses": len(rows),
             "n_marches": len({c.market for c in cands}) + bool(scorers), "non_analyses": others,
             "note": ALL_MARKETS_NOTE}
    if scorers:
        block["note_buteurs"] = SCORER_MARKET_NOTE
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


BOOK_NAMES = {"winamax_fr": "Winamax", "betclic_fr": "Betclic", "unibet_fr": "Unibet", "pmu_fr": "PMU",
              "netbet_fr": "NetBet"}


def match_odds_1x2(rows) -> dict[str, dict]:
    """The 1X2 odds of each match of a run by its "Home - Away" label, Winamax first, else the first bookmaker
    quoting all three."""
    books: dict[str, dict[str, dict]] = {}
    for r in rows:
        books.setdefault(r.event_label, {}).setdefault(r.bookmaker, {})[r.selection] = r.odds
    out = {}
    for match, by_book in books.items():
        full = {b: o for b, o in by_book.items() if {"home", "draw", "away"} <= o.keys()}
        if full:
            b = "winamax_fr" if "winamax_fr" in full else sorted(full)[0]
            o = full[b]
            out[match] = {"domicile": o["home"], "nul": o["draw"], "exterieur": o["away"],
                          "bookmaker": BOOK_NAMES.get(b, b)}
    return out


def build_match_docs(db: DB, run: AnalysisRun, current: dict[str, dict]) -> dict[str, dict]:
    """Documents to write for one completed pre-match run, merged into the page's current documents."""
    out: dict[str, dict] = {}
    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
    today = journee(started)
    cands = list(db.scalars(select(CandidateRow).where(CandidateRow.run_id == run.id)))
    sels = {s.candidate_id: s for s in db.scalars(select(Selection).where(Selection.run_id == run.id))}
    odds_1x2 = match_odds_1x2(db.scalars(select(OddsObservation).where(OddsObservation.run_id == run.id,
                                                                       OddsObservation.market == "1x2")))
    for f in run.fixtures or []:
        ko = datetime.fromisoformat(f["kickoff"])
        if journee(ko) != today:
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
        for label, o in odds_1x2.items():  # the odds feed spells teams its own way
            h, _, a = label.partition(" - ")
            if same_team(h, home) and same_team(a, away):
                doc["cotes_1x2"] = o
                break
        which = pass_name(started, ko)
        doc["passages"] = {**(doc.get("passages") or {}), which: started.isoformat()}
        if reason:
            if not doc.get("selections"):
                doc["verdict"] = "non_analyse"
                doc["rejets"] = [SKIP_REASONS.get(reason, reason)]
            pos = position(doc)
            if pos:
                doc["position"] = pos
            out[key] = doc
            continue
        mine = [c for c in cands if c.match_id == f["match_id"]]
        moves = f.get("market_moves") or {}
        previous = {s.get("pari"): s for s in doc.get("selections") or []}
        selections, rejets = [], []
        enabled = (run.config or {}).get("enabled_markets", ())
        for c in sorted(mine, key=lambda c: -c.score):
            label = bet_label(c.market, c.selection, home, away, c.line)
            if c.id in sels:
                s = dict(previous.get(label) or {})
                s.update(pari=label, marche=MARKET_NAMES.get(c.market, c.market), proba=round(c.p_win, 4),
                         valeur=round(c.ev, 4), bookmaker=c.bookmaker, raison=sels[c.id].rationale,
                         valide=c.market in enabled)
                if not s["valide"]:
                    s["validation"] = NON_VALIDE
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
        if run.competition in ("UCL", "UEL"):
            rejets = [UCL_INFO if run.competition == "UCL" else UEL_INFO] + [t for t in (ucl_model_line(f, mine, home, away),) if t]
        doc["selections"] = selections
        doc["rejets"] = rejets
        info = (run.config or {}).get("info_markets", ("totals", "btts"))
        goals = goals_block(f, mine, info, odds_asked=any(c.market in info for c in cands))
        if goals:
            doc["buts"] = goals
        every = all_markets_block(f, mine, home, away, run.config or {})
        if every:
            doc["tous_marches"] = every
        buteurs = dict(doc.get("buteurs") or {})
        if not buteurs.get("joueurs"):  # keep the line-up estimates once published
            buteurs["raison"] = SCORER_NOTE
        if f.get("buteurs_cotes"):  # the bookmakers' scorer prices, with the player's share (followed live)
            buteurs.update(cotes=f["buteurs_cotes"], cotes_note=SCORER_MARKET_NOTE,
                           ecartes=f.get("buteurs_ecartes") or [])
        doc["buteurs"] = buteurs
        shots = shots_block(run.competition, doc["domicile"], doc["exterieur"])
        if shots:
            doc["tirs"] = shots
        est = likely_score(f)
        if est:
            doc["score_estime"] = est
        p = f.get("probabilities") or {}
        if {"1x2:home", "1x2:draw", "1x2:away"} <= p.keys():
            doc["proba_1x2"] = {"domicile": p["1x2:home"], "nul": p["1x2:draw"], "exterieur": p["1x2:away"]}
        text = scenario(f, doc["domicile"], doc["exterieur"])
        if text:
            doc["scenario"] = scenario_with_squads({**doc, "scenario": text})  # keep absentees and XIs
        doc["verdict"] = "selection" if selections else "aucune"
        pos = position(doc)
        if pos:
            doc["position"] = pos
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
    key = _label_keys(home, away).get(bet)
    if key is not None:  # every other market priced from the score (handicaps, team totals, exact score...)
        from sportai.engine.football.markets import settle_score

        return settle_score(key, hg, ag)
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
