"""The Odds API (https://the-odds-api.com) — bookmaker odds, including
Winamax (`winamax_fr`), Betclic, Unibet, PMU and NetBet in region `fr`.

Endpoint used: GET /v4/sports/{sport_key}/odds
    ?apiKey=...&regions=fr&markets=h2h,totals&oddsFormat=decimal
Each request costs credits (markets x regions); quota headers are kept in
`last_quota`. Terms (checked 2026-10-09): display in a commercial app is
allowed when the data is not the primary product sold; resale of the raw feed
is forbidden.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from sportai.connectors.base import Fixture, OddsQuote, ProviderNotConfigured
from sportai.connectors.http import CachedClient

BASE_URL = "https://api.the-odds-api.com"

# Internal competition code -> The Odds API sport key.
SPORT_KEYS = {
    "FR1": "soccer_france_ligue_one",
    "EN1": "soccer_epl",
    "ES1": "soccer_spain_la_liga",
    "DE1": "soccer_germany_bundesliga",
    "IT1": "soccer_italy_serie_a",
    "UCL": "soccer_uefa_champs_league",
    "UEL": "soccer_uefa_europa_league",
    "BR1": "soccer_brazil_campeonato",
    "TR1": "soccer_turkey_super_league",
    "ENC": "soccer_england_efl_cup",
}


# Owner, 2026-10-28: positions only on odds a French player can take, i.e. bookmakers licensed in France (ANJ).
# Their prices come from this feed's region "fr" (Winamax, Betclic, Unibet, PMU, NetBet), keys ending in "_fr";
# API-Football's bookmakers (Bet365, Pinnacle, 1xBet... and the international Unibet / NetBet) are not French sites.
# Every bookmaker still counts for the market's de-vigged chance. FRENCH_ODDS_ONLY=0 turns the rule off.
NBA_SPORT_KEY = "basketball_nba"


# The French result and goal markets the league endpoint lacks, read match by match (1 credit a market a match), at
# the last pass before kick-off only (FRENCH_EXTRA_WITHIN). Checked 2026-10-28 on Parma - Udinese: region fr returns
# both teams score (NetBet) but no double chance nor draw no bet, so only btts is asked (1 credit a match).
FRENCH_EXTRA_MARKETS = os.getenv("FRENCH_EXTRA_MARKETS", "btts")
FRENCH_EXTRA_WITHIN = timedelta(minutes=75)


def french_only() -> bool:
    """Positions on French sites' odds only. Off since the owner's 2026-10-29 decision: French players also play on
    foreign sites, so the official position takes the best odds of every bookmaker again (FRENCH_ODDS_ONLY=1 restores
    the 2026-10-28 rule)."""
    return os.getenv("FRENCH_ODDS_ONLY", "0") != "0"


def french_tracking() -> bool:
    """French sites' odds still read (The Odds API, region fr) to keep the same positions at a French price apart,
    for comparison (owner 2026-10-29). FRENCH_TRACKING=0 stops it and saves the credits."""
    return french_only() or os.getenv("FRENCH_TRACKING", "1") != "0"


# owner 2026-10-29: no bookmaker name nor letter on the site, a coloured dot beside the odds for the country of the
# site's licence (blue: approved in France). Approximate: the main licence of each brand, "autre" when unsure.
BOOKMAKER_COUNTRY = {
    **{b: "royaume_uni" for b in ("betfair", "william hill", "betfair sb", "bet365", "ladbrokes", "betfred", "betvictor", "betway",
                                  "888sport", "sportingbet", "coral", "skybet", "paddypower", "boylesports")},
    **{b: "malte" for b in ("10bet", "unibet", "betsson", "nordicbet", "comeon", "netbet", "expekt", "interwetten",
                            "betano", "casumo", "leovegas", "mrgreen", "coolbet", "marathonbet")},
    **{b: "curacao" for b in ("pinnacle", "1xbet", "dafabet", "188bet", "sbo", "bovada", "5dimes", "intertops",
                              "betcris", "betonline", "betonlineag", "betanysports", "gtbets")},
}


def bookmaker_country(bookmaker: str | None) -> str | None:
    """france, royaume_uni, malte, curacao or autre: what the site's coloured dot shows instead of the bookmaker."""
    key = str(bookmaker or "").strip().lower()
    if not key:
        return None
    if is_french(key):
        return "france"
    key = key.removesuffix("_uk").removesuffix("_eu").replace("_", " ")
    return BOOKMAKER_COUNTRY.get(key) or BOOKMAKER_COUNTRY.get(key.replace(" ", "")) or "autre"


def is_french(bookmaker: str | None) -> bool:
    return str(bookmaker or "").endswith("_fr")


