"""Walk-forward out-of-sample backtest of the cross-league Dixon-Coles model on Champions League matches.

Same protocol as scripts/backtest.py, but for the UCL set-up used in production (services/runs.py):
one model fitted on the five big leagues and the European matches together, clubs seen in fewer than
UCL_MIN_MATCHES matches left out. For every UCL week of the test seasons the model is refitted on all
domestic matches + all earlier UCL matches of the 3-year window played before that week, then predicts
that week's UCL matches whose two clubs it knows.

Data: domestic results from openfootball (CC0); UCL results from API-Football (one /fixtures call per
season, raw replies cached on disk). Knockout ties extending past 90 minutes are scored at full time
(score.fulltime), never after extra time or penalties. The key is never printed.

Usage: python scripts/backtest_ucl.py --out ../docs/backtest-ucl.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.connectors.api_football import LEAGUE_IDS, ApiFootballProvider  # noqa: E402
from sportai.connectors.openfootball import OpenFootballProvider  # noqa: E402
from sportai.connectors.teams import normalise  # noqa: E402
from sportai.engine.evaluation import brier_multiclass, calibration, log_loss  # noqa: E402
from sportai.engine.football import dixon_coles as dc  # noqa: E402
from sportai.engine.football.markets import MarketKey, outcome  # noqa: E402
from sportai.services.runs import UCL_DOMESTIC, UCL_MIN_MATCHES  # noqa: E402

DOMESTIC_SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
UCL_SEASONS = list(range(2018, 2026))  # API-Football season = first year
TEST_SEASONS = [2021, 2022, 2023, 2024, 2025]  # 2021-22 .. 2025-26; earlier seasons are warm-up only
CACHE = Path("/tmp/claude-0/ucl_cache")
FAV_EDGES = [0.70, 0.75, 0.80, 0.85, 0.90, 1.0001]


def ucl_raw(season: int, provider: ApiFootballProvider | None) -> dict:
    path = CACHE / f"fixtures_{season}.json"
    if path.exists():
        return json.loads(path.read_text())
    if provider is None:
        raise RuntimeError(f"UCL {season}: not cached and no API-Football key")
    data = provider._get("/fixtures", {"league": LEAGUE_IDS["UCL"], "season": season}, ttl=1)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def ucl_matches(provider: ApiFootballProvider | None) -> list[dict]:
    out = []
    for s in UCL_SEASONS:
        for f in ucl_raw(s, provider).get("response", []):
            if f["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
                continue
            ft = (f.get("score") or {}).get("fulltime") or {}
            if ft.get("home") is None or ft.get("away") is None:
                continue  # no 90-minute score: not used
            stage = f["league"]["round"]
            kind = ("qualifying" if any(k in stage for k in ("Qualifying", "Preliminary", "Play-offs"))
                    else "group/league" if ("Group" in stage or "League Stage" in stage)
                    else "final" if stage == "Final" else "knockout")
            out.append({"season": s, "date": date.fromisoformat(f["fixture"]["date"][:10]), "round": stage,
                        "kind": kind, "home_raw": f["teams"]["home"]["name"], "away_raw": f["teams"]["away"]["name"],
                        "home": normalise(f["teams"]["home"]["name"]), "away": normalise(f["teams"]["away"]["name"]),
                        "hg": int(ft["home"]), "ag": int(ft["away"])})
    out.sort(key=lambda m: m["date"])
    return out


def domestic_matches() -> tuple[list[dc.MatchResult], dict]:
    prov, out, missing = OpenFootballProvider(), [], []
    for comp in UCL_DOMESTIC:
        for s in DOMESTIC_SEASONS:
            try:
                out += [dc.MatchResult(r.played_on, normalise(r.home), normalise(r.away), r.home_goals, r.away_goals)
                        for r in prov.results(comp, s)]
            except Exception as e:  # a missing season is reported, not invented
                missing.append(f"{comp} {s}: {e}")
                print(f"  {comp} {s}: unavailable ({e})", file=sys.stderr)
    return out, {"missing": missing}


def res(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2


def fav_calibration(p: np.ndarray, hit: np.ndarray) -> list[dict]:
    rows = []
    for lo, hi in zip(FAV_EDGES[:-1], FAV_EDGES[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            rows.append({"lower": lo, "upper": min(hi, 1.0), "count": int(m.sum()),
                         "mean_predicted": round(float(p[m].mean()), 4), "observed_rate": round(float(hit[m].mean()), 4)})
    m = p >= 0.70
    if m.sum():
        n, obs = int(m.sum()), float(hit[m].mean())
        rows.append({"lower": 0.70, "upper": 1.0, "count": n, "mean_predicted": round(float(p[m].mean()), 4),
                     "observed_rate": round(obs, 4), "observed_95ci_halfwidth": round(1.96 * (obs * (1 - obs) / n) ** 0.5, 4)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-seasons", nargs="+", type=int, default=TEST_SEASONS)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    try:
        from sportai.config import get_settings
        provider = ApiFootballProvider(get_settings().api_football_key)
    except Exception:
        provider = None
    t0 = time.time()
    ucl = ucl_matches(provider)
    dom, dom_info = domestic_matches()
    ucl_dc = [dc.MatchResult(m["date"], m["home"], m["away"], m["hg"], m["ag"]) for m in ucl]
    allm = sorted(dom + ucl_dc, key=lambda m: m.played_on)
    print(f"data: {len(dom)} domestic, {len(ucl)} UCL matches ({time.time() - t0:.0f}s)")

    test = [m for m in ucl if m["season"] in a.test_seasons]
    weeks: dict[date, list[dict]] = {}
    for m in test:
        weeks.setdefault(m["date"] - timedelta(days=m["date"].weekday()), []).append(m)

    cfg = dc.FitConfig(min_matches_per_team=UCL_MIN_MATCHES)
    rows, skipped = [], []
    for k, (monday, week) in enumerate(sorted(weeks.items())):
        train = [m for m in allm if m.played_on < monday and (monday - m.played_on).days <= 3 * 365]
        hist = [res(m.home_goals, m.away_goals) for m in ucl_dc if m.played_on < monday and (monday - m.played_on).days <= 3 * 365]
        base = np.bincount(hist, minlength=3) / len(hist)
        model = dc.fit(train, reference_date=monday - timedelta(days=1), config=cfg, with_uncertainty=False)
        for m in week:
            unknown = [s for s in ("home", "away") if not model.knows(m[s])]
            if unknown:
                skipped.append({**m, "unknown": [m[s] for s in unknown],
                                "seen": {m[s]: model.matches_per_team.get(m[s], 0) for s in unknown}})
                continue
            sm = model.score_matrix(m["home"], m["away"])
            p = [outcome(sm, MarketKey("1x2", s)).win for s in ("home", "draw", "away")]
            rows.append({**m, "p": p, "base": base.tolist(), "out": res(m["hg"], m["ag"])})
        if k % 10 == 0:
            print(f"  week {monday}: train {len(train)}, predicted {len(rows)}, skipped {len(skipped)} "
                  f"({time.time() - t0:.0f}s)")

    P = np.array([r["p"] for r in rows])
    B = np.array([r["base"] for r in rows])
    O = np.array([r["out"] for r in rows])
    fav_side = np.where(P[:, 0] >= P[:, 2], 0, 2)
    fav_p = P[np.arange(len(P)), fav_side]
    fav_hit = (O == fav_side).astype(float)

    def scores(mask: np.ndarray) -> dict:
        if not mask.any():
            return {"n": 0}
        bm, bb = brier_multiclass(P[mask], O[mask]), brier_multiclass(B[mask], O[mask])
        return {"n": int(mask.sum()), "brier_model": round(bm, 4), "brier_baseline": round(bb, 4),
                "brier_gain_pct": round(100 * (bb - bm) / bb, 1),
                "logloss_model": round(log_loss(P[mask], O[mask]), 4),
                "logloss_baseline": round(log_loss(B[mask], O[mask]), 4)}

    seasons = np.array([r["season"] for r in rows])
    dom_set = {m.home for m in dom} | {m.away for m in dom}
    both_big5 = np.array([r["home"] in dom_set and r["away"] in dom_set for r in rows])
    kinds = np.array([r["kind"] for r in rows])
    # Clubs never known by the model in any test week, with the closest domestic spelling as a hint.
    dom_names = sorted({m.home for m in dom} | {m.away for m in dom})
    known_ever = {r["home"] for r in rows} | {r["away"] for r in rows}
    never = Counter()
    raw_name = {}
    for s in skipped:
        for t in s["unknown"]:
            if t not in known_ever:
                never[t] += 1
                raw_name[t] = s["home_raw"] if s["home"] == t else s["away_raw"]
    never_rows = [{"club": t, "api_football_name": raw_name[t], "skipped_matches": n,
                   "close_domestic_spelling": difflib.get_close_matches(t, dom_names, n=1, cutoff=0.75)}
                  for t, n in never.most_common()]

    report = {
        "model": f"{dc.MODEL_NAME} {dc.MODEL_VERSION}", "fit_config": cfg.__dict__,
        "data": {"domestic": f"openfootball {', '.join(UCL_DOMESTIC)} {DOMESTIC_SEASONS[0]}..{DOMESTIC_SEASONS[-1]}",
                 "ucl": f"API-Football league {LEAGUE_IDS['UCL']}, seasons {UCL_SEASONS[0]}..{UCL_SEASONS[-1]}, "
                        "qualifiers included, 90-minute score (score.fulltime)",
                 "n_domestic": len(dom), "n_ucl": len(ucl), "domestic_missing": dom_info["missing"]},
        "protocol": "weekly refit (once per UCL week), 3-year window, with_uncertainty=False; "
                    "baseline = home/draw/away frequencies of earlier UCL matches in the window",
        "test_seasons": [f"{s}-{str(s + 1)[-2:]}" for s in a.test_seasons],
        "n_test_matches": len(test), "n_predicted": len(rows), "n_skipped": len(skipped),
        "skipped_by_kind": dict(Counter(s["kind"] for s in skipped)),
        "overall": scores(np.ones(len(rows), bool)),
        "by_season": {f"{s}-{str(s + 1)[-2:]}": scores(seasons == s) for s in a.test_seasons},
        "by_kind": {k: scores(kinds == k) for k in sorted(set(kinds))},
        "by_clubs": {"both_from_big5": scores(both_big5), "at_least_one_outside_big5": scores(~both_big5)},
        "draw_rate_actual": round(float(np.mean(O == 1)), 4),
        "draw_rate_predicted": round(float(P[:, 1].mean()), 4),
        "home_rate_actual": round(float(np.mean(O == 0)), 4),
        "home_rate_predicted": round(float(P[:, 0].mean()), 4),
        "calibration_home_win": [b.__dict__ for b in calibration(P[:, 0], (O == 0).astype(float))],
        "calibration_favourite_70plus": fav_calibration(fav_p, fav_hit),
        "favourite_70plus_home_away": {
            side: fav_calibration(fav_p[fav_side == i], fav_hit[fav_side == i])[-1:] for side, i in (("home", 0), ("away", 2))},
        "clubs_never_known": never_rows,
        "historical_odds": "API-Football /odds returned 0 results for past UCL fixtures (fixture=1199389, "
                           "league=2 season=2023): no historical closing odds, so no profitability measure.",
        "predictions": [{"date": r["date"].isoformat(), "round": r["round"], "home": r["home_raw"], "away": r["away_raw"],
                         "score_90": f"{r['hg']}-{r['ag']}", "p": [round(x, 4) for x in r["p"]]} for r in rows],
        "skipped": [{"date": s["date"].isoformat(), "round": s["round"], "home": s["home_raw"], "away": s["away_raw"],
                     "seen_matches": s["seen"]} for s in skipped],
        "runtime_s": round(time.time() - t0),
    }
    print(json.dumps({k: report[k] for k in ("n_predicted", "n_skipped", "skipped_by_kind", "overall", "by_season",
                                              "by_kind", "by_clubs", "calibration_favourite_70plus", "runtime_s")}, indent=1, default=str))
    if a.out:
        a.out.write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
