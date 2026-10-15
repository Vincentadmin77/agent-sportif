"""Anytime goalscorer bets (owner, 2026-10-15): priced before the match, followed and settled from the match sheet.

- Before the match (services/runs.py): the bookmakers' "Anytime Goal Scorer" selections of API-Football's pre-match
  odds, each matched to a player of one of the two squads by name and priced by sportai.engine.football.scorers. A
  name that is no player of either team is dropped: the feed can be wrong. The squads (/players, two or three
  requests per team) are cached on disk per team and per day; the official line-up (/fixtures/lineups, one request)
  is read only within 75 min of kick-off.
- During the match (services/live_page.py): the goals (own goals apart), substitutions and red cards from the match
  events are written to the page document ("buteurs.marques", "feuille_de_match"), so a scorer bet can be followed
  and settled.
- Settlement: won when the player scored (an own goal is not his), lost otherwise, refunded when he did not play at
  all, as the bookmakers do. Never settled on a guess: without the match sheet the bet stays open.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from sportai.connectors.teams import normalise
from sportai.engine.football.scorers import (PlayerSeason, Squad, best_scorer_odds, match_player, name_score,
                                             price_scorers, squad_from_api)

PLAYERS_CACHE = "/mnt/project-files/agent-sportif/state/effectifs"
LINEUP_WINDOW = timedelta(minutes=75)
MARCHE = "Buteur"


def cache_dir() -> Path:
    return Path(os.getenv("SPORTAI_PLAYERS_CACHE", PLAYERS_CACHE))


def squad_players(api, team_id: int, season: int, day: str) -> list[dict]:
    """/players of a team, from the day's disk cache when an earlier pass already fetched it (one fetch per team and
    per day: a striker's season does not change between two passes of the same day)."""
    path = cache_dir() / day / f"{team_id}-{season}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    items = api.team_players(team_id, season)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False))
    except OSError:
        pass  # no cache: the next pass asks again, nothing else is lost
    return items


def lineup_sides(lineups: list[dict], ids: dict[str, int]) -> dict[str, dict[int, str]]:
    """/fixtures/lineups -> by side, player id -> "titulaire" / "remplacant"; {} until both are published."""
    by_team = {(t.get("team") or {}).get("id"): t for t in lineups or []}
    if not all(tid in by_team for tid in ids.values()):
        return {}
    out = {}
    for side, tid in ids.items():
        t = by_team[tid]
        sheet = {p["player"]["id"]: "titulaire" for p in t.get("startXI") or [] if (p.get("player") or {}).get("id")}
        sheet.update({p["player"]["id"]: "remplacant" for p in t.get("substitutes") or []
                      if (p.get("player") or {}).get("id")})
        out[side] = sheet
    return out


def price_fixture(api, item: dict, fixture, expected_goals, now: datetime, season: int) -> dict | None:
    """Scorer rows of one fixture: item is its raw /odds entry; fixture has home_id / away_id. None when the
    bookmakers offer no anytime scorer market or the match model has no expected goals."""
    odds = best_scorer_odds(item.get("bookmakers") or [])
    if not odds or not expected_goals or not getattr(fixture, "home_id", None) or not getattr(fixture, "away_id", None):
        return None
    ids = {"domicile": fixture.home_id, "exterieur": fixture.away_id}
    day = f"{now:%Y-%m-%d}"
    squads = {side: squad_from_api(squad_players(api, tid, season, day), tid, season) for side, tid in ids.items()}
    sheet = {}
    if fixture.kickoff - now <= LINEUP_WINDOW:
        try:
            sheet = lineup_sides(api.lineups(fixture.provider_event_id), ids)
        except Exception:  # noqa: BLE001 - the line-up only sharpens the minutes, never blocks the pricing
            sheet = {}
    rows, dropped = price_scorers(odds, squads, {"domicile": expected_goals[0], "exterieur": expected_goals[1]}, sheet)
    names = {"domicile": fixture.home, "exterieur": fixture.away}
    for r in rows:
        r["equipe"] = names[r["cote_equipe"]]
    return {"rows": rows, "ecartes": dropped, "composition": bool(sheet)}


# --- the match sheet, from the live events --------------------------------------------------------------------------

