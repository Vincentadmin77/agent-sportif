"""Does the referee improve the cards model? (owner 2026-10-28: "est-ce que la Top confiance tient compte de l'arbitre ?")

Same matches, test seasons and walk-forward as backtest_counts.py. The referee factor of a match is the referee's
cards per match in the league before that day, pulled towards the league average by SHRINK matches, over the
league average; both teams' expected cards and the total are multiplied by it. Compared bet by bet with the model
without the referee (paired Brier difference, bootstrap 95 % interval), on the same matches.

    python3 backtest_referee.py cartons|jaunes|fautes|corners|tirs|tirs_cadres [--shrink 20]

Results (2026-10-28, 2,614 test matches): shrink 5 better on 2 of 29 bets, 10 on 6, 20 on 14, worse on none in
each case; 20 kept (chosen on the test seasons themselves: optimistic). Yellow cards only: 14 of 29 at 20. Other
statistics at 20: fouls 7 of 27 better, corners 4 of 35, none worse (both kept); shots 6 of 29 worse, shots on
target unchanged (left out).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest_counts import load, outcomes  # noqa: E402
from backtest_goals import LEAGUES, SEASONS, TEST_SEASONS, Fetcher, boot_ci, brier, ll  # noqa: E402
from sportai.engine.football import counts as cnt  # noqa: E402
from sportai.engine.football.counts import STATS, CountModel, probs_for  # noqa: E402
from sportai.services.contexte import referee_keys  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stat", choices=list(STATS))
    ap.add_argument("--shrink", type=float, default=cnt.REFEREE_SHRINK)
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    a = ap.parse_args()
    names = [n for n, _ in STATS[a.stat]["markets"]]
    fetcher = Fetcher(allow_network=False)
    rows = []
    for comp in a.leagues:
        ms = load(comp, fetcher, a.stat)
        days = np.array([m["kickoff"].date().toordinal() for m in ms])
        model, week = CountModel(), None
        refs: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
        n_known = 0
        for i, m in enumerate(ms):
            d = days[i]
            lo, hi = np.searchsorted(days, d - 3 * 365), np.searchsorted(days, d)
            if d - d % 7 != week:
                model.refit(ms[lo:hi])
                week = d - d % 7
            keys = referee_keys(m.get("referee"))
            key = keys[1] if keys else None
            if m["season"] in TEST_SEASONS and hi - lo >= 200:
                mean = model.expected(m["home"], m["away"], m["kickoff"])
                if mean is not None:
                    league = model.lh + model.la
                    s, n = refs[key] if key else (0.0, 0)
                    cnt.REFEREE_SHRINK = a.shrink
                    f = cnt.referee_factor(s, n, league)
                    n_known += n >= 10
                    total = model.total_mean(sum(model.raw(m["home"], m["away"])))
                    p0 = probs_for(a.stat, *mean, model.r, model.r_total, total)
                    p1 = probs_for(a.stat, mean[0] * f, mean[1] * f, model.r, model.r_total, total * f)
                    rows.append({"comp": comp, "f": f, "p0": p0, "p1": p1, "y": outcomes(a.stat, m["h"], m["a"])})
            model.add(m)
            if key:
                refs[key][0] += m["h"] + m["a"]
                refs[key][1] += 1
        print(f"{comp}: {sum(r['comp'] == comp for r in rows)} test matches, {n_known} with a referee known on 10+ "
              f"matches", flush=True)
    P0, P1, Y = (np.array([r[k] for r in rows]) for k in ("p0", "p1", "y"))
    comps = np.array([r["comp"] for r in rows])
    fs = np.array([r["f"] for r in rows])
    print(f"referee factor: mean {fs.mean():.3f}, 10th-90th centile {np.percentile(fs, 10):.3f}-{np.percentile(fs, 90):.3f}")
    res = {"generated": datetime.now(timezone.utc).isoformat(), "stat": a.stat, "shrink": a.shrink, "n_test": len(rows),
           "facteur": {"moyenne": round(float(fs.mean()), 3), "p10": round(float(np.percentile(fs, 10)), 3),
                       "p90": round(float(np.percentile(fs, 90)), 3)}, "marches": {}}
    better = worse = 0
    for k, name in enumerate(names):
        d = brier(P1[:, k], Y[:, k]) - brier(P0[:, k], Y[:, k])
        dl = ll(P1[:, k], Y[:, k]) - ll(P0[:, k], Y[:, k])
        ci = boot_ci(d)
        per = {c: round(float(d[comps == c].mean()), 5) for c in a.leagues if (comps == c).any()}
        verdict = "mieux" if ci[1] < 0 and dl.mean() < 0 else "moins bien" if ci[0] > 0 else "pareil"
        better += verdict == "mieux"
        worse += verdict == "moins bien"
        res["marches"][name] = {"ecart_brier": round(float(d.mean()), 5), "ic95": [round(x, 5) for x in ci],
                                "ecart_logloss": round(float(dl.mean()), 5), "par_championnat": per, "verdict": verdict}
        print(f"{name:20s} dBrier {d.mean():+.5f} [{ci[0]:+.5f};{ci[1]:+.5f}] dLL {dl.mean():+.5f} {verdict}")
    print(f"better on {better}, worse on {worse} of {len(names)} bets")
    out = Path(__file__).resolve().parents[2] / "docs" / f"backtest-arbitre-{a.stat}.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