class TheOddsApiProvider:
    name = "the-odds-api"

    def __init__(self, api_key: str | None, regions: str = "fr", bookmakers: list[str] | None = None,
                 cache_ttl: float = 120.0, odds_validity: timedelta = timedelta(minutes=15),
                 transport: httpx.BaseTransport | None = None, markets: str = "h2h,totals"):
        if not api_key:
            raise ProviderNotConfigured("THE_ODDS_API_KEY is not set")
        self._key = api_key
        self._regions = regions
        self._markets = markets
        self._bookmakers = bookmakers
        self._ttl = cache_ttl
        self._validity = odds_validity
        self._http = CachedClient(BASE_URL, transport=transport)
        self.last_quota: dict[str, str] = {}
        self.last_events: list[dict] = []

    def odds(self, competition: str, live: bool = False) -> list[OddsQuote]:
        sport = SPORT_KEYS[competition]
        params = {"apiKey": self._key, "markets": self._markets, "oddsFormat": "decimal", "dateFormat": "iso"}
        if self._bookmakers:
            params["bookmakers"] = ",".join(self._bookmakers)
        else:
            params["regions"] = self._regions
        data = self._http.get_json(f"/v4/sports/{sport}/odds", params, ttl=self._ttl)
        self.last_quota = {k: v for k, v in self._http.last_headers.items() if k.lower().startswith("x-requests")}
        self.last_events = data
        return list(parse_odds(data, self._validity, live))

    def raw_odds(self, sport_key: str) -> list[dict]:
        """The feed's events with their bookmakers as returned (any sport key; credits: markets x regions)."""
        params = {"apiKey": self._key, "markets": self._markets, "oddsFormat": "decimal", "dateFormat": "iso",
                  "regions": self._regions}
        data = self._http.get_json(f"/v4/sports/{sport_key}/odds", params, ttl=self._ttl)
        self.last_quota = {k: v for k, v in self._http.last_headers.items() if k.lower().startswith("x-requests")}
        return data

    def event_odds(self, competition: str, event_id: str, markets: str = "btts,totals") -> list[OddsQuote]:
        """Odds for one match. "Both teams to score" is only offered here, not on the league endpoint;
        each market costs one credit per match."""
        params = {"apiKey": self._key, "markets": markets, "oddsFormat": "decimal", "dateFormat": "iso"}
        if self._bookmakers:
            params["bookmakers"] = ",".join(self._bookmakers)
        else:
            params["regions"] = self._regions
        data = self._http.get_json(f"/v4/sports/{SPORT_KEYS[competition]}/events/{event_id}/odds", params, ttl=self._ttl)
        self.last_quota = {k: v for k, v in self._http.last_headers.items() if k.lower().startswith("x-requests")}
        return list(parse_odds([data], self._validity))

    def scores(self, competition: str, completed_too: bool = False) -> list[dict]:
        """Live scores (1 credit); with completed_too, also matches finished in the last day (2 credits)."""
        params = {"apiKey": self._key, "dateFormat": "iso"}
        if completed_too:
            params["daysFrom"] = 1
        data = self._http.get_json(f"/v4/sports/{SPORT_KEYS[competition]}/scores", params, ttl=30)
        self.last_quota = {k: v for k, v in self._http.last_headers.items() if k.lower().startswith("x-requests")}
        return data


def fixtures_from_events(events: list[dict], competition: str, now: datetime,
                         horizon: timedelta = timedelta(hours=48)) -> list[Fixture]:
    """Upcoming matches as listed by the odds feed itself: the fixture source
    when no fixtures provider is available. Matches already under way are
    marked live so the pre-match analysis skips them."""
    out = []
    for ev in events:
        kickoff = _ts(ev["commence_time"])
        if kickoff > now + horizon:
            continue
        out.append(Fixture(provider=TheOddsApiProvider.name, provider_event_id=ev["id"], competition=competition,
                           kickoff=kickoff, home=ev["home_team"], away=ev["away_team"], mode="real",
                           status="scheduled" if kickoff > now else "live"))
    return out


def parse_odds(events: list[dict], validity: timedelta, live: bool = False):
    for ev in events:
        home, away = ev["home_team"], ev["away_team"]
        kickoff = _ts(ev["commence_time"])
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                observed = _ts(mk.get("last_update") or bk["last_update"])
                for oc in mk.get("outcomes", []):
                    mapped = _map(mk["key"], oc, home, away)
                    if mapped is None:
                        continue
                    market, selection, line = mapped
                    yield OddsQuote(
                        provider=TheOddsApiProvider.name, bookmaker=bk["key"], provider_event_id=ev["id"],
                        home=home, away=away, kickoff=kickoff, market=market, selection=selection, line=line,
                        odds=float(oc["price"]), observed_at=observed, mode="real",
                        expires_at=observed + validity, is_live=live,
                        validation_status="valid" if float(oc["price"]) > 1.0 else "rejected",
                    )


def _map(key: str, oc: dict, home: str, away: str):
    if key == "h2h":
        sel = {home: "home", away: "away", "Draw": "draw"}.get(oc["name"])
        return None if sel is None else ("1x2", sel, None)
    if key == "btts":
        sel = {"Yes": "yes", "No": "no"}.get(oc["name"])
        return None if sel is None else ("btts", sel, None)
    if key == "draw_no_bet":
        sel = {home: "home", away: "away"}.get(oc["name"])
        return None if sel is None else ("dnb", sel, None)
    if key == "double_chance":
        name = oc["name"]
        sides = frozenset(s for s, words in (("home", (home, "Home")), ("draw", ("Draw", "Tie")), ("away", (away, "Away")))
                          if any(w in name for w in words))
        sel = {frozenset({"home", "draw"}): "1X", frozenset({"draw", "away"}): "X2",
               frozenset({"home", "away"}): "12"}.get(sides)
        return None if sel is None else ("double_chance", sel, None)
    if key == "totals" and oc.get("point") is not None:
        return ("totals", oc["name"].lower(), float(oc["point"]))
    return None


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