def match_sheet(events: list[dict], home_id: int, lineups: list[dict] | None = None) -> dict:
    """Goals (own goals and missed penalties apart), substitutions and red cards of API-Football /fixtures/events.
    In a substitution event "player" leaves the pitch and "assist" comes on (checked on LASK - Liverpool, 2026-10-14)."""
    side = lambda e: "domicile" if (e.get("team") or {}).get("id") == home_id else "exterieur"  # noqa: E731
    who = lambda p: {"id": (p or {}).get("id"), "joueur": (p or {}).get("name")}  # noqa: E731
    marques, csc, entres, sortis, expulses = [], [], [], [], []
    for e in events or []:
        t, detail = e.get("type"), e.get("detail") or ""
        minute = ((e.get("time") or {}).get("elapsed") or 0) + ((e.get("time") or {}).get("extra") or 0)
        if t == "Goal":
            if detail == "Missed Penalty":
                continue
            row = {**who(e.get("player")), "equipe": side(e), "minute": minute,
                   **({"penalty": True} if detail == "Penalty" else {})}
            (csc if detail == "Own Goal" else marques).append(row)
        elif t == "subst":
            sortis.append({**who(e.get("player")), "equipe": side(e), "minute": minute})
            entres.append({**who(e.get("assist")), "equipe": side(e), "minute": minute})
        elif t == "Card" and detail in ("Red Card", "Second Yellow card"):
            expulses.append({**who(e.get("player")), "equipe": side(e), "minute": minute})
    sheet = {"buts": marques, "contre_son_camp": csc, "entres": entres, "sortis": sortis, "expulses": expulses}
    if lineups:
        teams = {(t.get("team") or {}).get("id"): t for t in lineups}
        for tid, t in teams.items():
            key = "domicile" if tid == home_id else "exterieur"
            sheet.setdefault("titulaires", {})[key] = [who(p.get("player")) for p in t.get("startXI") or []]
    return sheet


def _same(row: dict, scorer: dict) -> bool:
    """Is this match-sheet row the scorer bet's player? By API-Football id, else by name."""
    if row.get("id") is not None and scorer.get("id") is not None:
        return row["id"] == scorer["id"]
    name = row.get("joueur") or ""
    p = PlayerSeason(scorer.get("id") or 0, scorer.get("nom_api") or scorer.get("nom") or "")
    return bool(name) and (name_score(name, p) > 0 or normalise(name) == normalise(scorer.get("nom") or ""))


def player_state(scorer: dict, doc: dict) -> dict:
    """Where the player stands from the page document: goals scored (after `apres_minute` for a bet taken live),
    whether he started, came on, left the pitch; None for what the document cannot tell."""
    sheet = doc.get("feuille_de_match") or {}
    after = scorer.get("apres_minute")
    goals = [g for g in (doc.get("buteurs") or {}).get("marques") or sheet.get("buts") or []
             if _same(g, scorer) and (after is None or (g.get("minute") or 0) > after)]
    starters = (sheet.get("titulaires") or {}).get(scorer.get("cote_equipe") or "")
    if starters is None and "titulaires" in sheet:
        starters = [p for side in sheet["titulaires"].values() for p in side]
    if starters is None:  # the line-up published before the match (names only)
        block = ((doc.get("compositions") or {}).get(scorer.get("cote_equipe") or "") or {})
        if block.get("titulaires"):
            starters = [{"id": None, "joueur": n} for n in block["titulaires"]]
    started = None if starters is None else any(_same(p, scorer) for p in starters)
    came_on = any(_same(p, scorer) for p in sheet.get("entres") or [])
    left = any(_same(p, scorer) for p in (sheet.get("sortis") or []) + (sheet.get("expulses") or []))
    return {"buts": len(goals), "titulaire": started, "entre": came_on, "sorti": left,
            "a_joue": True if (started or came_on or goals) else (None if started is None else False)}


def settle_scorer(leg: dict, doc: dict) -> str | None:
    """"gagne" / "perdu" / "rembourse" of a scorer leg once the match is over, None while it cannot be told (match not
    over, or no match sheet recorded: never settled on a guess)."""
    if doc.get("statut") != "termine":
        return None
    scorer = leg.get("buteur") or {}
    if "marques" not in (doc.get("buteurs") or {}) and "buts" not in (doc.get("feuille_de_match") or {}):
        return None
    st = player_state(scorer, doc)
    if st["buts"]:
        return "gagne"
    if scorer.get("apres_minute") is not None:  # taken live while he was on the pitch: he played
        return "perdu"
    if st["a_joue"] is None:
        return None
    return "perdu" if st["a_joue"] else "rembourse"


def scorer_leg_info(row: dict) -> dict:
    """What a leg keeps to be followed and settled."""
    return {"id": row.get("joueur_id"), "nom": row["joueur"], "nom_api": row.get("nom_api"),
            "cote_equipe": row.get("cote_equipe"), "part": row.get("part"),
            "minutes_par_titularisation": row.get("minutes_par_titularisation")}


def live_odds_by_player(bets: list[dict]) -> dict[str, dict]:
    """In-play anytime scorer prices (API-Football /odds/live bets), when the feed offers that market."""
    return best_scorer_odds([{"name": "API-Football direct", "bets": bets}])


def find_scorer_row(name: str, rows: list[dict]) -> dict | None:
    """The pre-match scorer row (with the player's share) a live selection names."""
    exact = [r for r in rows if normalise(r["joueur"]) == normalise(name)]
    if len(exact) == 1:  # the same bookmaker spelling as before the match
        return exact[0]
    squads = [Squad(0, {i: PlayerSeason(i, r.get("nom_api") or r["joueur"]) for i, r in enumerate(rows)}, 1.0, 1)]
    found = match_player(name, squads)
    return None if found is None else rows[found[1].player_id]
