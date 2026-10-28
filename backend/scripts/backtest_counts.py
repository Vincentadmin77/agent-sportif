"""Backtest of the count markets: corners, cards, shots (owner, 2026-10-27: every bookmaker market must be studied).

For each match, each team's expected count is its own average "for" (at home for the home side, away for the away
side) times the opponent's average "against", over the league average, all from past matches only (current and
previous season, shrunk to the league average when few matches are known). The count follows a negative binomial
law whose dispersion is fitted on past matches of the league. Tested: totals and team totals over / under at the
bookmakers' usual lines, and which team has more. Reference: the league frequency over the 3 previous years.
Same test seasons and decision rule as docs/11-backtest-buts.md; results in docs/18-corners-cartons-tirs.md.

Reads only the API-Football cache (no request). Writes docs/backtest-<stat>.json.
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

from backtest_goals import (  # noqa: E402
    FINISHED, LEAGUES, SEASONS, TEST_SEASONS, Budget, Fetcher, _num, boot_ci, brier, decile_calibration, ll,
)
from sportai.engine.football.counts import CountModel, STATS, probs_for  # noqa: E402

MARGIN = 0.002


def load(comp: str, fetcher: Fetcher, stat: str) -> list[dict]:
    """Finished regular-season matches with the statistic for both teams."""
    names = STATS[stat]["feed"]
    out = []
    for season in SEASONS[comp]:
        try:
            lst = fetcher.get({"league": LEAGUES[comp], "season": season})
        except Budget:
            continue
        fx = {f["fixture"]["id"]: f for f in lst.get("response", [])
              if f["fixture"]["status"]["short"] in FINISHED and f["score"]["fulltime"]["home"] is not None
              and str(f["league"].get("round", "")).startswith("Regular Season")}
        ids = sorted(f["fixture"]["id"] for f in lst.get("response", [])
                     if f["fixture"]["status"]["short"] in FINISHED and f["score"]["fulltime"]["home"] is not None)
        for i in range(0, len(ids), 20):
            try:
                data = fetcher.get({"ids": "-".join(map(str, ids[i:i + 20]))})
            except Budget:
                continue
            for f in data.get("response", []):
                fid = f["fixture"]["id"]
                if fid not in fx:
                    continue
                hid = f["teams"]["home"]["id"]
                val = {"home": 0.0, "away": 0.0}
                seen = {"home": False, "away": False}
                for block in f.get("statistics") or []:
                    side = "home" if block["team"]["id"] == hid else "away"
                    for s in block.get("statistics") or []:
                        if s.get("type") in names:
                            v = _num(s.get("value"))
                            val[side] += v or 0.0  # API-Football gives null for 0 cards
                            seen[side] = True
                if not (seen["home"] and seen["away"]):
                    continue
                out.append({"fid": fid, "comp": comp, "season": season,
                            "kickoff": datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")),
                            "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "h": int(val["home"]), "a": int(val["away"]), "referee": f["fixture"].get("referee")})
    out.sort(key=lambda m: (m["kickoff"], m["fid"]))
    return out


def outcomes(stat: str, h: int, a: int) -> list[float]:
    return [float(f(h, a)) for _, f in STATS[stat]["markets"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stat", choices=list(STATS))
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    a = ap.parse_args()
    names = [n for n, _ in STATS[a.stat]["markets"]]
    fetcher = Fetcher(allow_network=False)
    rows, coverage, disp = [], {}, {}
    for comp in a.leagues:
        ms = load(comp, fetcher, a.stat)
        coverage[comp] = len(ms)
        Y = np.array([outcomes(a.stat, m["h"], m["a"]) for m in ms])
        cY = np.vstack([np.zeros(len(names)), np.cumsum(Y, axis=0)])
        days = np.array([m["kickoff"].date().toordinal() for m in ms])
        model = CountModel()
        week = None
        for i, m in enumerate(ms):
            d = days[i]
            lo, hi = np.searchsorted(days, d - 3 * 365), np.searchsorted(days, d)
            wk = d - d % 7
            if wk != week:  # dispersion and league averages refreshed weekly, on past matches only
                model.refit(ms[lo:hi])
                week = wk
            if m["season"] >= SEASONS[comp][1] and hi - lo >= 200:
                mean = model.expected(m["home"], m["away"], m["kickoff"])
                if mean is not None:
                    total = model.total_mean(sum(model.raw(m["home"], m["away"])))
                    rows.append({"comp": comp, "season": m["season"], "mean": mean,
                                 "p": probs_for(a.stat, *mean, model.r, model.r_total, total),
                                 "ref": ((cY[hi] - cY[lo]) / (hi - lo)).tolist(), "y": Y[i].tolist()})
            model.add(m)
        disp[comp] = {"equipe": round(model.r, 2), "total": round(model.r_total, 2)}
        print(f"{comp}: {len(ms)} matches with {a.stat}, {sum(r['comp'] == comp for r in rows)} predicted, "
              f"dispersion {model.r:.1f} / total {model.r_total:.1f}, beta {model.beta:.2f}", flush=True)
    test = [r for r in rows if r["season"] in TEST_SEASONS]
    P, Q, Yt = (np.array([r[k] for r in test]) for k in ("p", "ref", "y"))
    comps = np.array([r["comp"] for r in test])
    res = {"generated": datetime.now(timezone.utc).isoformat(), "stat": a.stat, "n_test": len(test),
           "matchs_avec_la_stat": coverage, "dispersion": disp, "marches": {}}
    for k, name in enumerate(names):
        p, q, y = P[:, k], Q[:, k], Yt[:, k]
        db, dl = brier(p, y) - brier(q, y), ll(p, y) - ll(q, y)
        per = {c: {"n": int((comps == c).sum()), "observe": round(float(y[comps == c].mean()), 4),
                   "brier_modele": round(float(brier(p, y)[comps == c].mean()), 5),
                   "brier_ref": round(float(brier(q, y)[comps == c].mean()), 5)} for c in a.leagues if (comps == c).any()}
        ci = boot_ci(db)
        valid = bool(db.mean() < 0 and dl.mean() < 0 and ci[1] < 0
                     and all(v["brier_modele"] - v["brier_ref"] <= MARGIN for v in per.values()))
        hz = p >= 0.60
        res["marches"][name] = {
            "observe": round(float(y.mean()), 4), "predit": round(float(p.mean()), 4),
            "brier_modele": round(float(brier(p, y).mean()), 5), "brier_ref": round(float(brier(q, y).mean()), 5),
            "ecart_brier": round(float(db.mean()), 5), "ecart_brier_ic95": [round(v, 5) for v in ci],
            "ecart_logloss": round(float(dl.mean()), 5), "valide": valid, "par_championnat": per,
            "calibration": decile_calibration(p, y),
            "zone_60": {"n": int(hz.sum()), "predit": round(float(p[hz].mean()), 4) if hz.any() else None,
                        "observe": round(float(y[hz].mean()), 4) if hz.any() else None}}
        z = res["marches"][name]["zone_60"]
        print(f"{name:24s} obs {y.mean():.3f} pred {p.mean():.3f} dBrier {db.mean():+.4f} [{ci[0]:+.4f};{ci[1]:+.4f}] "
              f"dLL {dl.mean():+.4f} z60 n={z['n']} {z['predit']}->{z['observe']} {'VALIDE' if valid else ''}")
    out = Path(__file__).resolve().parents[2] / "docs" / f"backtest-{a.stat}.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
