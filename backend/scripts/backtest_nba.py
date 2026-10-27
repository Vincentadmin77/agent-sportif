"""Out-of-sample backtest of the basketball ratings model on the NBA.

Data: API-Basketball (league 12), one cached file per season in
/mnt/project-files/agent-sportif/data/api-basketball/games_nba_<season>.json.gz
(downloaded once with --fetch; the key comes from API_FOOTBALL_KEY, the same
API-Sports account, and is never printed). Pre-season, All-Star and
exhibition games are dropped (teams with fewer than 40 games in a season).

Protocol: chronological walk-forward, the model is refitted every 7 days on
games strictly before the refit day. Test = seasons 2023-24, 2024-25, 2025-26
and 2026-27 so far.

Compared on the same games:
  * winner (incl. overtime): log loss / Brier vs the past home-win rate;
  * total points: P(over line) with line = league average total of the past 30
    days (rounded to .5), Brier vs the past over rate, plus mean absolute error
    of the expected total vs that line;
  * margin: mean absolute error vs home-advantage-only;
  * calibration tables of the winner and over probabilities.

There are no historical odds here: beating a base rate is NOT beating the
bookmakers.

Usage:
  python scripts/backtest_nba.py --fetch     # download missing seasons
  python scripts/backtest_nba.py --out ../docs/backtest-nba.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.engine.basketball.ratings import Game, RatingsModel, rest_flags  # noqa: E402

CACHE = Path("/mnt/project-files/agent-sportif/data/api-basketball")
SEASONS = ["2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026", "2026-2027"]
TEST_FROM = date(2023, 10, 20)
US = ZoneInfo("America/New_York")


def fetch(seasons: list[str]) -> None:
    import httpx
    key = os.environ["API_FOOTBALL_KEY"]
    CACHE.mkdir(parents=True, exist_ok=True)
    for s in seasons:
        path = CACHE / f"games_nba_{s}.json.gz"
        if path.exists() and s != SEASONS[-1]:
            continue
        r = httpx.get("https://v1.basketball.api-sports.io/games", params={"league": 12, "season": s},
                      headers={"x-apisports-key": key}, timeout=60)
        data = r.json()
        if data.get("errors"):
            raise SystemExit(f"api-basketball {s}: {data['errors']}")
        with gzip.open(path, "wt") as f:
            json.dump(data, f)
        print(s, data["results"], "games")


def load() -> list[Game]:
    games = []
    for s in SEASONS:
        path = CACHE / f"games_nba_{s}.json.gz"
        if not path.exists():
            continue
        rows = json.load(gzip.open(path, "rt"))["response"]
        finished = [g for g in rows if g["status"]["short"] in ("FT", "AOT")]
        count = Counter(t for g in finished for t in (g["teams"]["home"]["name"], g["teams"]["away"]["name"]))
        for g in finished:
            h, a = g["teams"]["home"]["name"], g["teams"]["away"]["name"]
            if count[h] < 40 or count[a] < 40:
                continue
            ts = datetime.fromtimestamp(g["timestamp"], timezone.utc).astimezone(US)
            # Pre-season (before the 3rd week of October) is not a real game.
            if ts.month == 10 and ts.day < 18:
                continue
            games.append(Game(ts.date(), h, a, g["scores"]["home"]["total"], g["scores"]["away"]["total"],
                              g["status"]["short"] == "AOT",
                              g["scores"]["home"]["quarter_1"], g["scores"]["away"]["quarter_1"]))
    games.sort(key=lambda g: g.day)
    return games


def _bins(pairs, edges=(0, .2, .35, .5, .65, .8, 1.01)):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [(p, y) for p, y in pairs if lo <= p < hi]
        if sel:
            out.append({"de": lo, "a": min(hi, 1), "n": len(sel), "prevu": round(sum(p for p, _ in sel) / len(sel), 3),
                        "reel": round(sum(y for _, y in sel) / len(sel), 3)})
    return out


def run(games: list[Game]) -> dict:
    flags = rest_flags(games)
    model, next_fit = None, None
    rows = []
    for g, fl in zip(games, flags):
        if g.day < TEST_FROM:
            continue
        if next_fit is None or g.day >= next_fit:
            model = RatingsModel().fit(games, g.day)
            next_fit = g.day + timedelta(days=7)
        past = [x for x in games if g.day - timedelta(days=30) <= x.day < g.day]
        line = round(sum(x.home_pts + x.away_pts for x in past) / len(past) * 2) / 2 if past else 220.5
        line = math.floor(line) + 0.5
        p = model.predict(g.home, g.away, fl)
        rows.append({"g": g, "p": p, "line": line})

    # Baselines from strictly earlier games (expanding).
    out_rows = []
    hw = hn = ov = on = 0
    hca_sum = 0.0
    for r in rows:
        g, p = r["g"], r["p"]
        base_home = hw / hn if hn > 50 else 0.57
        base_over = ov / on if on > 50 else 0.5
        base_hca = hca_sum / hn if hn > 50 else 2.5
        y_home = int(g.home_pts > g.away_pts)
        total = g.home_pts + g.away_pts
        y_over = int(total > r["line"])
        out_rows.append((p.p_home_win(), base_home, y_home, p.p_over(r["line"]), base_over, y_over,
                         abs(p.margin - (g.home_pts - g.away_pts)), abs(base_hca - (g.home_pts - g.away_pts)),
                         abs(p.total - total), abs(r["line"] - total), g.day))
        hw += y_home; hn += 1; ov += y_over; on += 1; hca_sum += g.home_pts - g.away_pts

    def ll(ps, ys):
        return sum(-math.log(max(1e-9, p if y else 1 - p)) for p, y in zip(ps, ys)) / len(ys)

    def br(ps, ys):
        return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)

    def block(sel):
        c = list(zip(*sel))
        return {
            "matchs": len(sel),
            "vainqueur": {"logloss_modele": round(ll(c[0], c[2]), 4), "logloss_reference": round(ll(c[1], c[2]), 4),
                          "brier_modele": round(br(c[0], c[2]), 4), "brier_reference": round(br(c[1], c[2]), 4),
                          "favori_juste": round(sum((p > .5) == bool(y) for p, y in zip(c[0], c[2])) / len(sel), 3)},
            "total_points": {"brier_modele": round(br(c[3], c[5]), 4), "brier_reference": round(br(c[4], c[5]), 4),
                             "ecart_moyen_modele": round(sum(c[8]) / len(sel), 2),
                             "ecart_moyen_reference": round(sum(c[9]) / len(sel), 2)},
            "ecart_points": {"ecart_moyen_modele": round(sum(c[6]) / len(sel), 2),
                             "ecart_moyen_reference": round(sum(c[7]) / len(sel), 2)},
        }

    # First quarter: its coefficients were fitted on 2023-24, so only later seasons are out of sample.
    q1 = [r for r in rows if r["g"].day >= date(2024, 7, 1) and r["g"].q1_home is not None]
    q1_out = None
    if q1:
        cnt = [1, 1, 1]
        ll_m = ll_b = 0.0
        pairs = []
        for r in sorted(q1, key=lambda r: r["g"].day):
            g = r["g"]
            k = 0 if g.q1_home > g.q1_away else 1 if g.q1_home == g.q1_away else 2
            pm = r["p"].q1_probs()
            pb = [c / sum(cnt) for c in cnt]
            ll_m -= math.log(max(1e-9, pm[k])); ll_b -= math.log(pb[k])
            pairs.append((pm[0], int(k == 0)))
            cnt[k] += 1
        q1_out = {"matchs": len(q1), "logloss_modele": round(ll_m / len(q1), 4), "logloss_reference": round(ll_b / len(q1), 4),
                  "calibration_domicile": _bins(pairs)}

    seasons = {}
    for lo, hi, name in [(date(2023, 7, 1), date(2024, 7, 1), "2023-24"), (date(2024, 7, 1), date(2025, 7, 1), "2024-25"),
                         (date(2025, 7, 1), date(2026, 7, 1), "2025-26"), (date(2026, 7, 1), date(2027, 7, 1), "2026-27")]:
        sel = [r for r in out_rows if lo <= r[-1] < hi]
        if sel:
            seasons[name] = block(sel)
    return {
        "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocole": "walk-forward, refit tous les 7 jours sur les matchs passés uniquement",
        "global": block(out_rows),
        "par_saison": seasons,
        "premier_quart_temps_1N2": q1_out,
        "calibration_vainqueur": _bins([(r[0], r[2]) for r in out_rows]),
        "calibration_over": _bins([(r[3], r[5]) for r in out_rows]),
        "sigma_derniere_fit": {"ecart": round(rows[-1]["p"].sigma_margin, 2), "total": round(rows[-1]["p"].sigma_total, 2)},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.fetch:
        fetch(SEASONS)
    res = run(load())
    txt = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)
