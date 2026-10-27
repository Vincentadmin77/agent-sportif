"""Basketball (NBA, WNBA) on the results page, next to football and under the same rules (owner 2026-10-27).

One pass = the games of the basketball day (prematch.journee(..., "basket"): 04:00 Paris to 04:00 the next night)
not started yet: the team-ratings model (sportai/engine/basketball/ratings.py, docs/20-nba-modele.md) is refitted on
the cached results, each game priced on every market it can read from the odds feed, and one page document is
written per game (doc_id = day-home-away, "sport": "basket"). The bets it keeps ("paris_basket") feed the day's
"Mon pronostic" document with the football ones (pronostic.basket_legs), where the usual rules apply: one bet per
match at 1.60 or more, the Top confiance only among "Confiance élevée" and validated bets, combos of 2.

Validated: the NBA winner (overtime included), calibrated by the walk-forward backtest. Everything else (points
total, handicaps, a team's points) and every WNBA market (no WNBA backtest) is "non validé": taken with the care of
football's unvalidated markets, never the Top confiance.

Live and final: score, quarter, and the quarter scores, from the same feed (no live betting advice).
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sportai.connectors.api_basketball import DROPPED, FINISHED, LIVE, NOT_STARTED, kickoff, season_for
from sportai.engine.basketball.ratings import Game, Prediction, RatingsModel, rest_flags

CACHE = Path(os.getenv("BASKET_CACHE", "/mnt/project-files/agent-sportif/data/api-basketball"))
CACHE_MAX_AGE = timedelta(hours=6)
US = ZoneInfo("America/New_York")
SPORT = "basket"
COMPETITIONS = ("NBA", "WNBA")
HISTORY_SEASONS = 3  # the current one and the two before (half-life 80 days: older ones weigh nothing)
MIN_GAMES = {"NBA": 40, "WNBA": 20}  # a team with fewer games in a full season is an exhibition side
VALIDATED = {"NBA": {"winner"}, "WNBA": set()}  # docs/20-nba-modele.md: the NBA winner only

MIN_ODDS = 1.60  # owner 2026-10-27: never under 1.60, for every basketball bet
# Unvalidated markets: football's care (pronostic.TRIAL_*), the 5 best of a game by value.
TRIAL_MIN_VALUE, TRIAL_MIN_PROBA, TRIAL_MAX_ODDS, TRIAL_PER_GAME = 0.03, 0.25, 4.0, 5
MAX_DIVERGENCE = 0.10
MAX_ROWS = 80

MARKETS = {  # feed bet (id, name) -> our market
    2: "winner", 3: "handicap", 4: "total", 28: "team_total_home", 29: "team_total_away",
    "home/away": "winner", "asian handicap": "handicap", "over/under": "total",
    "home team total goals (including ot)": "team_total_home", "away team total goals (including ot)": "team_total_away",
}
MARKET_NAMES = {"winner": "Vainqueur (prolongation comprise)", "handicap": "Handicap de points",
                "total": "Total de points", "team_total_home": "Points de l'équipe à domicile",
                "team_total_away": "Points de l'équipe à l'extérieur"}
NON_VALIDE = "non validé"
PERIODS = {"Q1": "1er quart-temps", "Q2": "2e quart-temps", "HT": "mi-temps", "Q3": "3e quart-temps",
           "Q4": "4e quart-temps", "BT": "pause", "OT": "prolongation"}
INFO = {
    "NBA": ("Basket NBA : modèle de points par équipe (attaque, défense, avantage du terrain, fatigue), testé sur "
            "trois saisons. Vainqueur, prolongation comprise : validé. Total de points, handicaps et points d'une "
            "équipe : « non validé », jamais la Top confiance. Cote minimale 1,60."),
    "WNBA": ("Basket WNBA : même modèle que la NBA, pas encore testé sur la WNBA. Tous les marchés sont « non validé » "
             "et ne peuvent pas être la Top confiance. Cote minimale 1,60."),
}
NO_MODEL = "Historique insuffisant pour une des deux équipes : match listé sans estimation."
NO_ODDS = "Aucune cote disponible pour ce match pour l'instant."


# ---------------------------------------------------------------- history and model

def _cache_file(comp: str, season) -> Path:
    return CACHE / f"games_{comp.lower()}_{season}.json.gz"


def _seasons(comp: str, day: date) -> list:
    cur = season_for(comp, day)
    if isinstance(cur, int):
        return [cur - k for k in range(HISTORY_SEASONS - 1, -1, -1)]
    y = int(cur[:4])
    return [f"{y - k}-{y - k + 1}" for k in range(HISTORY_SEASONS - 1, -1, -1)]


def load_season(api, comp: str, season, current: bool, now: datetime) -> list[dict]:
    """A season's raw games from the shared cache; the current season is fetched again when older than 6 h, a past
    one once. A failed download falls back on the cached copy."""
    path = _cache_file(comp, season)
    fresh = path.exists() and (not current or now.timestamp() - path.stat().st_mtime < CACHE_MAX_AGE.total_seconds())
    if not fresh and api is not None:
        try:
            games = api.season_games(comp, season)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt") as f:
                json.dump({"response": games}, f)
            tmp.replace(path)
            return games
        except Exception as e:  # noqa: BLE001 - keep the cached copy
            print(f"{comp} {season}: history unavailable ({str(e)[:120]})", file=sys.stderr)
    if not path.exists():
        return []
    with gzip.open(path, "rt") as f:
        return json.load(f).get("response") or []


def _us_day(g: dict) -> date:
    return kickoff(g).astimezone(US).date()


def _preseason(comp: str, d: date) -> bool:
    if comp == "WNBA":
        return d.month < 5 or (d.month == 5 and d.day < 14)
    return d.month == 10 and d.day < 18


def history_games(raw_by_season: list[tuple[list[dict], bool]], comp: str) -> list[Game]:
    """Finished regular games (overtime included) as model games, dated in US Eastern time like the backtest.
    Exhibition sides (fewer than MIN_GAMES in a full season) and pre-season dates are dropped; in the season under
    way, a team is kept when a full season knows it or it has played 5 games already."""
    known: set[str] = set()
    seasons = []
    for raw, current in raw_by_season:
        fin = [g for g in raw if g["status"]["short"] in FINISHED]
        count = Counter(t for g in fin for t in (g["teams"]["home"]["name"], g["teams"]["away"]["name"]))
        seasons.append((fin, count, current))
        if not current:
            known |= {t for t, n in count.items() if n >= MIN_GAMES.get(comp, 40)}
    games = []
    for fin, count, current in seasons:
        ok = ({t for t, n in count.items() if t in known or n >= 5} if current
              else {t for t, n in count.items() if n >= MIN_GAMES.get(comp, 40)})
        for g in fin:
            h, a = g["teams"]["home"]["name"], g["teams"]["away"]["name"]
            d = _us_day(g)
            if h not in ok or a not in ok or _preseason(comp, d):
                continue
            hp, ap = g["scores"]["home"]["total"], g["scores"]["away"]["total"]
            if hp is None or ap is None:
                continue
            games.append(Game(d, h, a, int(hp), int(ap), g["status"]["short"] == "AOT"))
    return sorted(games, key=lambda g: g.day)


def predictions(history: list[Game], upcoming: list[dict]) -> dict[int, Prediction | None]:
    """The model's prediction of each upcoming raw game (by id), None when a team is unknown to the model."""
    if not upcoming:
        return {}
    if len(history) < 100:
        return {g["id"]: None for g in upcoming}
    fut = [(g, Game(_us_day(g), g["teams"]["home"]["name"], g["teams"]["away"]["name"])) for g in upcoming]
    model = RatingsModel().fit(history, min(x.day for _, x in fut))
    allg = sorted(history + [x for _, x in fut], key=lambda x: x.day)
    flags = {id(x): f for x, f in zip(allg, rest_flags(allg))}
    out = {}
    for g, x in fut:
        out[g["id"]] = (model.predict(x.home, x.away, flags[id(x)])
                        if x.home in model.teams and x.away in model.teams else None)
        if out[g["id"]] is not None:
            out[g["id"]].rest = flags[id(x)]  # type: ignore[attr-defined]
    return out


