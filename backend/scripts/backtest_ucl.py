"""Walk-forward out-of-sample backtest of the cross-league Dixon-Coles model on Champions League matches.

Same protocol as scripts/backtest.py, but for the UCL set-up used in production (services/runs.py):
one model fitted on the five big leagues and the European matches together, clubs seen in fewer than
UCL_MIN_MATCHES matches left out. For every UCL week of the test seasons the model is refitted on all
domestic matches + all earlier UCL matches of the 3-year window played before that week, then predicts
that week's UCL matches whose two clubs it knows.

Data: domestic results from openfootball (CC0); UCL results from API-Football (one /fixtures call per
season, raw replies cached on disk). Knockout ties extending past 90 minutes are scored at full time
(score.fulltime), never after extra time or penalties. The key is never printed.

Calibration (UCL_CALIBRATION in services/runs.py): the raw model under-rates favourites in the UCL. A
correction of the expected goals (dc.RateCalibration: strength-gap stretch, CALIB_PARAMS) is fitted
walk-forward: for test season S it is fitted by maximum 1X2 likelihood on the predictions of the seasons
before S only (2019-20 and 2020-21 are predicted for that purpose and never scored), then applied to S.
Raw and calibrated forecasts are both scored; the variants with an extra UCL home advantage are reported too.

Extra history (UCL_EXTRA in services/runs.py, default on, --no-extra for the five-league set-up): the other
leagues whose clubs play the Champions League and the Europa / Conference League, from API-Football (one
/fixtures call per competition and season, cached in XL_CACHE). The report then splits the matches whose
clubs only the extra history makes known ("newly_covered") and measures, on big-five vs other-league matches,
whether the other-league club is over-rated (predicted vs observed win rate).

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
from scipy.optimize import minimize  # noqa: E402

from sportai.services.runs import UCL_CALIBRATION, UCL_DOMESTIC, UCL_EXTRA, UCL_MIN_MATCHES  # noqa: E402
from sportai.connectors.api_football import CALENDAR_YEAR  # noqa: E402

DOMESTIC_SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
UCL_SEASONS = list(range(2018, 2026))  # API-Football season = first year
TEST_SEASONS = [2021, 2022, 2023, 2024, 2025]  # 2021-22 .. 2025-26; earlier seasons are warm-up only
CALIB_SEASONS = [2019, 2020]  # predicted only to fit the first calibration, never scored
CALIB_PARAMS = ("stretch",)  # adding "home_shift" did worse out of sample (2020-21 behind closed doors)
CACHE = Path("/tmp/claude-0/ucl_cache")
XL_CACHE = Path("/tmp/claude-0/xl_cache")
EXTRA_SEASONS = list(range(2018, 2027))
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


def extra_raw(comp: str, season: int, provider: ApiFootballProvider | None) -> dict:
    path = XL_CACHE / f"fixtures_league-{LEAGUE_IDS[comp]}_season-{season}.json"
    if path.exists():
        return json.loads(path.read_text())
    if provider is None:
        raise RuntimeError(f"{comp} {season}: not cached and no API-Football key")
    data = provider._get("/fixtures", {"league": LEAGUE_IDS[comp], "season": season}, ttl=1)
    XL_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def extra_matches(provider: ApiFootballProvider | None) -> tuple[list[dc.MatchResult], dict[str, str]]:
    """Other leagues and European cups (90-minute scores); league of each club (its domestic league)."""
    out, league_of = [], {}
    for comp in UCL_EXTRA:
        for s in EXTRA_SEASONS:
            if s == EXTRA_SEASONS[-1] and comp not in CALENDAR_YEAR:
                continue  # autumn-spring 2026-27 starts after the last test season
            try:
                raw = extra_raw(comp, s, provider)
            except Exception as e:  # noqa: BLE001 - reported, not invented
                print(f"  {comp} {s}: unavailable ({e})", file=sys.stderr)
                continue
            for f in raw.get("response", []):
                ft = (f.get("score") or {}).get("fulltime") or {}
                if f["fixture"]["status"]["short"] not in ("FT", "AET", "PEN") or ft.get("home") is None:
                    continue
                h, a = normalise(f["teams"]["home"]["name"]), normalise(f["teams"]["away"]["name"])
                out.append(dc.MatchResult(date.fromisoformat(f["fixture"]["date"][:10]), h, a,
                                          int(ft["home"]), int(ft["away"])))
                if comp not in ("UEL", "UECL"):
                    league_of[h] = league_of[a] = comp
    return out, league_of


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


def probs_1x2(lam: float, mu: float, rho: float, cal: dc.RateCalibration | None = None) -> list[float]:
    if cal is not None:
        lam, mu = cal.apply(lam, mu)
    sm = dc.score_matrix(lam, mu, rho)
    return [float(np.tril(sm, -1).sum()), float(np.trace(sm)), float(np.triu(sm, 1).sum())]


def fit_calibration(rows: list[dict], params: tuple[str, ...] = CALIB_PARAMS) -> dc.RateCalibration:
    """Maximum 1X2 likelihood of the correction on already-played predictions (rows: lam, mu, rho, out)."""
    def nll(x: np.ndarray) -> float:
        cal = dc.RateCalibration(**dict(zip(params, x)))
        return -sum(np.log(max(probs_1x2(r["lam"], r["mu"], r["rho"], cal)[r["out"]], 1e-12)) for r in rows)
    x0 = np.array([1.0 if p == "stretch" else 0.0 for p in params])
    res = minimize(nll, x0, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-6})
    return dc.RateCalibration(**{p: round(float(v), 4) for p, v in zip(params, res.x)})


def bucket_table(P: np.ndarray, O: np.ndarray, edges=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001)) -> list[dict]:
    """All three 1X2 outcomes pooled: predicted probability bucket vs observed frequency."""
    p, hit = P.ravel(), (np.eye(3)[O]).ravel()
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            obs, n = float(hit[m].mean()), int(m.sum())
            rows.append({"lower": lo, "upper": min(hi, 1.0), "count": n, "mean_predicted": round(float(p[m].mean()), 4),
                         "observed_rate": round(obs, 4), "observed_95ci_halfwidth": round(1.96 * (obs * (1 - obs) / n) ** 0.5, 4)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-seasons", nargs="+", type=int, default=TEST_SEASONS)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-extra", action="store_true", help="five leagues + UCL only (set-up before UCL_EXTRA)")
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
    extra, league_of = ([], {}) if a.no_extra else extra_matches(provider)
    allm = sorted(dom + ucl_dc + extra, key=lambda m: m.played_on)
    base_m = sorted(dom + ucl_dc, key=lambda m: m.played_on)  # what the five-league set-up saw
    print(f"data: {len(dom)} domestic, {len(ucl)} UCL, {len(extra)} extra matches ({time.time() - t0:.0f}s)")

    test = [m for m in ucl if m["season"] in CALIB_SEASONS + a.test_seasons]
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
        base_seen = Counter()
        for m in base_m:
            if m.played_on < monday and (monday - m.played_on).days <= 3 * 365:
                base_seen[m.home] += 1
                base_seen[m.away] += 1
        for m in week:
            unknown = [s for s in ("home", "away") if not model.knows(m[s])]
            if unknown:
                skipped.append({**m, "unknown": [m[s] for s in unknown],
                                "seen": {m[s]: model.matches_per_team.get(m[s], 0) for s in unknown}})
                continue
            sm = model.score_matrix(m["home"], m["away"])
            p = [outcome(sm, MarketKey("1x2", s)).win for s in ("home", "draw", "away")]
            lam, mu = model.expected_goals(m["home"], m["away"])
            rows.append({**m, "p": p, "base": base.tolist(), "out": res(m["hg"], m["ag"]),
                         "lam": lam, "mu": mu, "rho": model.rho,
                         "new": any(base_seen[m[s]] < UCL_MIN_MATCHES for s in ("home", "away"))})
        if k % 10 == 0:
            print(f"  week {monday}: train {len(train)}, predicted {len(rows)}, skipped {len(skipped)} "
                  f"({time.time() - t0:.0f}s)")

    # Walk-forward calibration: season S corrected with parameters fitted on the seasons before S only.
    all_rows, rows = rows, [r for r in rows if r["season"] in a.test_seasons]
    skipped = [s for s in skipped if s["season"] in a.test_seasons]
    test = [m for m in test if m["season"] in a.test_seasons]
    calib_by_season, variants = {}, {"stretch_and_home_shift": ("stretch", "home_shift"),
                                    "home_shift_only": ("home_shift",)}
    for s in a.test_seasons:
        past = [r for r in all_rows if r["season"] < s]
        cal = fit_calibration(past)
        calib_by_season[f"{s}-{str(s + 1)[-2:]}"] = {"fitted_on_matches": len(past), **cal.__dict__}
        vcal = {v: fit_calibration(past, ps) for v, ps in variants.items()}
        for r in rows:
            if r["season"] == s:
                r["pc"] = probs_1x2(r["lam"], r["mu"], r["rho"], cal)
                r["pv"] = {v: probs_1x2(r["lam"], r["mu"], r["rho"], vcal[v]) for v in variants}
    final_cal = fit_calibration(all_rows)
    print(f"walk-forward calibration: {calib_by_season}; on all {len(all_rows)} predictions: {final_cal}; "
          f"production constant: {UCL_CALIBRATION}")

    P = np.array([r["p"] for r in rows])
    PC = np.array([r["pc"] for r in rows])
    B = np.array([r["base"] for r in rows])
    O = np.array([r["out"] for r in rows])

    # Paired bootstrap of the per-match Brier difference (positive = calibration helps).
    d = np.sum((P - np.eye(3)[O]) ** 2, axis=1) - np.sum((PC - np.eye(3)[O]) ** 2, axis=1)
    boot = np.random.default_rng(0).choice(d, size=(5000, len(d))).mean(axis=1)
    brier_diff_ci = [round(float(d.mean()), 4), round(float(np.quantile(boot, 0.025)), 4),
                     round(float(np.quantile(boot, 0.975)), 4)]

    def fav(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        side = np.where(X[:, 0] >= X[:, 2], 0, 2)
        return X[np.arange(len(X)), side], (O == side).astype(float), side

    fav_p, fav_hit, fav_side = fav(P)
    favc_p, favc_hit, _ = fav(PC)

    def scores(mask: np.ndarray) -> dict:
        if not mask.any():
            return {"n": 0}
        bm, bb, bc = (brier_multiclass(X[mask], O[mask]) for X in (P, B, PC))
        return {"n": int(mask.sum()), "brier_model": round(bm, 4), "brier_baseline": round(bb, 4),
                "brier_gain_pct": round(100 * (bb - bm) / bb, 1),
                "logloss_model": round(log_loss(P[mask], O[mask]), 4),
                "logloss_baseline": round(log_loss(B[mask], O[mask]), 4),
                "brier_calibrated": round(bc, 4), "brier_gain_calibrated_pct": round(100 * (bb - bc) / bb, 1),
                "logloss_calibrated": round(log_loss(PC[mask], O[mask]), 4)}

    seasons = np.array([r["season"] for r in rows])
    dom_set = {m.home for m in dom} | {m.away for m in dom}
    both_big5 = np.array([r["home"] in dom_set and r["away"] in dom_set for r in rows])
    kinds = np.array([r["kind"] for r in rows])
    new = np.array([r["new"] for r in rows], bool)

    def other_vs_big5(X: np.ndarray, mask: np.ndarray) -> dict:
        """Big-five club vs other-league club: is the other-league club over-rated?"""
        idx = [i for i in np.flatnonzero(mask) if (rows[i]["home"] in dom_set) != (rows[i]["away"] in dom_set)]
        if not idx:
            return {"n": 0}
        side = np.array([2 if rows[i]["home"] in dom_set else 0 for i in idx])
        pw = X[idx, side]
        ow = (O[idx] == side).astype(float)
        n = len(idx)
        return {"n": n, "other_club_win_predicted": round(float(pw.mean()), 4),
                "other_club_win_observed": round(float(ow.mean()), 4),
                "observed_95ci_halfwidth": round(1.96 * float((ow.mean() * (1 - ow.mean()) / n) ** 0.5), 4),
                "brier_model": round(brier_multiclass(X[idx], O[idx]), 4),
                "brier_baseline": round(brier_multiclass(B[idx], O[idx]), 4)}
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
        "extra_history": [] if a.no_extra else list(UCL_EXTRA), "n_extra": len(extra),
        "data": {"domestic": f"openfootball {', '.join(UCL_DOMESTIC)} {DOMESTIC_SEASONS[0]}..{DOMESTIC_SEASONS[-1]}",
                 "ucl": f"API-Football league {LEAGUE_IDS['UCL']}, seasons {UCL_SEASONS[0]}..{UCL_SEASONS[-1]}, "
                        "qualifiers included, 90-minute score (score.fulltime)",
                 "n_domestic": len(dom), "n_ucl": len(ucl), "domestic_missing": dom_info["missing"]},
        "protocol": "weekly refit (once per UCL week), 3-year window, with_uncertainty=False; "
                    "baseline = home/draw/away frequencies of earlier UCL matches in the window",
        "test_seasons": [f"{s}-{str(s + 1)[-2:]}" for s in a.test_seasons],
        "n_test_matches": len(test), "n_predicted": len(rows), "n_skipped": len(skipped),
        "skipped_by_kind": dict(Counter(s["kind"] for s in skipped)),
        "calibration": {
            "method": "dc.RateCalibration on expected goals: gap log(lam/mu) -> stretch * gap, goal level kept; "
                      "fitted by maximum 1X2 likelihood on earlier seasons' predictions only (expanding window "
                      "from 2019-20), applied to the next season",
            "params": list(CALIB_PARAMS),
            "brier_difference_raw_minus_calibrated_95ci": brier_diff_ci,
            "by_test_season": calib_by_season,
            "fitted_on_all_predictions_2019_26": {"n": len(all_rows), **final_cal.__dict__},
            "production_constant": UCL_CALIBRATION.__dict__ if UCL_CALIBRATION else None,
            "variants_overall": {v: {"brier": round(brier_multiclass(np.array([r["pv"][v] for r in rows]), O), 4),
                                     "logloss": round(log_loss(np.array([r["pv"][v] for r in rows]), O), 4)}
                                 for v in variants},
        },
        "overall": scores(np.ones(len(rows), bool)),
        "by_season": {f"{s}-{str(s + 1)[-2:]}": scores(seasons == s) for s in a.test_seasons},
        "by_kind": {k: scores(kinds == k) for k in sorted(set(kinds))},
        "by_clubs": {"both_from_big5": scores(both_big5), "at_least_one_outside_big5": scores(~both_big5),
                     "newly_covered": scores(new), "known_before": scores(~new)},
        "other_league_vs_big5": {"all": other_vs_big5(P, np.ones(len(rows), bool)),
                                 "all_calibrated": other_vs_big5(PC, np.ones(len(rows), bool)),
                                 "newly_covered": other_vs_big5(P, new),
                                 "newly_covered_calibrated": other_vs_big5(PC, new)},
        "draw_rate_actual": round(float(np.mean(O == 1)), 4),
        "draw_rate_predicted": round(float(P[:, 1].mean()), 4),
        "draw_rate_predicted_calibrated": round(float(PC[:, 1].mean()), 4),
        "away_rate_actual": round(float(np.mean(O == 2)), 4),
        "away_rate_predicted": round(float(P[:, 2].mean()), 4),
        "away_rate_predicted_calibrated": round(float(PC[:, 2].mean()), 4),
        "home_rate_actual": round(float(np.mean(O == 0)), 4),
        "home_rate_predicted": round(float(P[:, 0].mean()), 4),
        "home_rate_predicted_calibrated": round(float(PC[:, 0].mean()), 4),
        "calibration_1x2_all_outcomes": bucket_table(P, O),
        "calibration_1x2_all_outcomes_calibrated": bucket_table(PC, O),
        "calibration_home_win": [b.__dict__ for b in calibration(P[:, 0], (O == 0).astype(float))],
        "calibration_home_win_calibrated": [b.__dict__ for b in calibration(PC[:, 0], (O == 0).astype(float))],
        "calibration_favourite_70plus": fav_calibration(fav_p, fav_hit),
        "calibration_favourite_70plus_calibrated": fav_calibration(favc_p, favc_hit),
        "favourite_70plus_home_away": {
            side: fav_calibration(fav_p[fav_side == i], fav_hit[fav_side == i])[-1:] for side, i in (("home", 0), ("away", 2))},
        "clubs_never_known": never_rows,
        "historical_odds": "API-Football /odds returned 0 results for past UCL fixtures (fixture=1199389, "
                           "league=2 season=2023): no historical closing odds, so no profitability measure.",
        "predictions": [{"date": r["date"].isoformat(), "round": r["round"], "home": r["home_raw"], "away": r["away_raw"],
                         "score_90": f"{r['hg']}-{r['ag']}", "p": [round(x, 4) for x in r["p"]],
                         "p_calibrated": [round(x, 4) for x in r["pc"]]} for r in rows],
        "skipped": [{"date": s["date"].isoformat(), "round": s["round"], "home": s["home_raw"], "away": s["away_raw"],
                     "seen_matches": s["seen"]} for s in skipped],
        "runtime_s": round(time.time() - t0),
    }
    print(json.dumps({k: report[k] for k in ("n_predicted", "n_skipped", "skipped_by_kind", "calibration", "overall",
                                              "by_season", "by_kind", "by_clubs", "other_league_vs_big5", "calibration_1x2_all_outcomes",
                                              "calibration_1x2_all_outcomes_calibrated", "calibration_favourite_70plus",
                                              "calibration_favourite_70plus_calibrated", "runtime_s")}, indent=1, default=str))
    if a.out:
        a.out.write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
