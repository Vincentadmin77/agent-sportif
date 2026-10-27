"""Backtest of the half-time markets (owner, 2026-10-27: "tous les marchés doivent être étudiés").

Half-time score, first-half / second-half goals: the final-score model gives expected goals lam, mu for a match;
the first half is priced as two independent Poisson laws of means f * lam and f * mu, the second half with
(1 - f) * lam and (1 - f) * mu, where f is the share of goals scored before half-time in the league over the
3 previous years (past matches only). Same data, walk-forward Dixon-Coles and decision rule as
docs/11-backtest-buts.md; the reference is the league frequency of the market over the 3 previous years.

Reads only the API-Football cache of backtest_goals.py (no request). Writes docs/backtest-mi-temps.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest_goals import (  # noqa: E402
    FINISHED, LEAGUES, SEASONS, TEST_SEASONS, Budget, Fetcher, boot_ci, brier, decile_calibration, ll,
)
from sportai.engine.football.goals_model import Match, walk_forward_dc  # noqa: E402

from sportai.engine.football import halves  # noqa: E402

# (market, selection, line) tested: every half market the feed offers, the main selections of each.
KEYS = ([("ht_1x2", s, None) for s in ("home", "draw", "away")] + [("2h_1x2", s, None) for s in ("home", "draw", "away")]
        + [(h + "_dc", s, None) for h in ("ht", "2h") for s in ("1X", "X2", "12")]
        + [(h + "_totals", s, x) for h in ("ht", "2h") for x in (0.5, 1.5, 2.5) for s in ("over", "under")]
        + [(f"{h}_team_total_{t}", s, x) for h in ("ht", "2h") for t in ("home", "away") for x in (0.5, 1.5)
           for s in ("over", "under")]
        + [(h + "_btts", s, None) for h in ("ht", "2h") for s in ("yes", "no")]
        + [(h + "_odd_even", "odd", None) for h in ("ht", "2h")]
        + [("htft", f"{x}/{y}", None) for x in ("home", "draw", "away") for y in ("home", "draw", "away")]
        + [("highest_half", s, None) for s in ("1st", "2nd", "draw")]
        + [("goal_both_halves", s, None) for s in ("yes", "no")]
        + [(f"{m}_{t}", "yes", None) for m in ("team_both_halves", "win_both_halves", "win_either_half")
           for t in ("home", "away")]
        + [("btts_both_halves", "yes", None)])
NAMES = [f"{m}:{s}" + ("" if x is None else f"@{x:g}") for m, s, x in KEYS]
MARGIN = 0.002


def load(comp: str, fetcher: Fetcher) -> tuple[list[Match], dict[int, tuple[int, int]]]:
    """Regular-season finished matches with their half-time score."""
    out, ht = [], {}
    for season in SEASONS[comp]:
        try:
            lst = fetcher.get({"league": LEAGUES[comp], "season": season})
        except Budget as e:
            print(f"  {comp} {season}: {e}", file=sys.stderr)
            continue
        for f in lst.get("response", []):
            sc = f["score"]
            if (f["fixture"]["status"]["short"] not in FINISHED or sc["fulltime"]["home"] is None
                    or sc["halftime"]["home"] is None or not str(f["league"].get("round", "")).startswith("Regular Season")):
                continue
            m = Match(f["fixture"]["id"], comp, season,
                      datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).astimezone(timezone.utc),
                      f["teams"]["home"]["name"], f["teams"]["away"]["name"],
                      int(sc["fulltime"]["home"]), int(sc["fulltime"]["away"]))
            out.append(m)
            ht[m.fid] = (int(sc["halftime"]["home"]), int(sc["halftime"]["away"]))
    out.sort(key=lambda m: (m.kickoff, m.fid))
    return out, ht


def outcomes(m: Match, ht: tuple[int, int]) -> list[float]:
    h1, a1 = ht
    return [float(halves.settle(k, sel, x, h1, a1, m.hg - h1, m.ag - a1) == "gagne") for k, sel, x in KEYS]


def probs(lam: float, mu: float, f: float) -> list[float]:
    j = halves.joint(lam, mu, f)
    return [halves.outcome(j, k, sel, x).win for k, sel, x in KEYS]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "backtest-mi-temps.json")
    a = ap.parse_args()
    fetcher = Fetcher(allow_network=False)
    rows, shares = [], {}
    for comp in a.leagues:
        ms, ht = load(comp, fetcher)
        start = SEASONS[comp][1]
        pred = walk_forward_dc(ms, start)
        days = np.array([m.day.toordinal() for m in ms])
        Y = np.array([outcomes(q, ht[q.fid]) for q in ms])
        cY = np.vstack([np.zeros(len(KEYS)), np.cumsum(Y, axis=0)])
        c1 = np.concatenate([[0], np.cumsum([sum(ht[q.fid]) for q in ms])])
        ct = np.concatenate([[0], np.cumsum([q.hg + q.ag for q in ms])])
        for i, m in enumerate(ms):
            p = pred.get(m.fid)
            if p is None:
                continue
            lo = np.searchsorted(days, m.day.toordinal() - 3 * 365)
            hi = np.searchsorted(days, m.day.toordinal())  # matches before this day only
            if hi - lo < 200:
                continue
            f1 = (c1[hi] - c1[lo]) / (ct[hi] - ct[lo])
            rows.append({"comp": comp, "season": m.season, "f1": f1, "p": probs(p["lam"], p["mu"], f1),
                         "ref": ((cY[hi] - cY[lo]) / (hi - lo)).tolist(), "y": Y[i].tolist()})
        last = np.searchsorted(days, days[-1] - 3 * 365)
        shares[comp] = round(float((c1[-1] - c1[last]) / (ct[-1] - ct[last])), 3)
        print(f"{comp}: {len(ms)} matches, {sum(r['comp'] == comp for r in rows)} predicted", flush=True)

    test = [r for r in rows if r["season"] in TEST_SEASONS]
    P, Q, Yt = (np.array([r[k] for r in test]) for k in ("p", "ref", "y"))
    comps = np.array([r["comp"] for r in test])
    res = {"generated": datetime.now(timezone.utc).isoformat(), "n_test": len(test),
           "part_premiere_mi_temps_3_ans": shares, "marches": {}}
    for k, name in enumerate(NAMES):
        p, q, y = P[:, k], Q[:, k], Yt[:, k]
        db, dl = brier(p, y) - brier(q, y), ll(p, y) - ll(q, y)
        per = {c: {"n": int((comps == c).sum()), "observe": round(float(y[comps == c].mean()), 4),
                   "brier_modele": round(float(brier(p, y)[comps == c].mean()), 5),
                   "brier_ref": round(float(brier(q, y)[comps == c].mean()), 5)} for c in a.leagues}
        ci = boot_ci(db)
        valid = bool(db.mean() < 0 and dl.mean() < 0 and ci[1] < 0
                     and all(v["brier_modele"] - v["brier_ref"] <= MARGIN for v in per.values()))
        hz = p >= 0.60  # what a "Confiance élevée" bet on it would have hit
        res["marches"][name] = {
            "observe": round(float(y.mean()), 4), "predit": round(float(p.mean()), 4),
            "brier_modele": round(float(brier(p, y).mean()), 5), "brier_ref": round(float(brier(q, y).mean()), 5),
            "ecart_brier": round(float(db.mean()), 5), "ecart_brier_ic95": [round(v, 5) for v in ci],
            "ecart_logloss": round(float(dl.mean()), 5), "valide": valid, "par_championnat": per,
            "calibration": decile_calibration(p, y),
            "zone_60": {"n": int(hz.sum()), "predit": round(float(p[hz].mean()), 4) if hz.any() else None,
                        "observe": round(float(y[hz].mean()), 4) if hz.any() else None}}
        z = res["marches"][name]["zone_60"]
        print(f"{name:28s} obs {y.mean():.3f} pred {p.mean():.3f} dBrier {db.mean():+.4f} [{ci[0]:+.4f};{ci[1]:+.4f}] "
              f"dLL {dl.mean():+.4f} z60 n={z['n']} {z['predit']}->{z['observe']} {'VALIDE' if valid else ''}")
    print("shares", shares)
    a.out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
