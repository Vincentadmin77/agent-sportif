import pytest

from sportai.engine.value import (
    Outcome, devig_power, devig_proportional, devig_shin, expected_value, fair_odds,
    implied_probability, overround,
)


def test_binary_formulas_match_spec():
    o = Outcome(win=0.55)
    assert implied_probability(2.0) == 0.5
    assert fair_odds(o) == pytest.approx(1 / 0.55)
    assert expected_value(o, 2.0) == pytest.approx(0.55 * 2.0 - 1)


def test_high_probability_is_not_positive_ev():
    assert expected_value(Outcome(win=0.70), 1.35) < 0


def test_push_market():
    o = Outcome(win=0.45, push=0.28)
    assert o.lose == pytest.approx(0.27)
    assert expected_value(o, 1.60) == pytest.approx(0.45 * 0.60 - 0.27)
    assert expected_value(o, fair_odds(o)) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("method", [devig_proportional, devig_power, devig_shin])
def test_devig_sums_to_one_and_keeps_order(method):
    odds = [1.80, 3.60, 4.50]
    p = method(odds)
    assert sum(p) == pytest.approx(1.0, abs=1e-9)
    assert p[0] > p[1] > p[2]
    assert overround(odds) > 0


def test_shin_shifts_margin_to_longshot():
    odds = [1.25, 5.50, 11.0]
    prop, shin = devig_proportional(odds), devig_shin(odds)
    assert shin[2] < prop[2] and shin[0] > prop[0]


def test_invalid_odds_rejected():
    with pytest.raises(ValueError):
        implied_probability(1.0)
