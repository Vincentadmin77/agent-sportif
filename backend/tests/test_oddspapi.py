import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sportai.connectors import oddspapi
from sportai.connectors.base import Fixture
from sportai.connectors.the_odds_api import bookmaker_country, is_french

NOW = datetime(2026, 11, 3, 19, 20, tzinfo=timezone.utc)
KO = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
DATA = json.loads((Path(__file__).parent / "fixtures" / "oddspapi_odds.json").read_text())


def test_french_markets_parsed():
    qs = oddspapi.parse(DATA, "Bodo/Glimt", "Lille", KO, "ev1", NOW)
    kinds = {(q.bookmaker, q.market, q.selection, q.line) for q in qs}
    assert ("winamax_fr", "double_chance", "X2", None) in kinds
    assert ("pmu_fr", "dnb", "home", None) in kinds
    assert ("winamax_fr", "btts", "yes", None) in kinds
    assert ("pmu_fr", "totals", "over", 2.5) in kinds
    assert ("winamax_fr", "1x2", "draw", None) in kinds
    assert all(is_french(q.bookmaker) and bookmaker_country(q.bookmaker) == "france" for q in qs)


def test_match_found_by_kickoff_and_names():
    listed = [{"fixtureId": "a", "startTime": "2026-11-03T20:00:00.000Z", "participant1Name": "Bodoe/Glimt",
               "participant2Name": "Lille OSC"},
              {"fixtureId": "b", "startTime": "2026-11-03T20:00:00.000Z", "participant1Name": "Feyenoord Rotterdam",
               "participant2Name": "Inter Milano"}]
    assert oddspapi.find_fixture(listed, "Bodo/Glimt", "Lille", KO)["fixtureId"] == "a"
    assert oddspapi.find_fixture(listed, "Feyenoord", "Inter", KO)["fixtureId"] == "b"
    assert oddspapi.find_fixture(listed, "Bodo/Glimt", "Lille", KO + timedelta(hours=2)) is None


def test_budget_once_per_match_and_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setenv("ODDSPAPI", "1")
    monkeypatch.setattr(oddspapi, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(oddspapi, "CACHE", tmp_path)
    monkeypatch.setattr(oddspapi, "DAILY_CAP", 1)
    calls = []
    listed = [{"fixtureId": "a", "startTime": "2026-11-03T20:00:00.000Z", "participant1Name": "Bodoe/Glimt",
               "participant2Name": "Lille OSC"},
              {"fixtureId": "b", "startTime": "2026-11-03T20:00:00.000Z", "participant1Name": "Feyenoord",
               "participant2Name": "Inter"}]

    def fake_get(path, **params):
        calls.append(path)
        return listed if path == "/fixtures" else DATA

    monkeypatch.setattr(oddspapi, "_get", fake_get)
    fx = [Fixture("api-football", "1", "UCL", KO, "Bodo/Glimt", "Lille", "real"),
          Fixture("api-football", "2", "UCL", KO, "Feyenoord", "Inter", "real")]
    first = oddspapi.french_quotes(fx, "UCL", NOW, timedelta(minutes=75))
    assert first and calls == ["/fixtures", "/odds"]  # second match over the daily cap of 1
    again = oddspapi.french_quotes(fx, "UCL", NOW, timedelta(minutes=75))
    assert len(again) == len(first) and calls == ["/fixtures", "/odds"]  # cached, nothing spent
    oddspapi.french_quotes(fx, "UCL", NOW + timedelta(minutes=20), timedelta(minutes=75))
    assert calls == ["/fixtures", "/odds"]  # read 20 min ago: cache; daily cap reached for the other


def test_french_correspondence_beside_each_decision():
    from sportai.services.pronostic import french_match

    fr = {"btts:yes": [1.5, "winamax_fr"], "1x2:home": [1.64, "bet365_fr"], "double_chance:1X": [1.2, "pmu_fr"]}
    assert french_match("btts:yes", fr) == {"cote": 1.5, "site": "Winamax"}
    assert french_match("1x2:home", fr) == {"cote": 1.64, "site": "Bet365"}
    assert french_match("totals:under@3.5", fr) == {"cote": None, "site": None}  # read, no French site
    assert french_match("corners:over@9.5", fr) == {"cote": None, "site": None, "non_lu": True}
    assert french_match(None, fr) == {"cote": None, "site": None, "non_lu": True}


def test_not_read_yet_before_the_full_french_read():
    from sportai.services.pronostic import french_match

    morning = {"1x2:home": [1.64, "betclic_fr"], "totals:under@3.5": [1.5, "pmu_fr"]}  # The Odds API only
    assert french_match("totals:under@4.5", morning) == {"cote": None, "site": None, "non_lu": True}
    assert french_match("dnb:home", morning)["non_lu"] is True
    assert french_match("totals:under@3.5", morning) == {"cote": 1.5, "site": "PMU"}


def test_early_passes_read_while_the_last_pass_keeps_its_reserve(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setenv("ODDSPAPI", "1")
    monkeypatch.setattr(oddspapi, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(oddspapi, "CACHE", tmp_path)
    monkeypatch.setattr(oddspapi, "DAILY_CAP", 3)
    monkeypatch.setattr(oddspapi, "EARLY_RESERVE", 1)
    listed = [{"fixtureId": "a", "startTime": "2026-11-03T20:00:00.000Z", "participant1Name": "Bodoe/Glimt",
               "participant2Name": "Lille OSC"}]
    calls = []
    monkeypatch.setattr(oddspapi, "_get", lambda path, **p: calls.append(path) or (listed if path == "/fixtures" else DATA))
    fx = [Fixture("api-football", "1", "UCL", KO, "Bodo/Glimt", "Lille", "real")]
    morning = KO - timedelta(hours=11)
    assert oddspapi.french_quotes(fx, "UCL", morning, timedelta(minutes=75))  # early read: 3 left > reserve 1
    assert oddspapi.french_quotes(fx, "UCL", morning + timedelta(minutes=30), timedelta(minutes=75))  # cache
    assert calls.count("/odds") == 1
    oddspapi.french_quotes(fx, "UCL", KO - timedelta(hours=3), timedelta(minutes=75))  # 2 left > 1: read again
    assert calls.count("/odds") == 2
    assert oddspapi.french_quotes(fx, "UCL", NOW, timedelta(minutes=75))  # last pass uses the reserve
    assert calls.count("/odds") == 3
