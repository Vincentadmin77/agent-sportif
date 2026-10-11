"""Out-of-sample backtest of goal markets (BTTS, over 1.5 / 2.5 / 3.5).

Compares, per market, three forecasts on the same matches:
  a. reference: the league's historical frequency of the market (past 3 years
     only, recomputed every week);
  b. plain Dixon-Coles (refitted every week on the past 3 years, as in
     scripts/backtest.py);
  c. enriched models fitted on the past only and refitted every week:
       - "logit": one L2-regularised logistic regression per market, stacking
         the Dixon-Coles probability with team form features (current-season
         home/away goals, rolling xG / shots on target / total shots, shot
         regularity, league goal rate, team BTTS and over-2.5 rates);
       - "glm": a Poisson GLM on each side's goals with the same kind of
         features, feeding a Dixon-Coles score matrix (one model for all markets).
     Which enriched variant (and penalty) is used for the test is chosen per
     market on the validation season only (2024-25 / Brazil 2024), never on the
     test period. An ablation refits the chosen variant without shot features.

Protocol: chronological walk-forward. Features for a match use only matches
that kicked off before it. Validation = season 2024 (2024-25), test = season
2025 (2025-26) plus season 2026 up to the data snapshot.

Data: API-Football v3 (results + per-match statistics). Every raw response is
cached on disk (gzipped JSON, one file per request, wrapped with metadata) and
never downloaded twice; a hard request budget and the provider's remaining
daily quota are checked before each call. The key comes from API_FOOTBALL_KEY
and is never printed.

There are no historical goal odds here: beating a base rate is NOT beating the
bookmakers.

Usage:
  python scripts/backtest_goals.py --fetch        # download what is missing (budgeted)
  python scripts/backtest_goals.py --out ../docs/backtest-buts.json \
      --shots-table /mnt/project-files/agent-sportif/data/tirs-equipes-2026.json

To extend the current season later, move its per-league list files
(fixtures_league*_season2026.json.gz) out of the cache and run --fetch again:
only the new lists and the statistics of newly finished matches are downloaded.
Results: docs/11-backtest-buts.md. Reusable model code: sportai/engine/football/goals_model.py.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.engine.football import dixon_coles as dc  # noqa: E402
from sportai.engine.football.goals_model import (  # noqa: E402
    MARKETS, Match, Standardiser, build_features, fit_logistic, hit, logit_design, market_probs,
    predict_logistic, walk_forward_dc,
)

BASE_URL = "https://v3.football.api-sports.io"
CACHE = Path(os.environ.get("SPORTAI_GOALS_CACHE", "/mnt/project-files/agent-sportif/data/api-football"))
LEAGUES = {"FR1": 61, "EN1": 39, "ES1": 140, "DE1": 78, "IT1": 135, "BR1": 71}
SEASONS = {c: list(range(2020, 2027)) for c in ("FR1", "EN1", "ES1", "DE1", "IT1")}
SEASONS["BR1"] = list(range(2021, 2027))
VALID_SEASON = 2024
TEST_SEASONS = (2025, 2026)
FEATURE_FROM_SEASON = {c: s[1] for c, s in SEASONS.items()}  # first season is warm-up only
MAX_REQUESTS = 1500
MIN_REMAINING = 3000
FINISHED = ("FT", "AET", "PEN")
MEANINGFUL_LEAGUE_MARGIN = 0.002  # a league is "meaningfully worse" if Brier(model) - Brier(ref) > this


# --------------------------------------------------------------------------- data


class Budget(Exception):
    pass


LEDGER = CACHE / "_requests_ledger.log"


def _ledger(name: str, status: int) -> None:
    """Every HTTP request actually sent (including refused ones), for the budget report."""
    with open(LEDGER, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {status} {name}\n")


class Fetcher:
    """Budgeted, cached API-Football GET. One gzipped JSON file per request."""

    def __init__(self, allow_network: bool):
        self.allow_network = allow_network
        self.used_this_run = 0
        self.last_remaining: int | None = None
        CACHE.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def path_for(params: dict) -> Path:
        if "ids" in params:
            h = hashlib.sha1(params["ids"].encode()).hexdigest()[:16]
            return CACHE / f"fixtures_ids_{h}.json.gz"
        return CACHE / f"fixtures_league{params['league']}_season{params['season']}.json.gz"

    @staticmethod
    def requests_on_disk() -> int:
        return len(list(CACHE.glob("fixtures_*.json.gz")))

    def get(self, params: dict) -> dict:
        p = self.path_for(params)
        if p.exists():
            with gzip.open(p, "rt") as f:
                return json.load(f)["data"]
        if not self.allow_network:
            raise Budget(f"not cached and network disabled: {p.name}")
        sent = sum(1 for _ in open(LEDGER)) if LEDGER.exists() else 0
        if max(self.requests_on_disk(), sent) + 1 > MAX_REQUESTS:
            raise Budget("hard budget of API-Football requests reached")
        if self.last_remaining is not None and self.last_remaining < MIN_REMAINING:
            raise Budget(f"daily quota remaining {self.last_remaining} < {MIN_REMAINING}")
        import httpx

        key = os.environ.get("API_FOOTBALL_KEY")
        if not key:
            raise Budget("API_FOOTBALL_KEY is not set")
        for attempt in range(6):
            try:
                r = httpx.get(f"{BASE_URL}/fixtures", params=params, headers={"x-apisports-key": key}, timeout=90)
            except Exception:  # noqa: BLE001 - transient network error, retried
                if attempt == 5:
                    raise
                time.sleep(3)
                continue
            self.used_this_run += 1
            _ledger(p.name, r.status_code)
            rem = r.headers.get("x-ratelimit-requests-remaining")
            self.last_remaining = int(rem) if rem and rem.isdigit() else self.last_remaining
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {p.name}")
            data = r.json()
            errs = data.get("errors")
            if errs and "rateLimit" in (errs if isinstance(errs, dict) else {}):
                time.sleep(30)  # per-minute limit shared with the live product: back off
                continue
            if errs:
                raise RuntimeError(f"api-football refused {p.name}: {errs}")
            break
        else:
            raise Budget(f"rate limited repeatedly on {p.name}")
        meta = {"params": params, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "requests_remaining": self.last_remaining}
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as f:
            json.dump({"meta": meta, "data": data}, f)
        tmp.rename(p)
        if self.last_remaining is not None and self.last_remaining < MIN_REMAINING:
            raise Budget(f"daily quota remaining {self.last_remaining} < {MIN_REMAINING}, stopping")
        time.sleep(0.25)  # stay far below 300 requests / minute
        return data


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return None


def load_league(comp: str, fetcher: Fetcher) -> list[Match]:
    out: list[Match] = []
    for season in SEASONS[comp]:
        try:
            lst = fetcher.get({"league": LEAGUES[comp], "season": season})
        except Budget as e:
            print(f"  {comp} {season}: {e}", file=sys.stderr)
            continue
        # regular season only: relegation / title play-offs mix in lower-division teams
        fx = [f for f in lst.get("response", []) if f["fixture"]["status"]["short"] in FINISHED
              and f["score"]["fulltime"]["home"] is not None
              and str(f["league"].get("round", "")).startswith("Regular Season")]
        ids = sorted(f["fixture"]["id"] for f in lst.get("response", [])
                     if f["fixture"]["status"]["short"] in FINISHED and f["score"]["fulltime"]["home"] is not None)
        stats: dict[int, dict] = {}
        for i in range(0, len(ids), 20):
            chunk = ids[i : i + 20]
            try:
                data = fetcher.get({"ids": "-".join(map(str, chunk))})
            except Budget as e:
                print(f"  {comp} {season} stats chunk {i // 20}: {e}", file=sys.stderr)
                continue
            for f in data.get("response", []):
                stats[f["fixture"]["id"]] = _stats(f)
        for f in fx:
            ft = f["score"]["fulltime"]
            m = Match(f["fixture"]["id"], comp, season,
                      datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).astimezone(timezone.utc),
                      f["teams"]["home"]["name"], f["teams"]["away"]["name"], int(ft["home"]), int(ft["away"]))
            s = stats.get(m.fid)
            if s:
                m.xg_h, m.xg_a = s["home"].get("expected_goals"), s["away"].get("expected_goals")
                m.sot_h, m.sot_a = s["home"].get("Shots on Goal"), s["away"].get("Shots on Goal")
                m.sh_h, m.sh_a = s["home"].get("Total Shots"), s["away"].get("Total Shots")
            out.append(m)
    out.sort(key=lambda m: (m.kickoff, m.fid))
    return out


def _stats(f: dict) -> dict:
    home_id = f["teams"]["home"]["id"]
    res = {"home": {}, "away": {}}
    for block in f.get("statistics") or []:
        side = "home" if block["team"]["id"] == home_id else "away"
        for s in block.get("statistics") or []:
            if s.get("type") in ("expected_goals", "Shots on Goal", "Total Shots"):
                res[side][s["type"]] = _num(s.get("value"))
    return res


# ----------------------------------------------------------------------- features


def glm_design(rows: list[dict], side: str, featset: str = "full") -> np.ndarray:
    """Features for the Poisson GLM of one side's goals (side 'h' or 'a'); column 0 is the offset."""
    o = "a" if side == "h" else "h"
    shots = featset in ("full", "noregularity")
    regularity = featset == "full"
    if featset == "dc_only":
        return np.asarray([[math.log(r["lam"] if side == "h" else r["mu"]), 0.0] for r in rows])
    cols = []
    for r in rows:
        f = r["f"]
        gf, ga = (f["h_home_gf"], f["a_away_ga"]) if side == "h" else (f["a_away_gf"], f["h_home_ga"])
        x = [
            math.log(r["lam"] if side == "h" else r["mu"]),
            math.log(gf), math.log(ga),
            math.log(f[f"xgf_{side}"]), math.log(f[f"xga_{o}"]),
            math.log(f[f"sotf_{side}"] + 0.1), math.log(f[f"sota_{o}"] + 0.1),
            math.log(f["league_rate"]),
        ]
        if shots:
            sf, sa = (f["sh_h_for"], f["sh_a_against"]) if side == "h" else (f["sh_a_for"], f["sh_h_against"])
            x += [math.log(sf), math.log(sa)]
            if regularity:
                x += [f[f"shcv_{side}"] + f[f"shcv_{o}"], math.log(sf + sa) * (f[f"shcv_{side}"] + f[f"shcv_{o}"])]
        cols.append(x)
    return np.asarray(cols, dtype=float)


