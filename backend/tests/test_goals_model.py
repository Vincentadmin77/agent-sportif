from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sportai.engine.football import goals_model as gm


def synthetic_history(seed=3, seasons=4, n_teams=14):
    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    att = rng.normal(0, 0.3, n_teams)
    dfc = rng.normal(0, 0.25, n_teams)
    out, fid = [], 0
    t = datetime(2022, 8, 6, 15, tzinfo=timezone.utc)
    for s in range(seasons):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if i == j:
                    continue
                lam, mu = np.exp(0.3 + att[i] - dfc[j]), np.exp(0.05 + att[j] - dfc[i])
                hg, ag = int(rng.poisson(lam)), int(rng.poisson(mu))
                sh_h, sh_a = int(rng.poisson(9 * lam)), int(rng.poisson(9 * mu))
                out.append(gm.Match(fid, "XX", 2022 + s, t, h, a, hg, ag, xg_h=float(lam), xg_a=float(mu),
                                    sot_h=float(rng.poisson(3 * lam)), sot_a=float(rng.poisson(3 * mu)),
                                    sh_h=float(sh_h), sh_a=float(sh_a)))
                fid += 1
                t += timedelta(hours=12)
        t += timedelta(days=40)
    return out


@pytest.fixture(scope="module")
def history():
    return synthetic_history()


@pytest.fixture(scope="module")
def model(history):
    as_of = history[-1].kickoff - timedelta(days=30)
    return gm.GoalsModel.fit({"XX": history}, as_of), as_of


def test_features_use_only_earlier_matches(history):
    base = gm.build_features(history)
    k = 400
    changed = list(history)
    changed[k] = replace(changed[k], hg=9, ag=7, xg_h=5.0, sh_h=40.0)
    alt = gm.build_features(changed)
    # the changed match's own features and every earlier match's are untouched
    for m in history[: k + 1]:
        assert alt[m.fid] == base[m.fid]
    # the next match of the same home team does see it
    nxt = next(m for m in history[k + 1:] if history[k].home in (m.home, m.away))
    assert alt[nxt.fid] != base[nxt.fid]


def test_shrinkage_toward_league_prior_without_matches(history):
    feats = gm.build_features(history)
    first_of_season = next(m for m in history if m.season == 2023)
    f = feats[first_of_season.fid]
    # no current-season home match yet: the home team's rate is the league prior
    assert f["h_home_gf"] == pytest.approx(f["a_away_ga"])
    for key in ("xgf_h", "sotf_a", "sh_h_for", "btts_h", "o25_a", "shcv_h", "league_rate"):
        assert np.isfinite(f[key])


def test_walk_forward_dc_probabilities(history):
    wf = gm.walk_forward_dc(history, 2023)
    assert len(wf) > 300
    for r in list(wf.values())[:50]:
        p = r["dc"]
        assert 0 < p["over_3.5"] < p["over_2.5"] < p["over_1.5"] < 1
        assert 0 < r["base"]["btts_yes"] < 1


def test_logistic_fit_recovers_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(4000, 2))
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.5 + 1.2 * X[:, 0])))).astype(float)
    b = gm.fit_logistic(X, y, l2=1e-4)
    assert b[0] == pytest.approx(0.5, abs=0.15)
    assert b[1] == pytest.approx(1.2, abs=0.15)
    assert abs(b[2]) < 0.15


def test_goals_model_predicts_validated_markets(model, history):
    m, as_of = model
    fx = next(x for x in history if x.kickoff >= as_of)
    p = m.predict(history, fx.home, fx.away, fx.kickoff, fx.season, markets=tuple(gm.MARKETS))
    assert set(p) == set(gm.MARKETS)
    assert 0 < p["over_3.5"] < p["over_2.5"] < p["over_1.5"] < 1
    assert 0 < p["btts_yes"] < 1
    default = m.predict(history, fx.home, fx.away, fx.kickoff, fx.season)
    assert set(default) == set(gm.VALIDATED_MARKETS)


def test_goals_model_ignores_future_and_roundtrips(model, history):
    m, as_of = model
    fx = next(x for x in history if x.kickoff >= as_of)
    past_only = [x for x in history if x.kickoff < fx.kickoff]
    a = m.predict(history, fx.home, fx.away, fx.kickoff, fx.season)
    b = m.predict(past_only, fx.home, fx.away, fx.kickoff, fx.season)
    assert a == b
    m2 = gm.GoalsModel.from_dict(m.to_dict())
    assert m2.predict(past_only, fx.home, fx.away, fx.kickoff, fx.season) == b


def test_unknown_team_returns_none(model, history):
    m, as_of = model
    assert m.predict(history, "Promoted FC", "T01", as_of, 2024) is None
