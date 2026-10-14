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
from sportai.engine.football.markets import MarketKey, likely_score, outcome, settle_score, weights  # noqa: E402

SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TEST_FROM = "2020-21"  # earlier seasons are warm-up only

# Every market family the model prices from the score (owner, 2026-10-14), each as a few yes/no events without a
# refund. A family's forecasts are compared with the base rate of each event over the same training window (the
# 760 matches before the week): the test that 1X2 passes and over 2.5 fails. Beating a base rate is not beating
# the bookmakers: these results need historical odds of each market before any market can be called validated.
FAMILY_EVENTS = {
    "totals": [MarketKey("totals", "over", x) for x in (1.5, 2.5, 3.5)],
    "btts": [MarketKey("btts", "yes")],
    "team_total_home": [MarketKey("team_total_home", "over", x) for x in (0.5, 1.5, 2.5)],
    "team_total_away": [MarketKey("team_total_away", "over", x) for x in (0.5, 1.5, 2.5)],
    "asian_handicap": [MarketKey("asian_handicap", "home", x) for x in (-1.5, -0.5, 0.5, 1.5)],
    "euro_handicap": [MarketKey("euro_handicap", s, -1.0) for s in ("home", "draw", "away")],
    "result_total": [MarketKey("result_total", f"{r}_{t}", 2.5) for r in ("home", "draw", "away")
                     for t in ("over", "under")],
    "result_btts": [MarketKey("result_btts", f"{r}_{y}") for r in ("home", "draw", "away") for y in ("yes", "no")],
    "total_btts": [MarketKey("total_btts", f"{t}_{y}", 2.5) for t in ("over", "under") for y in ("yes", "no")],
    "clean_sheet": [MarketKey("clean_sheet_home", "yes"), MarketKey("clean_sheet_away", "yes")],
    "win_to_nil": [MarketKey("win_to_nil_home", "yes"), MarketKey("win_to_nil_away", "yes")],
    "odd_even": [MarketKey("odd_even", "odd")],
    "exact_score": [MarketKey("exact_score", s) for s in ("0:0", "1:0", "0:1", "1:1", "2:0", "2:1", "1:2", "2:2")],
    "exact_goals": [MarketKey("exact_goals", s) for s in ("0", "1", "2", "3", "4+")],
    "goal_range": [MarketKey("goal_range", s) for s in ("0-1", "2-3", "4+")],
}


def _hit(k: MarketKey, hg: int, ag: int) -> float:
    return 1.0 if settle_score(k, hg, ag) == "gagne" else 0.0


def _family_scores(rows: dict[str, list[tuple[float, float, float]]]) -> dict:
    """Per family: Brier and log-loss of the model and of the base rate, over every (event, match)."""
    out = {}
    for fam, r in rows.items():
        a = np.array(r)
        if not len(a):
            continue
        p, b, y = np.clip(a[:, 0], 1e-6, 1 - 1e-6), np.clip(a[:, 1], 1e-6, 1 - 1e-6), a[:, 2]
        bm, bb = float(np.mean((p - y) ** 2)), float(np.mean((b - y) ** 2))
        lm = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        lb = float(-np.mean(y * np.log(b) + (1 - y) * np.log(1 - b)))
        out[fam] = {"n": int(len(a)), "brier_model": round(bm, 5), "brier_base": round(bb, 5),
                    "brier_skill": round(1 - bm / bb, 4), "logloss_model": round(lm, 5), "logloss_base": round(lb, 5)}
    return out


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
    modal, decided = [], []  # outcome of the single most likely exact score vs of the estimated score
    fam_rows: dict[str, list[tuple[float, float, float]]] = {f: [] for f in FAMILY_EVENTS}
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
            hg = np.minimum([t.home_goals for t in train[-760:]], 10)
            ag = np.minimum([t.away_goals for t in train[-760:]], 10)
            base = {k: float(np.mean(weights(k, 11)[0][hg, ag])) for keys in FAMILY_EVENTS.values() for k in keys}
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
                for fam, keys in FAMILY_EVENTS.items():
                    for k in keys:
                        fam_rows[fam].append((outcome(sm, k).win, base[k], _hit(k, m.home_goals, m.away_goals)))
                i, j = divmod(int(sm.argmax()), sm.shape[1])
                modal.append(0 if i > j else 1 if i == j else 2)
                i, j, _ = likely_score(sm)
                decided.append(0 if i > j else 1 if i == j else 2)
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
        "draw_rate_actual": round(float(np.mean(O == 1)), 4),
        "estimated_score_modal": {"draw_share": round(float(np.mean(np.array(modal) == 1)), 4),
                                  "outcome_hit": round(float(np.mean(np.array(modal) == O)), 4)},
        "estimated_score_outcome_first": {"draw_share": round(float(np.mean(np.array(decided) == 1)), 4),
                                          "outcome_hit": round(float(np.mean(np.array(decided) == O)), 4)},
        "calibration_home_win": [b.__dict__ for b in calibration(P[:, 0], (O == 0).astype(float))],
        "calibration_over25": [b.__dict__ for b in calibration(op, oh)],
        "market_families": _family_scores(fam_rows),
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
        for fam, v in r["market_families"].items():
            print(f"  {fam}: brier {v['brier_model']} vs base {v['brier_base']} (skill {v['brier_skill']:+.2%}), "
                  f"logloss {v['logloss_model']} vs {v['logloss_base']}, n={v['n']}")
    if a.out:
        a.out.write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
