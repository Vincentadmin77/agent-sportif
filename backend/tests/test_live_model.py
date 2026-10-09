from datetime import datetime, timedelta, timezone

import pytest

from sportai.engine.football.live import LiveConfig, LiveState, StaleDataError, check_fresh, final_score_matrix, remaining_rates
from sportai.engine.football.markets import MarketKey, outcome


def p(m, market, sel, line=None):
    return outcome(m, MarketKey(market, sel, line)).win


def test_full_time_is_certain():
    m = final_score_matrix(1.8, 0.9, LiveState(minute=95, home_goals=0, away_goals=1))
    assert p(m, "1x2", "away") == pytest.approx(1.0)


def test_favourite_conceding_first_lowers_but_keeps_comeback_chance():
    pre = final_score_matrix(2.0, 0.8, LiveState(minute=0, home_goals=0, away_goals=0))
    after = final_score_matrix(2.0, 0.8, LiveState(minute=25, home_goals=0, away_goals=1))
    assert p(after, "1x2", "home") < p(pre, "1x2", "home")
    assert 0.2 < p(after, "1x2", "home") < 0.6
    assert p(after, "totals", "over", 2.5) > p(pre, "totals", "over", 2.5)


def test_red_card_shifts_rates():
    base = remaining_rates(1.5, 1.2, LiveState(minute=30, home_goals=0, away_goals=0))
    red = remaining_rates(1.5, 1.2, LiveState(minute=30, home_goals=0, away_goals=0, home_red_cards=1))
    assert red[0] < base[0] and red[1] > base[1]


def test_dominance_in_xg_raises_rate_only_when_xg_given():
    s_no = LiveState(minute=45, home_goals=0, away_goals=1)
    s_xg = LiveState(minute=45, home_goals=0, away_goals=1, home_xg=1.6, away_xg=0.2)
    assert remaining_rates(1.5, 1.0, s_xg)[0] > remaining_rates(1.5, 1.0, s_no)[0]


def test_stale_data_blocks():
    now = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    cfg = LiveConfig(max_staleness_seconds=60)
    with pytest.raises(StaleDataError):
        check_fresh(LiveState(30, 0, 0, observed_at=now - timedelta(seconds=120)), cfg, now)
    with pytest.raises(StaleDataError):
        check_fresh(LiveState(30, 0, 0), cfg, now)
    assert check_fresh(LiveState(30, 0, 0, observed_at=now - timedelta(seconds=10)), cfg, now) == 10