def fit_poisson(X: np.ndarray, y: np.ndarray, l2: float, offset: np.ndarray) -> np.ndarray:
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])

    def f(b):
        eta = np.clip(Xb @ b + offset, -5, 3)
        mu = np.exp(eta)
        val = np.sum(mu - y * eta) / n + l2 * (b[1:] @ b[1:]) / 2
        g = Xb.T @ (mu - y) / n
        g[1:] += l2 * b[1:]
        return val, g

    return minimize(f, np.zeros(k + 1), jac=True, method="L-BFGS-B").x


L2_GRID = (0.003, 0.03, 0.3)


def enriched_walk_forward(rows: list[dict], featset: str = "full", variants=None) -> dict[str, dict[int, float]]:
    """Weekly refit on all past rows (pooled leagues); predicts each week's rows.

    Returns {variant_name: {row_index: prob}} for variant names like
    'logit:l2=0.03:btts_yes' and 'glm:l2=0.03:btts_yes'.
    Only rows of the validation and test seasons are predicted.
    """
    order = sorted(range(len(rows)), key=lambda i: rows[i]["m"].kickoff)
    target = [i for i in order if rows[i]["m"].season >= VALID_SEASON]
    first = rows[target[0]]["m"].day
    day = first - timedelta(days=first.weekday())
    out: dict[str, dict[int, float]] = defaultdict(dict)
    Xl = {mk: logit_design(rows, mk, featset) for mk in MARKETS}
    Xh, Xa = glm_design(rows, "h", featset), glm_design(rows, "a", featset)
    Y = {mk: np.array([hit(r["m"], mk) for r in rows]) for mk in MARKETS}
    HG = np.array([r["m"].hg for r in rows], dtype=float)
    AG = np.array([r["m"].ag for r in rows], dtype=float)
    offh = np.log([r["lam"] for r in rows])
    offa = np.log([r["mu"] for r in rows])
    kos = np.array([rows[i]["m"].kickoff.timestamp() for i in order])
    order = np.array(order)
    last = max(r["m"].day for r in rows)
    variants = variants or [("logit", l) for l in L2_GRID] + [("glm", l) for l in L2_GRID]
    while day <= last:
        t0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        t1 = t0 + 7 * 86400
        tr = order[: np.searchsorted(kos, t0)]
        te = order[np.searchsorted(kos, t0) : np.searchsorted(kos, t1)]
        te = te[[rows[i]["m"].season >= VALID_SEASON for i in te]] if len(te) else te
        if len(te) and len(tr) > 500:
            for kind, l2 in variants:
                if kind == "logit":
                    for mk in MARKETS:
                        st = Standardiser(Xl[mk][tr])
                        b = fit_logistic(st(Xl[mk][tr]), Y[mk][tr], l2)
                        p = predict_logistic(b, st(Xl[mk][te]))
                        for i, pi in zip(te, p):
                            out[f"logit:l2={l2}:{mk}"][int(i)] = float(pi)
                else:
                    # GLM residual on top of the DC rate (offset = log DC rate): the
                    # enriched model can only move away from DC if the data support it.
                    sth, sta = Standardiser(Xh[tr][:, 1:]), Standardiser(Xa[tr][:, 1:])
                    bh = fit_poisson(sth(Xh[tr][:, 1:]), HG[tr], l2, offh[tr])
                    ba = fit_poisson(sta(Xa[tr][:, 1:]), AG[tr], l2, offa[tr])
                    lam = np.exp(np.clip(bh[0] + sth(Xh[te][:, 1:]) @ bh[1:] + offh[te], -5, 3))
                    mu = np.exp(np.clip(ba[0] + sta(Xa[te][:, 1:]) @ ba[1:] + offa[te], -5, 3))
                    for i, l, u in zip(te, lam, mu):
                        probs = market_probs(dc.score_matrix(l, u, rows[i]["rho"]))
                        for mk in MARKETS:
                            out[f"glm:l2={l2}:{mk}"][int(i)] = probs[mk]
        day += timedelta(days=7)
    return out


