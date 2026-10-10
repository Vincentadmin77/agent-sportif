import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.base import ProviderNotConfigured, ProviderUnavailable
from sportai.connectors.openfootball import parse as parse_openfootball
from sportai.connectors.teams import normalise, same_team
from sportai.connectors.the_odds_api import TheOddsApiProvider, fixtures_from_events, parse_odds

FIX = Path(__file__).parent / "fixtures"


def test_parse_the_odds_api_payload():
    quotes = list(parse_odds(json.loads((FIX / "the_odds_api_odds.json").read_text()), timedelta(minutes=15)))
    assert len(quotes) == 5
    home = next(q for q in quotes if q.market == "1x2" and q.selection == "home")
    assert home.bookmaker == "winamax_fr" and home.odds == 1.42 and home.mode == "real"
    assert home.expires_at - home.observed_at == timedelta(minutes=15)
    over = next(q for q in quotes if q.market == "totals" and q.selection == "over")
    assert over.line == 2.5


def test_parse_btts_from_event_odds():
    event = {"id": "e1", "commence_time": "2026-10-11T13:00:00Z", "home_team": "Hull City", "away_team": "Everton",
             "bookmakers": [{"key": "winamax_fr", "last_update": "2026-10-10T18:50:00Z", "markets": [
                 {"key": "btts", "last_update": "2026-10-10T18:50:00Z",
                  "outcomes": [{"name": "Yes", "price": 1.72}, {"name": "No", "price": 2.05}]}]}]}
    quotes = list(parse_odds([event], timedelta(minutes=15)))
    assert {(q.market, q.selection, q.odds) for q in quotes} == {("btts", "yes", 1.72), ("btts", "no", 2.05)}


def test_the_odds_api_requires_key_and_maps_errors():
    with pytest.raises(ProviderNotConfigured):
        TheOddsApiProvider(None)
    failing = httpx.MockTransport(lambda req: httpx.Response(401, text="invalid key"))
    with pytest.raises(ProviderUnavailable):
        TheOddsApiProvider("k", transport=failing).odds("FR1")


def test_the_odds_api_request_shape_and_quota():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json=json.loads((FIX / "the_odds_api_odds.json").read_text()),
                              headers={"x-requests-remaining": "480"})

    p = TheOddsApiProvider("k", transport=httpx.MockTransport(handler))
    assert len(p.odds("FR1")) == 5
    assert "/v4/sports/soccer_france_ligue_one/odds" in seen["url"] and "regions=fr" in seen["url"]
    assert p.last_quota["x-requests-remaining"] == "480"


def test_openfootball_skips_unplayed():
    data = {"matches": [
        {"date": "2024-08-16", "team1": "Le Havre AC", "team2": "Paris Saint-Germain", "score": {"ft": [1, 4]}},
        {"date": "2026-05-01", "team1": "A", "team2": "B"},
    ]}
    res = parse_openfootball(data, "FR1")
    assert len(res) == 1 and res[0].away_goals == 4 and res[0].mode == "real"


def test_team_names_join_across_providers():
    assert same_team("Paris Saint-Germain", "Paris Saint Germain")
    assert same_team("PSG", "Paris Saint-Germain FC")
    assert same_team("Olympique de Marseille", "Marseille")
    assert normalise("RC Lens") == "lens"


def test_api_football_plan_error_is_a_provider_failure():
    # Real reply observed 2026-10-10 on the free plan: HTTP 200, empty response, reason in "errors".
    body = {"get": "fixtures", "errors": {"plan": "Free plans do not have access to this season, try from 2022 to 2024."},
            "results": 0, "paging": {"current": 1, "total": 1}, "response": []}
    p = ApiFootballProvider("k", transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with pytest.raises(ProviderUnavailable, match="Free plans"):
        p.upcoming_fixtures("FR1", season=2026)


def test_fixtures_from_odds_events():
    events = json.loads((FIX / "the_odds_api_odds.json").read_text())
    kickoff = datetime.fromisoformat(events[0]["commence_time"].replace("Z", "+00:00"))
    before = fixtures_from_events(events, "FR1", now=kickoff - timedelta(hours=2))
    assert [(f.home, f.away, f.status, f.provider_event_id) for f in before] == [
        (events[0]["home_team"], events[0]["away_team"], "scheduled", events[0]["id"])]
    assert fixtures_from_events(events, "FR1", now=kickoff + timedelta(minutes=5))[0].status == "live"
    assert fixtures_from_events(events, "FR1", now=kickoff - timedelta(hours=49)) == []
    assert len(fixtures_from_events(events, "FR1", now=kickoff - timedelta(hours=47))) == 1


def test_odds_and_openfootball_spellings_join():
    # Pairs observed on 2026-10-10: The Odds API name, openfootball name.
    pairs = [("RC Lens", "Racing Club de Lens"), ("Rennes", "Stade Rennais FC 1901"), ("Brest", "Stade Brestois 29"),
             ("Angers", "Angers SCO"), ("Troyes", "ES Troyes AC"), ("Strasbourg", "RC Strasbourg Alsace"),
             ("Brighton and Hove Albion", "Brighton & Hove Albion FC"), ("Athletic Bilbao", "Athletic Club"),
             ("Real Betis", "Real Betis Balompié"), ("Espanyol", "RCD Espanyol de Barcelona"),
             ("Alavés", "Deportivo Alavés"), ("Rayo Vallecano", "Rayo Vallecano de Madrid"),
             ("Bayer Leverkusen", "Bayer 04 Leverkusen"), ("Elversberg", "SV 07 Elversberg"),
             ("TSG Hoffenheim", "TSG 1899 Hoffenheim"), ("Inter Milan", "FC Internazionale Milano"),
             ("Bologna", "Bologna FC 1909"), ("Lazio", "SS Lazio"), ("Fiorentina", "ACF Fiorentina")]
    for odds_name, history_name in pairs:
        assert same_team(odds_name, history_name), (odds_name, history_name)
    assert not same_team("AC Milan", "FC Internazionale Milano")
    assert not same_team("Real Madrid", "Rayo Vallecano de Madrid")
