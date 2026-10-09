"""Walk-forward out-of-sample backtest of the Dixon-Coles model on real results.

For every week of the test seasons, the model is refitted using only matches
played before that week, then predicts that week's matches. Scores are
compared with a naive baseline (historical home/draw/away frequencies).

Data: openfootball (public domain). It has no odds, so this backtest measures
forecast quality only, not betting profitability. Profitability needs
historical closing odds (e.g. football-data.co.uk or The Odds API history).

Usage: python scripts/backtest.py --leagues FR1 EN1 --out ../docs/backtest.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.connectors.openfootball import OpenFootballProvider  # noqa: E402
from sportai.engine.evaluation import brier_multiclass, calibration, log_loss  # noqa: E402
from sportai.engine.football import dixon_coles as dc  # noqa: E402
from sportai.engine.football.markets import MarketKey, outcome  # noqa: E402

SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TEST_FROM = "2020-21"  # earlier seasons are warm-up only


def run_league(comp: str, provider: OpenFootballProvider, xi: float) -> dict:
    results = []
    for s in SEASONS:
        try:
            results += provider.results(comp, s)
        except Exception as e:  # a missing season is reported, not invented
            print(f"  {comp} {s}: unavailable ({e})", file=sys.stderr)
    results.sort(key=lambda r: r.played_on)
    matches = [dc.MatchResult(r.played_on, r.home, r.away, r.home_goals, r.away_goals) for r in results]
    test_start = next(m.played_on for m in results if m.played_on.isoformat() >= f"{TEST_FROM[:4]}-08-01")

    probs, outs, base_probs, over_p, over_hit = [], [], [], [], []
    day = test_start
    last = matches[-1].played_on
    cfg = dc.FitConfig(xi=xi)
    while day <= last:
        week = [m for m in matches if day <= m.played_on < day + timedelta(days=7)]
        train = [m for m in matches if m.played_on < day and (day - m.played_on).days <= 3 * 365]
        if week and len(train) > 200:
            model = dc.fit(train, reference_date=day - timedelta(days=1), config=cfg, with_uncertainty=False)
            hfreq = np.bincount([0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
                                 for m in train[-760:]], minlength=3) / min(len(train), 760)
            for m in week:
                if not (model.knows(m.home) and model.knows(m.away)):
                    continue  # promoted team without enough history: skipped, as in production
                sm = model.score_matrix(m.home, m.away)
                p = [outcome(sm, MarketKey("1x2", s)).win for s in ("home", "draw", "away")]
                probs.append(p)
                base_probs.append(hfreq)
                outs.append(0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2)
                over_p.append(outcome(sm, MarketKey("totals", "over", 2.5)).win)
                over_hit.append(m.home_goals + m.away_goals > 2.5)
        day += timedelta(days=7)

    P, B, O = np.array(probs), np.array(base_probs), np.array(outs)
    op, oh = np.array(over_p), np.array(over_hit, dtype=float)
    return {
        "competition": comp,
        "n_predictions": int(len(O)),
        "test_period": f"{test_start} to {last}",
        "brier_1x2_model": round(brier_multiclass(P, O), 4),
        "brier_1x2_baseline": round(brier_multiclass(B, O), 4),
        "logloss_1x2_model": round(log_loss(P, O), 4),
        "logloss_1x2_baseline": round(log_loss(B, O), 4),
        "brier_over25_model": round(float(np.mean((op - oh) ** 2)), 4),
        "brier_over25_baseline": round(float(np.mean((oh.mean() - oh) ** 2)), 4),
        "calibration_home_win": [b.__dict__ for b in calibration(P[:, 0], (O == 0).astype(float))],
        "calibration_over25": [b.__dict__ for b in calibration(op, oh)],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["FR1", "EN1", "ES1", "DE1", "IT1"])
    ap.add_argument("--xi", type=float, default=dc.FitConfig().xi)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    prov = OpenFootballProvider()
    report = {"model": f"{dc.MODEL_NAME} {dc.MODEL_VERSION}", "xi": a.xi, "data": "openfootball (CC0), results only",
              "note": "Forecast quality only. No odds in this dataset, so no profitability claim can be made.",
              "leagues": []}
    for comp in a.leagues:
        t = time.time()
        r = run_league(comp, prov, a.xi)
        report["leagues"].append(r)
        print(f"{comp}: n={r['n_predictions']} brier {r['brier_1x2_model']} vs {r['brier_1x2_baseline']} "
              f"logloss {r['logloss_1x2_model']} vs {r['logloss_1x2_baseline']} ({time.time() - t:.0f}s)")
    if a.out:
        a.out.write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
