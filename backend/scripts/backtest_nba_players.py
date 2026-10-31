"""Out-of-sample backtest of the NBA player-stat model (points, 3-pointers,
rebounds, assists, points+assists, points+rebounds+assists).

Data: per-player box scores cached by scripts/fetch_nba_players.py, seasons
2023-24 to 2026-27. Walk-forward: each game is predicted from earlier games
only, then the model is updated with it. Players are counted when they had 3+
earlier games and played at least 10 minutes in the game (we cannot know
before tip-off who will be benched; at the books a non-starter voids the bet).

Without historical prop lines, the line used is the player's mean over his
last 10 games rounded to .5 (close to how books set them). Compared:
  * P(over line): Brier of the model vs 0.5 and vs the past over rate of the
    same market;
  * mean absolute error of the expected stat vs the last-10 mean;
  * calibration table.
2023-24 is the validation season (dispersion choice), 2024-25 on is the test.

Usage: python scripts/backtest_nba_players.py --out ../docs/backtest-nba-joueurs.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.engine.basketball import players as P  # noqa: E402

CACHE = Path("/mnt/project-files/agent-sportif/data/api-basketball")
SEASONS = ["2023-2024", "2024-2025", "2025-2026", "2026-2027"]
MARKETS = list(P.COMBOS)


def load():
    out = []
    for s in SEASONS:
        games = {g["id"]: g for g in json.load(gzip.open(CACHE / f"games_nba_{s}.json.gz", "rt"))["response"]}
        box = json.load(gzip.open(CACHE / f"players_nba_{s}.json.gz", "rt"))
        for gid, rows in box.items():
            g = games.get(int(gid))
            if not g or not rows:
                continue
            out.append((g["timestamp"], s, rows))
    out.sort(key=lambda x: x[0])
    return out


def line_of(r) -> P.Line:
    return P.Line(P.parse_minutes(r.get("minutes")), r.get("points") or 0, (r.get("threepoint_goals") or {}).get("total") or 0,
                  (r.get("rebounds") or {}).get("total") or 0, r.get("assists") or 0)


def run(games, dispersion=None):
    if dispersion:
        P.DISPERSION.update(dispersion)
    model = P.PlayerModel()
    last10 = defaultdict(lambda: deque(maxlen=10))
    res = defaultdict(list)  # (season, market) -> (p_model, y, abs_err_model, abs_err_naive)
    for ts, season, rows in games:
        for r in rows:
            pid = str(r["player"]["id"])
            ln = line_of(r)
            hist = last10[pid]
            if model.known(pid) and ln.minutes >= 10 and len(hist) >= 3:
                exp = model.expected(pid)
                for m in MARKETS:
                    actual = sum(getattr(ln, s) for s in P.COMBOS[m])
                    naive = sum(sum(getattr(h, s) for s in P.COMBOS[m]) for h in hist) / len(hist)
                    line = math.floor(naive) + 0.5
                    if m == "fg3" and line < 0.5:
                        continue
                    p = model.p_over(exp[m], m, line)
                    res[(season, m)].append((p, int(actual > line), abs(exp[m] - actual), abs(naive - actual)))
            if ln.minutes > 0:
                model.update(pid, ln)
                hist.append(ln)
    return res


def summary(rows):
    n = len(rows)
    over_rate = sum(y for _, y, _, _ in rows) / n
    return {"n": n,
            "brier_modele": round(sum((p - y) ** 2 for p, y, _, _ in rows) / n, 4),
            "brier_50": 0.25,
            "brier_taux_over": round(sum((over_rate - y) ** 2 for _, y, _, _ in rows) / n, 4),
            "ecart_moyen_modele": round(sum(e for _, _, e, _ in rows) / n, 2),
            "ecart_moyen_moyenne10": round(sum(e for _, _, _, e in rows) / n, 2)}


def bins(rows, edges=(0, .3, .4, .5, .6, .7, 1.01)):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [(p, y) for p, y, _, _ in rows if lo <= p < hi]
        if sel:
            out.append({"de": lo, "a": min(hi, 1), "n": len(sel), "prevu": round(sum(p for p, _ in sel) / len(sel), 3),
                        "reel": round(sum(y for _, y in sel) / len(sel), 3)})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--tune", action="store_true")
    a = ap.parse_args()
    games = load()
    if a.tune:
        val = [g for g in games if g[1] == "2023-2024"]
        for k in (4, 8, 12, 20, 40):
            r = run(val, {m: float(k) for m in MARKETS})
            print(k, {m: summary(r[("2023-2024", m)])["brier_modele"] for m in MARKETS})
        raise SystemExit
    res = run(games)
    test = defaultdict(list)
    for (s, m), rows in res.items():
        if s != "2023-2024":
            test[m] += rows
    out = {"genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "dispersion": P.DISPERSION,
           "test_2024_2027": {m: summary(test[m]) for m in MARKETS},
           "par_saison": {f"{s}:{m}": summary(rows) for (s, m), rows in sorted(res.items())},
           "calibration_test": {m: bins(test[m]) for m in MARKETS}}
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    print(json.dumps(out["test_2024_2027"], indent=1))
