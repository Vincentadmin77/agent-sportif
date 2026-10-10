from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from sportai.connectors.base import OddsQuote
from sportai.connectors.simulation import SimulationProvider
from sportai.services.live import evaluate_live
from sportai.selection.engine import SelectionConfig
from sportai.services.prematch import MixedModeError, run_prematch

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def sim():
    return SimulationProvider(now=NOW)


def test_realistic_market_gives_zero_or_few_selections(sim):
    r = run_prematch(sim.results(), sim.upcoming_fixtures(), sim.odds(), now=NOW)
    assert r.mode == "simulation"
    assert len(r.selection.selected) <= 4
    assert len(r.selection.rejected) > 0
    if not r.selection.selected:
        assert r.selection.status == "no_opportunity"


def test_mispriced_market_produces_capped_uncorrelated_selections(sim):
    cfg = SelectionConfig(enabled_markets=("1x2", "double_chance", "dnb", "totals", "btts"))
    r = run_prematch(sim.results(), sim.upcoming_fixtures(), sim.odds(noise=0.25), cfg, now=NOW)
    sel = r.selection.selected
    assert 1 <= len(sel) <= 4
    assert len({e.candidate.match_id for e in sel}) == len(sel)
    for e in sel:
        assert e.ev >= 0.03 and e.ev_low >= 0 and e.candidate.data_mode == "simulation"


def test_mixing_real_and_simulated_inputs_is_refused(sim):
    q = sim.odds()
    q[0] = replace(q[0], mode="real")
    with pytest.raises(MixedModeError):
        run_prematch(sim.results(), sim.upcoming_fixtures(), q, now=NOW)


def test_live_favourite_concedes_first(sim):
    fx = sim.upcoming_fixtures()[0]
    snap = sim.live_snapshot(fx.provider_event_id, minute=25, score=(0, 1), xg=(1.1, 0.2))
    snap = replace(snap, observed_at=NOW - timedelta(seconds=5))
    q = OddsQuote("sim", "sim_book_a", fx.provider_event_id, fx.home, fx.away, fx.kickoff, "1x2", "home", None,
                  3.40, NOW - timedelta(seconds=5), "simulation", "valid", is_live=True)
    alerts = evaluate_live(fx.provider_event_id, "x", 2.0, 0.8, 0.65, 0.12, snap, [q], now=NOW)
    kinds = {a.kind for a in alerts}
    assert {"favourite_conceded_first", "dominant_but_trailing"} <= kinds
    for a in alerts:
        if a.kind == "opportunity":
            assert a.commercial and a.ev >= 0.05 and a.invalidation


def test_live_stale_feed_blocks_everything(sim):
    snap = replace(sim.live_snapshot("sim-0"), observed_at=NOW - timedelta(minutes=5))
    alerts = evaluate_live("sim-0", "x", 1.5, 1.0, 0.5, 0.2, snap, [], now=NOW)
    assert [a.kind for a in alerts] == ["stale_data"] and not alerts[0].commercial


def test_unproven_markets_disabled_by_default(sim):
    r = run_prematch(sim.results(), sim.upcoming_fixtures(), sim.odds(noise=0.25), now=NOW)
    assert all(e.candidate.market not in ("totals", "btts") for e in r.selection.selected)


def test_only_todays_matches_close_to_kickoff_are_analysed(sim):
    fixtures = sim.upcoming_fixtures()
    later = replace(fixtures[0], kickoff=NOW + timedelta(hours=4))       # today, but too early for a pass
    tomorrow = replace(fixtures[2], kickoff=NOW + timedelta(hours=14))   # 04:00 Paris time the next day
    started = replace(fixtures[1], kickoff=NOW - timedelta(minutes=5))
    r = run_prematch(sim.results(), [later, started, tomorrow] + fixtures[3:], sim.odds(), now=NOW)
    reasons = {f.fixture.provider_event_id: f.skipped_reason for f in r.fixtures}
    assert reasons[later.provider_event_id] == "more than 3.25h before kick-off"
    assert reasons[tomorrow.provider_event_id] == "not played today"
    assert reasons[started.provider_event_id] == "already kicked off"
    skipped = (later.provider_event_id, tomorrow.provider_event_id, started.provider_event_id)
    assert all(e.candidate.match_id not in skipped for e in r.selection.selected + r.selection.rejected)
    assert all(reasons[f.provider_event_id] is None for f in fixtures[3:])


def test_history_seasons_follow_each_league_calendar():
    from sportai.services.runs import _history_seasons
    assert _history_seasons(NOW, "FR1") == ["2024-25", "2025-26", "2026-27"]
    assert _history_seasons(NOW, "BR1") == ["2024", "2025", "2026"]


class _Source:
    def __init__(self, name, refuse=()):
        self.name, self.refuse = name, refuse

    def results(self, competition, season):
        from sportai.connectors.base import ProviderUnavailable
        if season in self.refuse:
            raise ProviderUnavailable(f"{self.name} refuses {season}")
        return [f"{self.name}:{season}"]


def test_history_prefers_api_football_and_falls_back_season_by_season():
    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.connectors.base import ProviderUnavailable
    from sportai.db.models import Base
    from sportai.services.runs import load_history

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    hist, used = load_history(db, "FR1", now, _Source("api-football", refuse={"2024-25"}), _Source("openfootball"))
    assert hist == ["openfootball:2024-25", "api-football:2025-26", "api-football:2026-27"]
    assert used == {"api-football", "openfootball"}
    hist, _ = load_history(db, "FR1", now, _Source("api-football", refuse={"2024-25"}), None)
    assert hist == ["api-football:2025-26", "api-football:2026-27"]
    with pytest.raises(ProviderUnavailable):
        load_history(db, "FR1", now, _Source("api-football", refuse={"2026-27"}), _Source("openfootball", refuse={"2026-27"}))