# ---------------------------------------------------------------- odds

def parse_odds(entry: dict | None) -> dict[tuple, dict[str, float]]:
    """(market, selection, line) -> {bookmaker: odds}. The handicap line is always the home side's (the feed's
    "Away -4.5" is the away side of home -4.5, i.e. away +4.5). Only half-point lines: no refund to settle."""
    out: dict[tuple, dict[str, float]] = {}
    for b in (entry or {}).get("bookmakers") or []:
        book = str(b.get("name") or b.get("id"))
        for bet in b.get("bets") or []:
            name = bet.get("name")
            market = MARKETS.get(str(name).lower()) if name else MARKETS.get(bet.get("id"))
            if not market:
                continue
            for v in bet.get("values") or []:
                try:
                    odd = float(v["odd"])
                    words = str(v["value"]).split()
                except (KeyError, TypeError, ValueError):
                    continue
                if odd <= 1.0:
                    continue
                if market == "winner":
                    if words[0].lower() not in ("home", "away") or len(words) != 1:
                        continue
                    key = ("winner", words[0].lower(), None)
                else:
                    if len(words) != 2:
                        continue
                    side = words[0].lower()
                    try:
                        line = float(words[1])
                    except ValueError:
                        continue
                    if line % 1 != 0.5:
                        continue
                    if market == "handicap" and side in ("home", "away"):
                        key = ("handicap", side, line)
                    elif market != "handicap" and side in ("over", "under"):
                        key = (market, side, line)
                    else:
                        continue
                out.setdefault(key, {})[book] = odd
    return out


