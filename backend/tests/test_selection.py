from datetime import datetime, timedelta, timezone

import numpy as np

from sportai.engine.value import Outcome
from sportai.selection.engine import Candidate, SelectionConfig, select

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def cand(match="m1", market="1x2", sel="home", odds=1.80, p=0.62, spread=0.02, mfp=None, quality=0.9, age=60, push=0.0):
    rng = np.random.default_rng(0)
    return Candidate(
        match_id=match, match_label=match, market=market, selection=sel, line=None, odds=odds,
        bookmaker="bk", odds_observed_at=NOW - timedelta(seconds=age), odds_expires_at=None,
        outcome=Outcome(win=p, push=push), win_prob_samples=rng.normal(p, spread, 400), push_prob=push,
        market_fair_prob=mfp, data_quality=quality, data_mode="real",
    )


def test_no_edge_means_no_selection():
    r = select([cand(p=0.52, odds=1.85), cand(match="m2", p=0.40, odds=2.4)], now=NOW)
    assert r.status == "no_opportunity" and r.selected == []
    assert all(e.reasons for e in r.rejected)


def test_one_per_match_and_cap():
    cs = [cand(match=f"m{i}", p=0.62 + i * 0.005) for i in range(6)]
    cs.append(cand(match="m5", market="double_chance", sel="1X", odds=1.40, p=0.80))
    r = select(cs, now=NOW)
    assert len(r.selected) == 4
    assert len({e.candidate.match_id for e in r.selected}) == 4
    assert any("cap of 4" in " ".join(e.reasons) for e in r.rejected)


def test_uncertain_edge_rejected():
    r = select([cand(p=0.60, spread=0.08)], now=NOW)
    assert r.selected == []
    assert "pessimistic EV" in " ".join(r.rejected[0].reasons)


def test_stale_odds_and_divergence_and_quality_rejected():
    r = select([cand(age=4000), cand(match="m2", mfp=0.45), cand(match="m3", quality=0.3)], now=NOW)
    text = [" ".join(e.reasons) for e in r.rejected]
    assert r.selected == []
    assert "old" in text[0] and "divergence" in text[1] and "data quality" in text[2]


def test_higher_odds_need_stronger_edge():
    weak = cand(odds=2.6, p=0.41, spread=0.02)  # EV 6.6% but pessimistic edge thin
    r = select([weak], now=NOW)
    assert r.selected == []
