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
    with pytest.raises(ValueError):
        outcome(m, MarketKey("totals", "over", 2.25))


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