OTHER = {"home": "away", "away": "home", "over": "under", "under": "over"}


def model_proba(pred: Prediction, market: str, selection: str, line: float | None) -> float:
    if market == "winner":
        p = pred.p_home_win()
        return p if selection == "home" else 1 - p
    if market == "handicap":
        p = pred.p_home_cover(line)
        return p if selection == "home" else 1 - p
    if market == "total":
        p = pred.p_over(line)
    else:
        p = pred.p_team_over("home" if market.endswith("home") else "away", line)
    return p if selection == "over" else 1 - p


def price(pred: Prediction, quotes: dict[tuple, dict[str, float]]) -> list[dict]:
    """Every quoted bet with the model's chance, the bookmakers' de-vigged chance (mean over the bookmakers quoting
    both sides of the line) and the best odds."""
    rows = []
    for (market, sel, line), books in quotes.items():
        other = quotes.get((market, OTHER[sel], line)) or {}
        fair = [(1 / o) / (1 / o + 1 / other[b]) for b, o in books.items() if b in other]
        if not fair:
            continue
        p = model_proba(pred, market, sel, line)
        best = max(books.values())
        rows.append({"marche_cle": market, "selection": sel, "ligne": line, "cote": round(best, 2),
                     "proba": round(p, 4), "proba_marche": round(sum(fair) / len(fair), 4),
                     "valeur": round(p * best - 1, 4), "n_bookmakers": len(books)})
    return sorted(rows, key=lambda r: -r["valeur"])


