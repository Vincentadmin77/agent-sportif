from datetime import date, timedelta

import numpy as np
import pytest

from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey, all_keys, outcome


def synthetic_league(seed=1, seasons=2):
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(12)]
    att = rng.normal(0, 0.3, len(teams)); att -= att.mean()
    dfc = rng.normal(0, 0.25, len(teams)); dfc -= dfc.mean()
    home = 0.25
    matches, day = [], date(2023, 8, 1)
    for _ in range(seasons):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if i == j:
                    continue
                lam = np.exp(home + att[i] + dfc[j]); mu = np.exp(att[j] + dfc[i])
                matches.append(dc.MatchResult(day, h, a, int(rng.poisson(lam)), int(rng.poisson(mu))))
                day += timedelta(days=1)
    return teams, att, dfc, home, matches


def test_fit_recovers_parameters():
    teams, att, dfc, home, matches = synthetic_league(seed=2, seasons=4)
    model = dc.fit(matches, config=dc.FitConfig(xi=0.0, ridge=0.01))
    assert model.home_adv == pytest.approx(home, abs=0.1)
    assert np.corrcoef(model.attack, att)[0, 1] > 0.85
    assert np.corrcoef(model.defence, dfc)[0, 1] > 0.75
    assert model.covariance is not None


def test_score_matrix_and_markets_are_coherent():
    m = dc.score_matrix(1.6, 1.1, -0.08)
    assert m.sum() == pytest.approx(1.0)
    p = {k.label(): outcome(m, k) for k in all_keys()}
    assert p["1x2:home"].win + p["1x2:draw"].win + p["1x2:away"].win == pytest.approx(1.0)
    assert p["totals:over@2.5"].win + p["totals:under@2.5"].win == pytest.approx(1.0)
    assert p["btts:yes"].win + p["btts:no"].win == pytest.approx(1.0)
    assert p["dnb:home"].push == pytest.approx(p["1x2:draw"].win)
    assert p["double_chance:1X"].win == pytest.approx(p["1x2:home"].win + p["1x2:draw"].win)
    assert p["1x2:home"].win > p["1x2:away"].win


def test_whole_line_total_has_push():
    m = dc.score_matrix(1.4, 1.2, 0.0)
    o = outcome(m, MarketKey("totals", "over", 2.0))
    assert o.push > 0.15
    # A quarter line is the split bet: half on 2.0, half on 2.5 (EV-equivalent single outcome).
    q = outcome(m, MarketKey("totals", "over", 2.25))
    half = outcome(m, MarketKey("totals", "over", 2.5))
    assert q.win == pytest.approx((o.win + half.win) / 2) and q.push == pytest.approx(o.push / 2)
    with pytest.raises(ValueError):
        outcome(m, MarketKey("totals", "over", None))


def test_uncertainty_samples_spread_around_centre():
    _, _, _, _, matches = synthetic_league(seed=3, seasons=1)
    model = dc.fit(matches)
    centre = outcome(model.score_matrix("T00", "T01"), MarketKey("1x2", "home")).win
    samples = [outcome(s, MarketKey("1x2", "home")).win for s in model.sample_score_matrices("T00", "T01", n=200)]
    assert np.percentile(samples, 10) < centre < np.percentile(samples, 90)
    assert abs(np.mean(samples) - centre) < 0.03


def test_refuses_tiny_samples():
    with pytest.raises(ValueError):
        dc.fit([dc.MatchResult(date(2024, 1, 1), "A", "B", 1, 0)] * 5)


def test_likely_score_picks_the_outcome_first():
    from sportai.engine.football.markets import likely_score

    # Home side favourite at 50 %: 1-1 is the single most likely exact score, yet the estimate must be a home win.
    m = dc.score_matrix(1.6, 1.0, -0.08)
    assert divmod(int(m.argmax()), m.shape[1]) == (1, 1)
    hg, ag, p = likely_score(m)
    assert hg > ag and p == round(float(m[hg, ag]), 4)
    assert likely_score(dc.score_matrix(0.9, 1.4, -0.08))[:2] == (0, 1)  # away favourite, playing away
    # A draw only when it is truly the most likely of the three outcomes.
    hg, ag, _ = likely_score(dc.score_matrix(0.5, 0.5, -0.1))
    assert hg == ag


def test_rate_calibration_stretches_the_gap_and_keeps_the_goal_level():
    assert dc.RateCalibration().apply(1.8, 0.9) == pytest.approx((1.8, 0.9))
    lam, mu = dc.RateCalibration(stretch=1.2).apply(1.8, 0.9)
    assert lam * mu == pytest.approx(1.8 * 0.9)  # geometric mean of the two rates unchanged
    assert np.log(lam / mu) == pytest.approx(1.2 * np.log(2.0))
    # Equal teams stay equal under a pure stretch; home_shift moves them towards the home side.
    assert dc.RateCalibration(stretch=1.2).apply(1.3, 1.3) == pytest.approx((1.3, 1.3))
    lam, mu = dc.RateCalibration(home_shift=0.1).apply(1.3, 1.3)
    assert np.log(lam / mu) == pytest.approx(0.1)
    # A stretch makes the favourite more likely and the draw less likely.
    raw, cal = dc.score_matrix(1.8, 0.9, -0.05), dc.score_matrix(*dc.RateCalibration(1.2).apply(1.8, 0.9), -0.05)
    assert outcome(cal, MarketKey("1x2", "home")).win > outcome(raw, MarketKey("1x2", "home")).win
    assert outcome(cal, MarketKey("1x2", "draw")).win < outcome(raw, MarketKey("1x2", "draw")).win


def test_model_calibration_applies_to_central_and_sampled_rates():
    _, _, _, _, matches = synthetic_league(seed=3, seasons=1)
    model = dc.fit(matches)
    lam, mu = model.expected_goals("T00", "T01")
    raw_samples = model.sample_score_matrices("T00", "T01", n=50, seed=1)
    model.calibration = dc.RateCalibration(stretch=1.5)
    assert model.expected_goals("T00", "T01") == pytest.approx(dc.RateCalibration(stretch=1.5).apply(lam, mu))
    assert np.allclose(model.score_matrix("T00", "T01"), dc.score_matrix(*model.expected_goals("T00", "T01"), model.rho))
    cal_samples = model.sample_score_matrices("T00", "T01", n=50, seed=1)
    key = MarketKey("1x2", "draw")
    assert np.mean([outcome(s, key).win for s in cal_samples]) < np.mean([outcome(s, key).win for s in raw_samples])