# ------------------------------------------------------------------------- metrics


def brier(p, y):
    return (p - y) ** 2


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def boot_ci(d: np.ndarray, seed: int = 0, n_boot: int = 4000, alpha: float = 0.05) -> tuple[float, float]:
    """Paired (by match) bootstrap interval of the mean of d."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    b = d[idx].mean(axis=1)
    lo, hi = np.percentile(b, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def decile_calibration(p: np.ndarray, y: np.ndarray) -> list[dict]:
    q = np.quantile(p, np.linspace(0, 1, 11))
    out = []
    for k in range(10):
        m = (p >= q[k]) & ((p < q[k + 1]) | (k == 9))
        if m.sum():
            out.append({"decile": k + 1, "n": int(m.sum()), "mean_predicted": round(float(p[m].mean()), 4),
                        "observed": round(float(y[m].mean()), 4)})
    return out


def score_block(p_model, p_dc, p_ref, y, with_ci=True) -> dict:
    r = {"n": int(len(y)), "observed_rate": round(float(y.mean()), 4)}
    for name, p in (("ref", p_ref), ("dc", p_dc), ("model", p_model)):
        r[f"brier_{name}"] = round(float(brier(p, y).mean()), 5)
        r[f"logloss_{name}"] = round(float(ll(p, y).mean()), 5)
    if with_ci:
        for name, p in (("model", p_model), ("dc", p_dc)):
            db = brier(p, y) - brier(p_ref, y)
            dl = ll(p, y) - ll(p_ref, y)
            r[f"brier_diff_{name}_vs_ref"] = round(float(db.mean()), 5)
            r[f"brier_diff_{name}_vs_ref_ci95"] = [round(v, 5) for v in boot_ci(db)]
            # Bonferroni over the 4 markets tested: 98.75% interval
            r[f"brier_diff_{name}_vs_ref_ci9875"] = [round(v, 5) for v in boot_ci(db, alpha=0.0125)]
            r[f"logloss_diff_{name}_vs_ref"] = round(float(dl.mean()), 5)
            r[f"logloss_diff_{name}_vs_ref_ci95"] = [round(v, 5) for v in boot_ci(dl, seed=1)]
        db = brier(p_model, y) - brier(p_dc, y)
        r["brier_diff_model_vs_dc"] = round(float(db.mean()), 5)
        r["brier_diff_model_vs_dc_ci95"] = [round(v, 5) for v in boot_ci(db, seed=2)]
    return r


# ---------------------------------------------------------------------- shot table


def shots_table(all_matches: dict[str, list[Match]], season: int = 2026) -> dict:
    out: dict = {}
    for comp, ms in all_matches.items():
        acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for m in ms:
            if m.season != season or m.sh_h is None or m.sh_a is None:
                continue
            acc[m.home]["home_for"].append(m.sh_h)
            acc[m.home]["home_against"].append(m.sh_a)
            acc[m.away]["away_for"].append(m.sh_a)
            acc[m.away]["away_against"].append(m.sh_h)
        lg = {}
        for team, d in sorted(acc.items()):
            row = {}
            for k in ("home_for", "home_against", "away_for", "away_against"):
                v = d.get(k, [])
                row[k] = {"n": len(v), "mean": round(float(np.mean(v)), 2) if v else None,
                          "sd": round(float(np.std(v, ddof=1)), 2) if len(v) > 1 else None}
            lg[team] = row
        out[comp] = lg
    return out


# ---------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="allow downloading uncached responses (budgeted)")
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--shots-table", type=Path)
    a = ap.parse_args()

    fetcher = Fetcher(allow_network=a.fetch)
    all_matches: dict[str, list[Match]] = {}
    for comp in a.leagues:
        ms = load_league(comp, fetcher)
        all_matches[comp] = ms
        with_xg = sum(m.xg_h is not None for m in ms)
        with_sh = sum(m.sh_h is not None for m in ms)
        print(f"{comp}: {len(ms)} matches, xG {with_xg}, shots {with_sh}  "
              f"(requests this run {fetcher.used_this_run}, on disk {Fetcher.requests_on_disk()}, "
              f"quota left {fetcher.last_remaining})", flush=True)
    if a.shots_table:
        a.shots_table.write_text(json.dumps({
            "season": 2026, "source": "API-Football fixture statistics, 'Total Shots'",
            "snapshot": max(m.day for ms in all_matches.values() for m in ms).isoformat(),
            "leagues": shots_table(all_matches)}, indent=1, ensure_ascii=False))
    if a.fetch and not a.out:
        return

    rows: list[dict] = []
    coverage = {}
    for comp, ms in all_matches.items():
        t = time.time()
        feats = build_features(ms)
        wf = walk_forward_dc(ms, FEATURE_FROM_SEASON[comp])
        for m in ms:
            if m.fid in wf:
                rows.append({"m": m, "f": feats[m.fid], **wf[m.fid]})
        by_season = defaultdict(lambda: {"matches": 0, "xg": 0, "sot": 0, "shots": 0})
        for m in ms:
            s = by_season[m.season]
            s["matches"] += 1; s["xg"] += m.xg_h is not None; s["sot"] += m.sot_h is not None
            s["shots"] += m.sh_h is not None
        coverage[comp] = dict(sorted(by_season.items()))
        print(f"{comp}: DC walk-forward {len(wf)} predictions ({time.time() - t:.0f}s)", flush=True)

    preds = enriched_walk_forward(rows, featset="full")
    y = {mk: np.array([hit(r["m"], mk) for r in rows]) for mk in MARKETS}
    seasons = np.array([r["m"].season for r in rows])
    comps = np.array([r["m"].comp for r in rows])
    p_dc = {mk: np.array([r["dc"][mk] for r in rows]) for mk in MARKETS}
    p_ref = {mk: np.array([r["base"][mk] for r in rows]) for mk in MARKETS}
    val = seasons == VALID_SEASON
    test = np.isin(seasons, TEST_SEASONS)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": "API-Football v3: results + per-match statistics (xG, shots on target, total shots)",
        "protocol": {
            "validation_season": VALID_SEASON, "test_seasons": list(TEST_SEASONS),
            "dc": "Dixon-Coles refitted weekly on the past 3 years (xi=0.0019), promoted teams without 6 matches skipped",
            "reference": "league frequency of the market over the past 3 years, recomputed weekly",
            "enriched": "logit per market or Poisson GLM (offset log DC rate) + DC score matrix; refitted weekly on "
                        "all past rows of the 6 leagues; variant and L2 chosen per market on validation log-loss",
            "decision_rule": "validated iff on the pooled test set Brier(model)<Brier(ref) and LL(model)<LL(ref) "
                             "with the 95% bootstrap CI of the Brier difference entirely < 0, and in no league "
                             f"Brier(model)-Brier(ref) > {MEANINGFUL_LEAGUE_MARGIN}",
        },
        "coverage": coverage,
        "api_requests_cached_on_disk": Fetcher.requests_on_disk(),
        "api_requests_ledger_lines": (sum(1 for _ in open(LEDGER)) if LEDGER.exists() else None),
        "markets": {},
    }
    for mk in MARKETS:
        cands = {k: v for k, v in preds.items() if k.endswith(":" + mk)}
        val_scores = {}
        for k, d in cands.items():
            idx = np.array([i for i in np.where(val)[0] if i in d])
            p = np.array([d[i] for i in idx])
            val_scores[k] = float(ll(p, y[mk][idx]).mean())
        best = min(val_scores, key=val_scores.get)
        d = preds[best]
        idx_test = np.array([i for i in np.where(test)[0] if i in d])
        pm = np.array([d[i] for i in idx_test])
        yt, pd_, pr = y[mk][idx_test], p_dc[mk][idx_test], p_ref[mk][idx_test]
        pooled = score_block(pm, pd_, pr, yt)
        by_league = {}
        for c in sorted(set(comps[idx_test])):
            mask = comps[idx_test] == c
            by_league[c] = score_block(pm[mask], pd_[mask], pr[mask], yt[mask])
        by_season = {}
        for s in TEST_SEASONS:
            mask = seasons[idx_test] == s
            if mask.sum():
                by_season[str(s)] = score_block(pm[mask], pd_[mask], pr[mask], yt[mask], with_ci=False)
        # validation-season scores of the chosen variant (for transparency)
        idx_val = np.array([i for i in np.where(val)[0] if i in d])
        val_block = score_block(np.array([d[i] for i in idx_val]), p_dc[mk][idx_val], p_ref[mk][idx_val],
                                y[mk][idx_val], with_ci=False)
        # Robustness (not part of the decision rule): a more adaptive reference
        # (league frequency over the past 365 days) and the pooled test without
        # the league where the model gains the most.
        p_ref1 = np.array([rows[i]["base1y"][mk] for i in idx_test])
        d1 = brier(pm, yt) - brier(p_ref1, yt)
        best_lg = min(by_league, key=lambda c: by_league[c]["brier_model"] - by_league[c]["brier_ref"])
        keep = comps[idx_test] != best_lg
        d2 = brier(pm[keep], yt[keep]) - brier(pr[keep], yt[keep])
        robustness = {
            "ref_1y": {"brier_ref_1y": round(float(brier(p_ref1, yt).mean()), 5),
                       "logloss_ref_1y": round(float(ll(p_ref1, yt).mean()), 5),
                       "brier_diff_model_vs_ref_1y": round(float(d1.mean()), 5),
                       "brier_diff_model_vs_ref_1y_ci95": [round(v, 5) for v in boot_ci(d1, seed=6)]},
            "without_best_league": {"excluded": best_lg, "n": int(keep.sum()),
                                    "brier_diff_model_vs_ref": round(float(d2.mean()), 5),
                                    "brier_diff_model_vs_ref_ci95": [round(v, 5) for v in boot_ci(d2, seed=7)]},
        }
        worst_league = max(by_league.items(), key=lambda kv: kv[1]["brier_model"] - kv[1]["brier_ref"])
        validated = (pooled["brier_model"] < pooled["brier_ref"] and pooled["logloss_model"] < pooled["logloss_ref"]
                     and pooled["brier_diff_model_vs_ref_ci95"][1] < 0
                     and worst_league[1]["brier_model"] - worst_league[1]["brier_ref"] <= MEANINGFUL_LEAGUE_MARGIN)
        report["markets"][mk] = {
            "chosen_variant": best.rsplit(":", 1)[0], "validation_logloss_by_variant": val_scores,
            "validation": val_block, "test_pooled": pooled, "test_by_league": by_league,
            "test_by_season": by_season,
            "calibration_model_deciles": decile_calibration(pm, yt),
            "calibration_dc_deciles": decile_calibration(pd_, yt),
            "worst_league": worst_league[0], "validated": bool(validated), "robustness": robustness,
        }
        print(f"{mk}: {best} n={pooled['n']} brier model {pooled['brier_model']} dc {pooled['brier_dc']} "
              f"ref {pooled['brier_ref']} CI {pooled['brier_diff_model_vs_ref_ci95']} | ll model "
              f"{pooled['logloss_model']} dc {pooled['logloss_dc']} ref {pooled['logloss_ref']} -> "
              f"{'VALIDATED' if validated else 'non validé'}", flush=True)

    # Ablations of the chosen variant: (1) without any total-shot / shot-regularity
    # feature, (2) without shot regularity only, (3) DC recalibration only.
    chosen = {}
    for mk, r in report["markets"].items():
        kind, l2 = r["chosen_variant"].split(":l2=")
        chosen.setdefault((kind, float(l2)), []).append(mk)
    for featset, label in (("noshots", "shots_ablation"), ("noregularity", "shot_regularity_ablation"),
                           ("dc_only", "dc_recalibration_only")):
        abl = enriched_walk_forward(rows, featset=featset, variants=list(chosen))
        for (kind, l2), mks in chosen.items():
            for mk in mks:
                d_with = preds[f"{kind}:l2={l2}:{mk}"]
                d_wo = abl[f"{kind}:l2={l2}:{mk}"]
                idx = np.array([i for i in np.where(test)[0] if i in d_with and i in d_wo])
                pw = np.array([d_with[i] for i in idx]); po = np.array([d_wo[i] for i in idx])
                yt, pr = y[mk][idx], p_ref[mk][idx]
                db = brier(pw, yt) - brier(po, yt)
                dl = ll(pw, yt) - ll(po, yt)
                report["markets"][mk][label] = {
                    "n": int(len(idx)), "featset_compared": featset,
                    "brier_full": round(float(brier(pw, yt).mean()), 5),
                    "brier_ablated": round(float(brier(po, yt).mean()), 5),
                    "logloss_full": round(float(ll(pw, yt).mean()), 5),
                    "logloss_ablated": round(float(ll(po, yt).mean()), 5),
                    "brier_ref": round(float(brier(pr, yt).mean()), 5),
                    "brier_diff_full_minus_ablated": round(float(db.mean()), 5),
                    "brier_diff_ci95": [round(v, 5) for v in boot_ci(db, seed=3)],
                    "logloss_diff_full_minus_ablated": round(float(dl.mean()), 5),
                    "logloss_diff_ci95": [round(v, 5) for v in boot_ci(dl, seed=4)],
                    "brier_diff_ablated_vs_ref_ci95": [round(v, 5) for v in boot_ci(brier(po, yt) - brier(pr, yt), seed=5)],
                }
                print(f"  {label} {mk}: brier full {report['markets'][mk][label]['brier_full']} "
                      f"ablated {report['markets'][mk][label]['brier_ablated']}", flush=True)

    if a.out:
        a.out.write_text(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