def _n(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def label(market: str, selection: str, line: float | None, home: str, away: str) -> str:
    team = home if selection == "home" else away
    if market == "winner":
        return f"Victoire {team}"
    if market == "handicap":
        h = line if selection == "home" else -line
        return f"{team} {'+' if h > 0 else ''}{_n(h)} points (handicap)"
    word = "Plus" if selection == "over" else "Moins"
    if market == "total":
        return f"{word} de {_n(line)} points dans le match"
    side = home if market.endswith("home") else away
    return f"{side} : {word.lower()} de {_n(line)} points"


def settle(market: str, selection: str, line: float | None, hp: int, ap: int) -> str:
    """A basketball bet from the final score, overtime included (half-point lines: never refunded)."""
    if market == "winner":
        won = hp > ap if selection == "home" else ap > hp
    elif market == "handicap":
        won = (hp + line - ap > 0) if selection == "home" else (hp + line - ap < 0)
    else:
        pts = hp + ap if market == "total" else (hp if market.endswith("home") else ap)
        won = pts > line if selection == "over" else pts < line
    return "gagne" if won else "perdu"


# ---------------------------------------------------------------- documents

def doc_key(game: dict) -> str:
    from sportai.services.dashboard import doc_id

    return doc_id(kickoff(game), game["teams"]["home"]["name"], game["teams"]["away"]["name"], SPORT)


def bets(comp: str, rows: list[dict], home: str, away: str) -> list[dict]:
    """The bets a game offers to "Mon pronostic": the validated ones at 1.60 or more, and the 5 best unvalidated
    ones by value that pass football's care for unvalidated markets."""
    keep, trials = [], []
    for r in rows:
        if r["cote"] < MIN_ODDS:
            continue
        ok = r["marche_cle"] in VALIDATED.get(comp, set())
        b = {"pari": label(r["marche_cle"], r["selection"], r["ligne"], home, away),
             "marche": MARKET_NAMES[r["marche_cle"]], "cote": r["cote"], "proba": r["proba"],
             "proba_marche": r["proba_marche"], "valeur": r["valeur"], "valide": ok,
             "basket": {"marche": r["marche_cle"], "selection": r["selection"], "ligne": r["ligne"]}}
        if ok:
            keep.append(b)
            continue
        if (r["valeur"] < TRIAL_MIN_VALUE or r["proba"] < TRIAL_MIN_PROBA or r["cote"] > TRIAL_MAX_ODDS
                or abs(r["proba"] - r["proba_marche"]) > MAX_DIVERGENCE):
            continue
        trials.append({**b, "validation": NON_VALIDE})
    return keep + sorted(trials, key=lambda b: -b["valeur"])[:TRIAL_PER_GAME]


def _scenario(pred: Prediction, home: str, away: str) -> str:
    p = pred.p_home_win()
    fav = home if pred.margin >= 0 else away
    text = (f"Points attendus : {home} {pred.home_pts:.0f}, {away} {pred.away_pts:.0f} (total {pred.total:.0f}, "
            f"écart de {abs(pred.margin):.0f} en faveur de {fav}). Chance de victoire, prolongation comprise : "
            f"{home} {p * 100:.0f} %, {away} {(1 - p) * 100:.0f} %. ")
    hb, _, ab, _ = getattr(pred, "rest", (0, 0, 0, 0))
    tired = [t for t, f in ((home, hb), (away, ab)) if f]
    if tired:
        text += " et ".join(tired) + (" a" if len(tired) == 1 else " ont") + " joué la veille (fatigue comptée). "
    return text + "Avantage du terrain compté. Estimation du modèle, pas un pari."


def _estimate(pred: Prediction, home: str, away: str) -> dict:
    hp, ap = round(pred.home_pts), round(pred.away_pts)
    if hp == ap:  # no draw in basketball: the favourite by one
        hp, ap = (hp + 1, ap) if pred.margin >= 0 else (hp, ap + 1)
    p = pred.p_home_win()
    return {"score": f"{hp} - {ap}", "domicile": hp, "exterieur": ap, "proba": round(max(p, 1 - p), 4)}


def _position(pred: Prediction, home: str, away: str) -> dict:
    p = pred.p_home_win()
    team, q = (home, p) if p >= 0.5 else (away, 1 - p)
    return {"issue": f"Victoire {team}", "proba": round(q, 4), "source": "modele",
            "texte": f"Notre lecture : {team} gagne ({q * 100:.0f} %, prolongation comprise). "
                     "La cote retenue du match, s'il y en a une, est dans « Mon pronostic »."}


def build_doc(comp: str, game: dict, pred: Prediction | None, odds_entry: dict | None, current: dict | None,
              now: datetime) -> dict:
    """The page document of one game not started yet, merged into what earlier passes wrote."""
    from sportai.services.dashboard import COMPETITION_NAMES, pass_name
    from sportai.services.prematch import journee

    ko = kickoff(game)
    home, away = game["teams"]["home"]["name"], game["teams"]["away"]["name"]
    doc = dict(current or {})
    doc.update(sport=SPORT, jour=f"{journee(ko, SPORT):%Y-%m-%d}", coup_envoi=ko.isoformat(),
               competition=COMPETITION_NAMES.get(comp, comp), ref=game["id"])
    doc.setdefault("domicile", home)
    doc.setdefault("exterieur", away)
    doc.setdefault("statut", "a_venir")
    doc["passages"] = {**(doc.get("passages") or {}), pass_name(now, ko): now.isoformat()}
    doc["selections"] = []
    doc["rejets"] = [INFO.get(comp, INFO["WNBA"])]
    if pred is None:
        doc.update(verdict="non_analyse", paris_basket=[])
        doc["rejets"].append(NO_MODEL)
        return doc
    p = pred.p_home_win()
    doc["proba_vainqueur"] = {"domicile": round(p, 4), "exterieur": round(1 - p, 4)}
    doc["points_attendus"] = {"domicile": round(pred.home_pts, 1), "exterieur": round(pred.away_pts, 1),
                              "total": round(pred.total, 1), "ecart": round(pred.margin, 1)}
    doc["scenario"] = _scenario(pred, home, away)
    doc["score_estime"] = _estimate(pred, home, away)
    doc["position"] = _position(pred, home, away)
    rows = price(pred, parse_odds(odds_entry))
    win = {r["selection"]: r["cote"] for r in rows if r["marche_cle"] == "winner"}
    if {"home", "away"} <= win.keys():
        doc["cotes_vainqueur"] = {"domicile": win["home"], "exterieur": win["away"]}
    if not rows:
        doc["rejets"].append(NO_ODDS)
    ok = VALIDATED.get(comp, set())
    doc["tous_marches"] = {
        "analyses": [{"pari": label(r["marche_cle"], r["selection"], r["ligne"], home, away),
                      "marche": MARKET_NAMES[r["marche_cle"]], "cote": r["cote"], "proba": r["proba"],
                      "proba_marche": r["proba_marche"], "valeur": r["valeur"], "valide": r["marche_cle"] in ok,
                      "validation": "validé" if r["marche_cle"] in ok else NON_VALIDE, "retenu": False}
                     for r in rows if r["proba"] >= 0.05][:MAX_ROWS],
        "n_analyses": len(rows), "n_marches": len({r["marche_cle"] for r in rows}),
        "note": "Marchés du match chiffrés par le modèle de points. Seul le vainqueur NBA est validé."}
    doc["paris_basket"] = bets(comp, rows, doc["domicile"], doc["exterieur"])
    doc["verdict"] = "aucune"
    return doc


def prematch(api, comps: list[str], current: dict[str, dict], now: datetime) -> tuple[dict[str, dict], list[str]]:
    """Documents of every game of the basketball day not started yet. Returns (docs, notes)."""
    from sportai.services.prematch import journee

    day = journee(now, SPORT)
    docs, notes = {}, []
    for comp in comps:
        try:
            games = [g for g in api.games_on([comp], day) if g["status"]["short"] in NOT_STARTED and kickoff(g) > now]
        except Exception as e:  # noqa: BLE001 - one competition failing never loses the others
            notes.append(f"{comp} failed ({str(e)[:120]})")
            continue
        if not games:
            notes.append(f"{comp} aucun match")
            continue
        raw = [(load_season(api, comp, s, i == HISTORY_SEASONS - 1, now), i == HISTORY_SEASONS - 1)
               for i, s in enumerate(_seasons(comp, day))]
        preds = predictions(history_games(raw, comp), games)
        for g in games:
            key = doc_key(g)
            odds = None
            if preds.get(g["id"]) is not None:
                try:
                    odds = api.game_odds(g["id"])
                except Exception as e:  # noqa: BLE001 - the game is still listed, without odds
                    print(f"odds unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
            docs[key] = build_doc(comp, g, preds.get(g["id"]), odds, current.get(key), now)
        notes.append(f"{comp} completed ({len(games)} match(s))")
    return docs, notes


def quarters(game: dict) -> list[dict]:
    out = []
    sh, sa = game["scores"]["home"], game["scores"]["away"]
    for k, name in (("quarter_1", "Q1"), ("quarter_2", "Q2"), ("quarter_3", "Q3"), ("quarter_4", "Q4"),
                    ("over_time", "Prol.")):
        if sh.get(k) is not None and sa.get(k) is not None:
            out.append({"periode": name, "domicile": sh[k], "exterieur": sa[k]})
    return out


def follow(game: dict, doc: dict, now: datetime) -> dict | None:
    """The document updated from the feed's game: score, quarter and quarter scores, the end. None if unchanged."""
    from sportai.services.dashboard import settle_score

    short = game["status"]["short"]
    if short in NOT_STARTED or doc.get("statut") == "termine":
        return None
    new = dict(doc)
    if short in DROPPED:
        new["statut"] = "reporte"
        return new if new != doc else None
    hp, ap = game["scores"]["home"]["total"], game["scores"]["away"]["total"]
    if hp is None or ap is None:
        return None
    new["score"] = f"{int(hp)} - {int(ap)}"
    new["score_quarts"] = quarters(game)
    h1 = [game["scores"][s].get("quarter_1") for s in ("home", "away")]
    h2 = [game["scores"][s].get("quarter_2") for s in ("home", "away")]
    if None not in h1 + h2:
        new["score_mi_temps"] = f"{h1[0] + h2[0]} - {h1[1] + h2[1]}"
    if short in FINISHED:
        new["statut"] = "termine"
        new["direct"] = {"periode": "terminé" + (" après prolongation" if short == "AOT" else ""),
                         "maj": now.isoformat()}
        settle_score(new, int(hp), int(ap))
    elif short in LIVE:
        new["statut"] = "en_direct"
        new["direct"] = {"periode": PERIODS.get(short, short), "chrono": game["status"].get("timer"),
                         "maj": now.isoformat()}
    new["passages"] = {**(doc.get("passages") or {}), "direct": now.isoformat()}
    return new


def update_scores(api, comps: list[str], current: dict[str, dict], now: datetime) -> dict[str, dict]:
    """Live follow-up and final results of the page's basketball games: today's basketball day and every day with
    a game still open. One or two requests per competition and day."""
    from sportai.services.prematch import journee

    days = {journee(now, SPORT)} | {date.fromisoformat(d["jour"]) for d in current.values()
                                   if d.get("sport") == SPORT and d.get("jour") and d.get("statut") != "termine"
                                   and d.get("competition") in comps}
    docs = {}
    for day in sorted(days):
        try:
            games = api.games_on(comps, day)
        except Exception as e:  # noqa: BLE001 - one day failing never blocks the others
            print(f"basket {day}: {str(e)[:120]}", file=sys.stderr)
            continue
        for g in games:
            key = doc_key(g)
            if key in current:
                new = follow(g, current[key], now)
                if new is not None:
                    docs[key] = new
    return docs


def legs(matchs: dict[str, dict], day: str) -> list[dict]:
    """The day's basketball bets for "Mon pronostic" (pronostic.build_pronostic), from the page's documents."""
    out = []
    for key, d in matchs.items():
        if d.get("sport") != SPORT or d.get("jour") != day:
            continue
        for b in d.get("paris_basket") or []:
            out.append({"match_id": key, "match": f"{d['domicile']} - {d['exterieur']}",
                        "competition": d.get("competition"), "coup_envoi": d["coup_envoi"], "sport": SPORT,
                        **b, "_divergence_max": MAX_DIVERGENCE, "_p_cmp": b["proba"]})
    return out
