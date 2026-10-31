"""Süper Lig (TR1), analysed like the other championships since 2026-10-27 (owner). No network: wiring only."""

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

from sportai.connectors.api_football import CALENDAR_YEAR as API_CALENDAR_YEAR
from sportai.connectors.api_football import LEAGUE_IDS
from sportai.connectors.teams import normalise, same_team
from sportai.connectors.the_odds_api import SPORT_KEYS
from sportai.manage import COMPETITIONS as SEEDED
from sportai.services import calendrier, goals_refresh
from sportai.services.contexte import CALENDAR_LEAGUES, LEAGUE_NAMES
from sportai.services.dashboard import COMPETITION_NAMES
from sportai.services.runs import EUROPEAN_CUPS, _history_seasons


def _agent_pass():
    path = Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py"
    spec = importlib.util.spec_from_file_location("agent_pass_tr1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tr1_is_wired_everywhere_a_league_is():
    assert "TR1" in _agent_pass().COMPETITIONS
    assert LEAGUE_IDS["TR1"] == goals_refresh.LEAGUES["TR1"] == CALENDAR_LEAGUES["TR1"] == 203
    assert SPORT_KEYS["TR1"] == "soccer_turkey_super_league"
    assert COMPETITION_NAMES["TR1"] == LEAGUE_NAMES[203] == "Süper Lig"
    assert ("TR1", "Süper Lig") in SEEDED
    assert LEAGUE_NAMES[206] == "Coupe de Turquie"
    assert "TR1" not in EUROPEAN_CUPS  # its own model, the domestic rules


def test_tr1_plays_european_seasons():
    assert "TR1" not in API_CALENDAR_YEAR and "TR1" not in goals_refresh.CALENDAR_YEAR
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    assert calendrier.season_for("TR1", date(2026, 10, 27)) == 2026
    assert calendrier.season_for("TR1", date(2027, 3, 1)) == 2026
    assert goals_refresh.current_season("TR1", now) == 2026
    assert _history_seasons(now, "TR1") == ["2024-25", "2025-26", "2026-27"]


def test_turkish_spellings_join():
    # API-Football (2026-10) against the spellings of the odds feeds
    assert normalise("Kasımpaşa") == "kasimpasa"  # dotless i kept as an i
    pairs = [("Kasımpaşa", "Kasimpasa SK"), ("Başakşehir", "Istanbul Basaksehir"), ("Beşiktaş", "Besiktas JK"),
             ("Gençlerbirliği S.K.", "Genclerbirligi"), ("Fenerbahçe", "Fenerbahce"), ("Göztepe", "Goztepe"),
             ("Eyüpspor", "Eyupspor"), ("Gaziantep FK", "Gaziantep"), ("Çorum FK", "Corum"),
             ("Rizespor", "Caykur Rizespor"), ("İstanbulspor", "Istanbulspor"), ("Amed", "Amedspor")]
    for a, b in pairs:
        assert same_team(a, b), (a, b)
    assert not same_team("Galatasaray", "Fenerbahçe")
