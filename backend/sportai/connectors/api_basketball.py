"""API-Basketball v1 (https://www.api-basketball.com) — NBA and WNBA games, results by quarter, pre-match odds.

Same API-Sports account and key as API-Football (settings.api_football_key, header x-apisports-key). Like
API-Football it answers HTTP 200 when it refuses a request, with the reason in "errors": such a reply is a
ProviderUnavailable, never an empty list of games.

League ids and season labels (checked live on 2026-10-27): NBA = league 12, seasons "2026-2027";
WNBA = league 13 ("NBA W" in the feed), seasons 2026 (calendar year).
Odds come per game (/odds?game=<id>): every bookmaker with its bets (moneyline "Home/Away", "Asian Handicap",
"Over/Under", each team's points...). /odds does not take a date, so one request per game.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from sportai.connectors.base import ProviderNotConfigured, ProviderUnavailable
from sportai.connectors.http import CachedClient

BASE_URL = "https://v1.basketball.api-sports.io"
LEAGUE_IDS = {"NBA": 12, "WNBA": 13}
CALENDAR_YEAR = {"WNBA"}  # season "2026" = the 2026 championship; the NBA plays "2026-2027"

NOT_STARTED = {"NS"}
LIVE = {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"}
FINISHED = {"FT", "AOT"}
DROPPED = {"POST", "CANC", "SUSP", "AWD", "ABD"}


def season_for(competition: str, day: date) -> str | int:
    """API-Basketball season of a competition on a (betting) day."""
    if competition in CALENDAR_YEAR:
        return day.year
    y = day.year if day.month >= 7 else day.year - 1
    return f"{y}-{y + 1}"


def kickoff(game: dict) -> datetime:
    if game.get("timestamp"):
        return datetime.fromtimestamp(game["timestamp"], timezone.utc)
    return datetime.fromisoformat(game["date"].replace("Z", "+00:00")).astimezone(timezone.utc)


class ApiBasketballProvider:
    name = "api-basketball"

    def __init__(self, api_key: str | None, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ProviderNotConfigured("API_FOOTBALL_KEY is not set (API-Basketball uses the same key)")
        self._http = CachedClient(BASE_URL, headers={"x-apisports-key": api_key}, transport=transport, timeout=30.0)

    def _get(self, path: str, params: dict, ttl: float) -> dict:
        data = self._http.get_json(path, params, ttl=ttl)
        if data.get("errors"):
            raise ProviderUnavailable(f"api-basketball {path}: {data['errors']}")
        return data

    def games_on(self, competitions: list[str], day: date, ttl: float = 60) -> list[dict]:
        """Every game of the basketball betting day (sportai.services.prematch.journee, sport "basket": from 04:00
        Paris to 04:00 the next night) in these competitions (raw), finished ones included. Two UTC dates cover it."""
        from sportai.services.prematch import journee

        out = []
        for c in competitions:
            if c not in LEAGUE_IDS:
                continue
            season = season_for(c, day)
            for d in (day, day + timedelta(days=1)):
                for g in self._get("/games", {"league": LEAGUE_IDS[c], "season": season, "date": d.isoformat()},
                                   ttl=ttl).get("response", []):
                    if journee(kickoff(g), "basket") == day and g not in out:
                        out.append(g)
        return sorted(out, key=kickoff)

    def season_games(self, competition: str, season: str | int, ttl: float = 6 * 3600) -> list[dict]:
        """Every game of a season (raw), played and to come: one request."""
        return self._get("/games", {"league": LEAGUE_IDS[competition], "season": season}, ttl=ttl).get("response", [])

    def game_odds(self, game_id: int | str, ttl: float = 600) -> dict | None:
        """Pre-match odds of one game (raw: game, bookmakers [{name, bets [{id, name, values}]}]), None if none."""
        resp = self._get("/odds", {"game": game_id}, ttl=ttl).get("response") or []
        return resp[0] if resp else None
