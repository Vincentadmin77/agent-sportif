"""Rare events of a match: a red card, a penalty scored or missed (owner 2026-10-28: "l'arbitre a un rôle dans chaque
match, il doit toujours être pris en compte"). Bookmaker bets: "RCARD" (a red card in the match), "Red Card In The
Match (1st Half)", "To Score A Penalty" / "To Miss A Penalty" (Home / Away).

Each event is a Poisson count. Without the referee: each team's own rate (reds received, penalties scored or missed;
home matches for the home side, away for the away side) shrunk to the league rate by SHRINK matches, the opponent's
rate "against" the same way, multiplied as in the count model. With the referee: times the referee's rate of the
event per match in the league before that day, shrunk to the league rate by REF_SHRINK matches. Reference: the
league frequency over the 3 previous years. Same matches, test seasons and rule as backtest_counts.py.

    python3 backtest_rare.py [--ref-shrink 20]

Writes docs/backtest-evenements-rares.json.
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
    FINISHED, LEAGUES, SEASONS, TEST_SEASONS, Budget, Fetcher, boot_ci, brier, ll,
)
from sportai.services.contexte import referee_keys  # noqa: E402

SHRINK = 20.0
MARGIN = 0.002
# event -> (count of the home side, of the away side) from a match row; "rouge_mt" is the first half only
EVENTS = ("rouge", "rouge_mt", "pen_marque", "pen_rate")
MARKETS = {"rouge:match": ("rouge", "match"), "rouge_mt:match": ("rouge_mt", "match"),
           "pen_marque:home": ("pen_marque", "home"), "pen_marque:away": ("pen_marque", "away"),
           "pen_rate:home": ("pen_rate", "home"), "pen_rate:away": ("pen_rate", "away")}


def load(comp: str, fetcher: Fetcher) -> list[dict]:
    out = []
    for season in SEASONS[comp]:
        try:
            lst = fetcher.get({"league": LEAGUES[comp], "season": season})
        except Budget:
            continue
        ok = {f["fixture"]["id"] for f in lst.get("response", [])
              if f["fixture"]["status"]["short"] in FINISHED and f["score"]["fulltime"]["home"] is not None
              and str(f["league"].get("round", "")).startswith("Regular Season")}
        ids = sorted(ok)
        for i in range(0, len(ids), 20):
            try:
                data = fetcher.get({"ids": "-".join(map(str, ids[i:i + 20]))})
            except Budget:
                continue
            for f in data.get("response", []):
                if f["fixture"]["id"] not in ok or not f.get("events") or not f.get("statistics"):
                    continue
                hid = f["teams"]["home"]["id"]
                c = {e: [0, 0] for e in EVENTS}
                for e in f["events"]:
                    side = 0 if (e.get("team") or {}).get("id") == hid else 1
                    minute = (e.get("time") or {}).get("elapsed") or 0
                    if e.get("type") == "Card" and e.get("detail") in ("Red Card", "Second Yellow card"):
                        c["rouge"][side] += 1
                        c["rouge_mt"][side] += minute <= 45
                    elif e.get("type") == "Goal" and e.get("detail") == "Penalty":
                        c["pen_marque"][side] += 1
                    elif e.get("type") == "Goal" and e.get("detail") == "Missed Penalty":
                        c["pen_rate"][side] += 1
                out.append({"fid": f["fixture"]["id"], "season": season,
                            "kickoff": datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")),
                            "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "referee": f["fixture"].get("referee"), **c})
    out.sort(key=lambda m: (m["kickoff"], m["fid"]))
    return out


def outcome(m: dict, ev: str, who: str) -> float:
    h, a = m[ev]
    return float((h + a if who == "match" else h if who == "home" else a) > 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-shrink", type=float, default=20.0)
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    a = ap.parse_args()
    fetcher = Fetcher(allow_network=False)
    rows = []
    for comp in a.leagues:
        ms = load(comp, fetcher)
        days = np.array([m["kickoff"].date().toordinal() for m in ms])
        cum = {}  # running sums for the league rates over the 3 previous years
        for name, (ev, who) in MARKETS.items():
            arr = np.array([[m[ev][0], m[ev][1], outcome(m, ev, who)] for m in ms], dtype=float)
            cum[name] = np.vstack([np.zeros(3), np.cumsum(arr, axis=0)])
        # running totals: per team and venue [for, against, matches]; per referee [count, matches]; per day for the league
        team = {e: defaultdict(lambda: [0.0, 0.0, 0]) for e in EVENTS}
        ref = {e: defaultdict(lambda: [0.0, 0]) for e in EVENTS}
        for i, m in enumerate(ms):
            d = days[i]
            lo, hi = np.searchsorted(days, d - 3 * 365), np.searchsorted(days, d)
            keys = referee_keys(m.get("referee"))
            rk = keys[1] if keys else None
            if m["season"] in TEST_SEASONS and hi - lo >= 200:
                row = {"comp": comp, "p_ref": [], "p0": [], "p1": [], "y": []}
                for name, (ev, who) in MARKETS.items():
                    lh, la, freq = (cum[name][hi] - cum[name][lo]) / (hi - lo)

                    def rate(t, venue, league_for, league_against):
                        f, ag, n = team[ev][(t, venue)]
                        return (f + SHRINK * league_for) / (n + SHRINK), (ag + SHRINK * league_against) / (n + SHRINK)

                    hf, ha_ = rate(m["home"], "home", lh, la)  # home side's own count, and the away teams' count there
                    af, aa_ = rate(m["away"], "away", la, lh)
                    mh = hf * aa_ / lh if lh else hf  # what the home side gets, times what the away side concedes
                    ma = af * ha_ / la if la else af
                    s, n = ref[ev][rk] if rk else (0.0, 0)
                    league = lh + la
                    f = (s + a.ref_shrink * league) / (n + a.ref_shrink) / league if league else 1.0
                    lam0 = {"match": mh + ma, "home": mh, "away": ma}[who]
                    row["p_ref"].append(freq)
                    row["p0"].append(1 - np.exp(-lam0))
                    row["p1"].append(1 - np.exp(-lam0 * f))
                    row["y"].append(outcome(m, ev, who))
                rows.append(row)
            for ev in EVENTS:
                h, aw = m[ev]
                team[ev][(m["home"], "home")][0] += h
                team[ev][(m["home"], "home")][1] += aw
                team[ev][(m["home"], "home")][2] += 1
                team[ev][(m["away"], "away")][0] += aw
                team[ev][(m["away"], "away")][1] += h
                team[ev][(m["away"], "away")][2] += 1
                if rk:
                    ref[ev][rk][0] += h + aw
                    ref[ev][rk][1] += 1
        print(f"{comp}: {len(ms)} matches with events, {sum(r['comp'] == comp for r in rows)} tested", flush=True)
    R, P0, P1, Y = (np.array([r[k] for r in rows]) for k in ("p_ref", "p0", "p1", "y"))
    comps = np.array([r["comp"] for r in rows])
    res = {"generated": datetime.now(timezone.utc).isoformat(), "n_test": len(rows), "ref_shrink": a.ref_shrink,
           "marches": {}}
    for k, name in enumerate(MARKETS):
        y = Y[:, k]
        out = {"observe": round(float(y.mean()), 4)}
        for label, p, q in (("equipes_vs_ligue", P0[:, k], R[:, k]), ("arbitre_vs_equipes", P1[:, k], P0[:, k]),
                            ("arbitre_vs_ligue", P1[:, k], R[:, k])):
            db, dl = brier(p, y) - brier(q, y), ll(p, y) - ll(q, y)
            ci = boot_ci(db)
            per = {c: round(float(db[comps == c].mean()), 5) for c in a.leagues if (comps == c).any()}
            ok = bool(db.mean() < 0 and dl.mean() < 0 and ci[1] < 0 and all(v <= MARGIN for v in per.values()))
            out[label] = {"ecart_brier": round(float(db.mean()), 6), "ic95": [round(x, 6) for x in ci],
                          "ecart_logloss": round(float(dl.mean()), 6), "par_championnat": per, "mieux": ok}
            print(f"{name:16s} {label:20s} obs {y.mean():.3f} pred {p.mean():.3f} dBrier {db.mean():+.5f} "
                  f"[{ci[0]:+.5f};{ci[1]:+.5f}] dLL {dl.mean():+.5f} {'MIEUX' if ok else ''}")
        res["marches"][name] = out
    path = Path(__file__).resolve().parents[2] / "docs" / "backtest-evenements-rares.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
