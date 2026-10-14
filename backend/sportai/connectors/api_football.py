"""API-Football v3 (https://www.api-football.com) — fixtures, results, live
state, events and match statistics. Auth header: x-apisports-key.

API-Football answers HTTP 200 even when it refuses a request (plan limits,
wrong season, bad key): the reason is in the body's "errors" field. Such a
reply is a provider failure, never an empty list of matches.

Expected goals come from /fixtures/statistics ("expected_goals") and are only
present for some competitions; when absent they stay None.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from sportai.connectors.base import Fixture, HistoricalResult, LiveSnapshot, ProviderNotConfigured, ProviderUnavailable
from sportai.connectors.http import CachedClient

BASE_URL = "https://v3.football.api-sports.io"

LEAGUE_IDS = {"FR1": 61, "EN1": 39, "ES1": 140, "DE1": 78, "IT1": 135, "UCL": 2, "BR1": 71,
              # Only used as training history of the Champions League model (services/runs.py UCL_EXTRA):
              # the other European cups and the leagues their clubs come from.
              "UEL": 3, "UECL": 848,
              "PT1": 94, "NL1": 88, "TR1": 203, "BE1": 144, "AT1": 218, "SC1": 179, "GR1": 197, "UA1": 333,
              "CH1": 207, "CZ1": 345, "DK1": 119, "HR1": 210, "RS1": 286, "NO1": 103, "SK1": 332, "CY1": 318,
              "PL1": 106, "IL1": 383, "SE1": 113, "KZ1": 389, "AZ1": 419, "BG1": 172, "HU1": 271, "RO1": 283,
              "SI1": 373}
# Leagues playing calendar-year seasons (season "2026" = the 2026 championship), the others run autumn-spring.
CALENDAR_YEAR = {"BR1", "NO1", "SE1", "KZ1"}

_STATUS = {
    "NS": "scheduled", "TBD": "scheduled", "1H": "live", "HT": "live", "2H": "live", "ET": "live",
    "BT": "live", "P": "live", "LIVE": "live", "INT": "live", "FT": "finished", "AET": "finished",
    "PEN": "finished", "PST": "postponed", "CANC": "cancelled", "ABD": "cancelled", "SUSP": "postponed",
}


class ApiFootballProvider:
    name = "api-football"

    def __init__(self, api_key: str | None, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ProviderNotConfigured("API_FOOTBALL_KEY is not set")
        self._http = CachedClient(BASE_URL, headers={"x-apisports-key": api_key}, transport=transport)

    def _get(self, path: str, params: dict, ttl: float) -> dict:
        data = self._http.get_json(path, params, ttl=ttl)
        if data.get("errors"):
            raise ProviderUnavailable(f"api-football {path}: {data['errors']}")
        return data

    def upcoming_fixtures(self, competition: str, days_ahead: int = 1, season: int | None = None) -> list[Fixture]:
        today = date.today()
        season = season or (today.year if today.month >= 7 else today.year - 1)
        data = self._get("/fixtures", {
            "league": LEAGUE_IDS[competition], "season": season,
            "from": today.isoformat(), "to": (today + timedelta(days=days_ahead)).isoformat(),
        }, ttl=600)
        return [self._fixture(f, competition) for f in data.get("response", [])]

    def results(self, competition: str, season: str) -> list[HistoricalResult]:
        data = self._get("/fixtures", {"league": LEAGUE_IDS[competition], "season": int(season[:4]), "status": "FT-AET-PEN"}, ttl=3600)
        out = []
        for f in data.get("response", []):
            # 90-minute score: a cup tie decided in extra time is a draw over the 90 minutes the model prices.
            g = (f.get("score") or {}).get("fulltime") or {}
            if g.get("home") is None:
                g = f["goals"]
            if g["home"] is None:
                continue
            out.append(HistoricalResult(self.name, competition, _ts(f["fixture"]["date"]).date(),
                                        f["teams"]["home"]["name"], f["teams"]["away"]["name"], g["home"], g["away"], "real"))
        return out

    def live_fixtures(self, competitions: list[str]) -> list[dict]:
        """Matches under way now in these competitions (raw API-Football fixtures), in one request."""
        ids = "-".join(str(LEAGUE_IDS[c]) for c in competitions if c in LEAGUE_IDS)
        return self._get("/fixtures", {"live": ids}, ttl=10).get("response", []) if ids else []

    def fixtures_on(self, competitions: list[str], day: date) -> list[dict]:
        """Every match of the betting day (08:00-02:00 Paris) in these competitions (raw), finished ones included."""
        from sportai.services.prematch import DAY_ROLLOVER, LOCAL_TZ, journee

        season = day.year if day.month >= 7 else day.year - 1
        start = datetime.combine(day, datetime.min.time(), LOCAL_TZ) + DAY_ROLLOVER
        first, last = start.astimezone(timezone.utc).date(), (start + timedelta(days=1)).astimezone(timezone.utc).date()
        span = {"date": first.isoformat()} if first == last else {"from": first.isoformat(), "to": last.isoformat()}
        out = []
        for c in competitions:
            if c in LEAGUE_IDS:
                s = day.year if c in CALENDAR_YEAR else season  # Brazil plays calendar-year seasons
                out += [f for f in self._get("/fixtures", {"league": LEAGUE_IDS[c], "season": s, **span},
                                             ttl=10).get("response", [])
                        if journee(_ts(f["fixture"]["date"])) == day]
        return out

    def season_fixtures(self, competition: str, season: int) -> list[dict]:
        """Every match of a competition's season (raw fixtures): the full calendar, played and to come."""
        return self._get("/fixtures", {"league": LEAGUE_IDS[competition], "season": season},
                         ttl=6 * 3600).get("response", [])

    def head_to_head(self, team_a: int, team_b: int, last: int = 5) -> list[dict]:
        """The last meetings of two teams (raw fixtures), all competitions."""
        return self._get("/fixtures/headtohead", {"h2h": f"{team_a}-{team_b}", "last": last},
                         ttl=86400).get("response", [])

    def team_last(self, team_id: int, last: int = 5) -> list[dict]:
        """A team's last played matches (raw fixtures), all competitions."""
        return self._get("/fixtures", {"team": team_id, "last": last}, ttl=86400).get("response", [])

    def lineups(self, fixture_id: int | str, ttl: float = 300) -> list[dict]:
        """Official line-ups (empty until they are published, about an hour before kick-off)."""
        return self._get("/fixtures/lineups", {"fixture": fixture_id}, ttl=ttl).get("response", [])

    def injuries(self, fixture_id: int | str) -> list[dict]:
        """Players out or doubtful for one fixture (raw: player {id, name, type, reason}, team)."""
        return self._get("/injuries", {"fixture": fixture_id}, ttl=3600).get("response", [])

    def live_odds(self, fixture_id: int | str) -> list[dict]:
        """In-play odds of one fixture (raw bets: name, values), [] when the feed has none or fails."""
        try:
            resp = self._get("/odds/live", {"fixture": fixture_id}, ttl=30).get("response") or []
        except Exception:  # noqa: BLE001 - odds are optional, never break the live follow-up
            return []
        return (resp[0].get("odds") or []) if resp else []

    def team_players(self, team_id: int, season: int) -> list[dict]:
        """Every player of a team in a season with their statistics, all pages."""
        out, page = [], 1
        while True:
            data = self._get("/players", {"team": team_id, "season": season, "page": page}, ttl=6 * 3600)
            out += data.get("response", [])
            if page >= (data.get("paging") or {}).get("total", 1):
                return out
            page += 1

    def live_snapshot(self, provider_event_id: str) -> LiveSnapshot:
        fx = self._get("/fixtures", {"id": provider_event_id}, ttl=10)["response"][0]
        return self.live_details(fx)[0]

    def live_details(self, fx: dict) -> tuple[LiveSnapshot, list[dict]]:
        """Snapshot of one raw fixture plus its raw statistics (two requests)."""
        provider_event_id = fx["fixture"]["id"]
        observed = datetime.now(timezone.utc)
        events = self._get("/fixtures/events", {"fixture": provider_event_id}, ttl=10).get("response", [])
        stats = self._get("/fixtures/statistics", {"fixture": provider_event_id}, ttl=10).get("response", [])
        home_id = fx["teams"]["home"]["id"]
        reds = {"home": 0, "away": 0}
        for e in events:
            if e.get("type") == "Card" and e.get("detail") in ("Red Card", "Second Yellow card"):
                reds["home" if e["team"]["id"] == home_id else "away"] += 1
        xg = {"home": None, "away": None}
        for block in stats:
            side = "home" if block["team"]["id"] == home_id else "away"
            for s in block.get("statistics", []):
                if s.get("type") == "expected_goals" and s.get("value") not in (None, ""):
                    xg[side] = float(s["value"])
        if xg["home"] is None or xg["away"] is None:
            xg = {"home": None, "away": None}  # never use one side only
        return LiveSnapshot(
            provider=self.name, provider_event_id=str(provider_event_id),
            minute=float(fx["fixture"]["status"].get("elapsed") or 0),
            home_goals=fx["goals"]["home"] or 0, away_goals=fx["goals"]["away"] or 0,
            observed_at=observed, mode="real", home_red_cards=reds["home"], away_red_cards=reds["away"],
            home_xg=xg["home"], away_xg=xg["away"], events=events,
        ), stats

    def _fixture(self, f: dict, competition: str) -> Fixture:
        return Fixture(
            provider=self.name, provider_event_id=str(f["fixture"]["id"]), competition=competition,
            kickoff=_ts(f["fixture"]["date"]), home=f["teams"]["home"]["name"], away=f["teams"]["away"]["name"],
            mode="real", status=_STATUS.get(f["fixture"]["status"]["short"], "scheduled"),
            home_goals=f["goals"]["home"], away_goals=f["goals"]["away"],
        )


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
