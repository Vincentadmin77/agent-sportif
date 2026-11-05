"""Current-season-only model vs the production model (owner 2026-11-05: "se baser sur la saison, pas sur le passé",
"les 5 derniers matchs en priorité").

Walk-forward on football-data.co.uk CSVs (results + closing odds, research only). Every week, per league:
  base   = production fit: 3 seasons, xi 0.0019 (half-life about one year);
  saison = current season only (from 1 July), xi 0.0019;
  forme  = current season only, xi 0.0116 (half-life 60 days: the last 5-8 matches weigh most).
Only matches where every model knows both teams and each team has played at least MIN_GAMES this season are scored,
so all three are compared on the same matches. Reports the 1X2 Brier of each model and of the market (de-vigged
closing odds), and the straight wins each would retain the production way (confidence = 50/50 model + market >= 50 %,
model at most 8 points above the market, odds 1.50-2.30): hit rate and return at closing odds.

Usage: python scripts/backtest_saison.py DIR_WITH_CSVS [--from 2324] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_instabilite import load  # noqa: E402
from sportai.engine.football import dixon_coles as dc  # noqa: E402
from sportai.engine.football.markets import MarketKey, outcome  # noqa: E402

MIN_GAMES = 5
ODDS = (1.50, 2.30)
MODELS = {"base": (3 * 365, 0.0019, False), "saison": (None, 0.0019, True), "forme": (None, 0.0116, True)}


def season_start(d: date) -> date:
    return date(d.year if d.month >= 7 else d.year - 1, 7, 1)


def fit(train, day, xi):
    try:
        return dc.fit(train, reference_date=day - timedelta(days=1), config=dc.FitConfig(xi=xi), with_uncertainty=False)
    except Exception:  # noqa: BLE001 - a fit that does not converge skips the week for every model
        return None


def run(rows: list[dict], test_from: str) -> dict:
    by_league = defaultdict(list)
    for r in rows:
        by_league[r["league"]].append(r)
    brier = defaultdict(list)
    picks = defaultdict(list)
    for lg, L in by_league.items():
        L.sort(key=lambda r: r["date"])
        mr = [(r, dc.MatchResult(r["date"].date(), r["home"], r["away"], r["hg"], r["ag"])) for r in L]
        days = sorted({r["date"].date() for r in L if r["season"] >= test_from})
        if not days:
            continue
        day = days[0]
        while day <= days[-1]:
            week = [r for r, _ in mr if day <= r["date"].date() < day + timedelta(days=7)]
            if week:
                s0 = season_start(day)
                cur = [m for _, m in mr if s0 <= m.played_on < day]
                n = defaultdict(int)
                for m in cur:
                    n[m.home] += 1
                    n[m.away] += 1
                models = {}
                for k, (win, xi, only) in MODELS.items():
                    tr = cur if only else [m for _, m in mr if m.played_on < day and (day - m.played_on).days <= win]
                    models[k] = fit(tr, day, xi) if len(tr) >= 40 else None
                if all(models.values()):
                    for r in week:
                        if n[r["home"]] < MIN_GAMES or n[r["away"]] < MIN_GAMES:
                            continue
                        if not all(mo.knows(r["home"]) and mo.knows(r["away"]) for mo in models.values()):
                            continue
                        y = np.eye(3)[r["res"]]
                        brier["marche"].append(float(np.sum((r["p"] - y) ** 2)))
                        for k, mo in models.items():
                            sm = mo.score_matrix(r["home"], r["away"])
                            p = np.array([outcome(sm, MarketKey("1x2", s)).win for s in ("home", "draw", "away")])
                            brier[k].append(float(np.sum((p - y) ** 2)))
                            for i in (0, 2):
                                c, mk = r["odds"][i], float(r["p"][i])
                                conf = (p[i] + mk) / 2
                                if ODDS[0] <= c <= ODDS[1] and conf >= 0.5 and p[i] - mk <= 0.08:
                                    picks[k].append((r["res"] == i, c, r["season"], "home" if i == 0 else "away"))
            day += timedelta(days=7)
        print(lg, {k: len(v) for k, v in brier.items()}, file=sys.stderr)
    out = {"matchs_comparés": len(brier["marche"]), "brier": {k: round(float(np.mean(v)), 5) for k, v in brier.items()},
           "victoires_retenues": {}}
    for k, P in picks.items():
        w = np.array([x[0] for x in P], float)
        c = np.array([x[1] for x in P])
        aw = [x for x in P if x[3] == "away"]
        out["victoires_retenues"][k] = {"n": len(P), "taux": round(float(w.mean()), 4), "seuil": round(float((1 / c).mean()), 4),
                                        "rendement": round(float((w * c).mean() - 1), 4),
                                        "exterieur_n": len(aw), "exterieur_taux": round(float(np.mean([x[0] for x in aw])), 4) if aw else None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--from", dest="test_from", default="2324")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rep = run(load(a.dir), a.test_from)
    print(json.dumps(rep, indent=1))
    if a.out:
        a.out.write_text(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
