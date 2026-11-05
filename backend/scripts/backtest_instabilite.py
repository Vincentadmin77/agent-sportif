"""Team instability backtest (owner 2026-11-05: "une étiquette d'instabilité", Liverpool away as a straight win).

Question: does a team that surprised the market more than its odds imply, earlier in the same season, keep doing so?
If so, a straight win on such a team at 1.50-2.30 should hit less often than its odds imply, and a veto (or a switch
to another market) would help. Data: football-data.co.uk CSVs (results + closing odds, research use only, never shown
on the site), 7 leagues, seasons 2019-20 to 2026-27.

Instability of a team before a match = its "surprise excess" over its previous matches of the same season (at least
MIN_GAMES): observed Brier of its 1X2 results against the de-vigged closing odds, minus the Brier those same odds
expect if they are right (1 - sum p^2). Positive = more surprising than its odds say. Cut points are fixed on the
first four seasons and tested on the later ones (out of sample).

Usage: python scripts/backtest_instabilite.py DIR_WITH_CSVS [--out report.json]
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

MIN_GAMES = 8
ODDS = (1.50, 2.30)
FIT_SEASONS = ("1920", "2021", "2122", "2223")


def _odds(r: dict, side: str) -> float | None:
    for k in (f"AvgC{side}", f"Avg{side}", f"PSC{side}", f"PS{side}", f"B365C{side}", f"B365{side}"):
        try:
            v = float(r.get(k) or "")
            if v > 1:
                return v
        except ValueError:
            pass
    return None


def load(d: Path) -> list[dict]:
    rows = []
    for f in sorted(d.glob("*_*.csv")):
        league, season = f.stem.split("_")
        with f.open(encoding="utf-8-sig", errors="replace") as fh:
            for r in csv.DictReader(fh):
                try:
                    hg, ag = int(r["FTHG"]), int(r["FTAG"])
                    dt = datetime.strptime(r["Date"], "%d/%m/%Y" if len(r["Date"]) > 8 else "%d/%m/%y")
                except (KeyError, ValueError, TypeError):
                    continue
                o = [_odds(r, s) for s in "HDA"]
                if None in o:
                    continue
                inv = np.array([1 / x for x in o])
                ov = None
                for k in ("AvgC>2.5", "Avg>2.5", "PC>2.5", "P>2.5", "B365C>2.5", "B365>2.5"):
                    try:
                        ov = float(r.get(k) or "") if float(r.get(k) or "") > 1 else None
                    except ValueError:
                        continue
                    if ov:
                        break
                rows.append({"over": ov, "league": league, "season": season, "date": dt, "home": r["HomeTeam"], "away": r["AwayTeam"],
                             "hg": hg, "ag": ag, "odds": o, "p": inv / inv.sum(),
                             "res": 0 if hg > ag else 1 if hg == ag else 2})
    rows.sort(key=lambda x: (x["league"], x["season"], x["date"]))
    return rows


def annotate(rows: list[dict]) -> None:
    """Adds each team's season-to-date surprise excess (None before MIN_GAMES) to every match, before it is played."""
    hist: dict[tuple, list[float]] = defaultdict(list)
    form: dict[tuple, list[tuple]] = defaultdict(list)  # (points, expected points, goals against) per past match
    for m in rows:
        for side in ("home", "away"):
            f = form[(m["league"], m["season"], m[side])][-5:]
            m[f"f5_{side}"] = None if len(f) < 5 else {"pts": sum(x[0] for x in f), "xpts": sum(x[1] for x in f),
                                                      "ga": sum(x[2] for x in f), "sd": float(np.std([x[0] - x[1] for x in f]))}
        p, res = m["p"], m["res"]
        form[(m["league"], m["season"], m["home"])].append(((3, 1, 0)[res], 3 * p[0] + p[1], m["ag"]))
        form[(m["league"], m["season"], m["away"])].append(((0, 1, 3)[res], 3 * p[2] + p[1], m["hg"]))
        y = np.eye(3)[m["res"]]
        obs = float(np.sum((m["p"] - y) ** 2))
        exp = float(1 - np.sum(m["p"] ** 2))
        for side in ("home", "away"):
            h = hist[(m["league"], m["season"], m[side])]
            m[f"inst_{side}"] = float(np.mean(h)) if len(h) >= MIN_GAMES else None
        for side in ("home", "away"):
            hist[(m["league"], m["season"], m[side])].append(obs - exp)


def bets(rows: list[dict]) -> list[dict]:
    """Every straight win at closing odds within ODDS, with the backed team's and the opponent's instability."""
    out = []
    for m in rows:
        for k, side, opp in ((0, "home", "away"), (2, "away", "home")):
            c = m["odds"][k]
            if not ODDS[0] <= c <= ODDS[1] or m[f"inst_{side}"] is None:
                continue
            dc_inv = 1 / m["odds"][k] + 1 / m["odds"][1]
            over = sum(1 / x for x in m["odds"])
            out.append({"season": m["season"], "league": m["league"], "side": side, "cote": c, "p": float(m["p"][k]),
                        "win": m["res"] == k, "dc_win": m["res"] in (k, 1), "dc_cote": over / dc_inv,
                        "btts": m["hg"] > 0 and m["ag"] > 0, "goals": m["hg"] + m["ag"], "inst": m[f"inst_{side}"], "inst_opp": m[f"inst_{opp}"],
                        "f5": m[f"f5_{side}"], "over": m.get("over"),
                        "team": m[side], "match": f"{m['home']} - {m['away']}", "date": m["date"].date().isoformat()})
    return out


def summary(L: list[dict]) -> dict:
    if not L:
        return {"n": 0}
    w = np.array([b["win"] for b in L], float)
    c = np.array([b["cote"] for b in L])
    p = np.array([b["p"] for b in L])
    return {"n": len(L), "taux": round(float(w.mean()), 4), "attendu": round(float(p.mean()), 4),
            "ecart": round(float(w.mean() - p.mean()), 4), "rendement": round(float((w * c).mean() - 1), 4),
            "cote_moyenne": round(float(c.mean()), 3), "btts_oui": round(float(np.mean([b["btts"] for b in L])), 4),
            "double_chance": round(float(np.mean([b["dc_win"] for b in L])), 4),
            **_switch(L)}


def _switch(L: list[dict]) -> dict:
    """If we had played over 2.5 goals instead (only where its odds are within ODDS): its own hit rate and return."""
    S = [b for b in L if b["over"] and ODDS[0] <= b["over"] <= ODDS[1]]
    if not S:
        return {}
    w = np.array([b["goals"] > 2 for b in S], float)
    c = np.array([b["over"] for b in S])
    return {"plus_2_5_n": len(S), "plus_2_5_taux": round(float(w.mean()), 4), "plus_2_5_rendement": round(float((w * c).mean() - 1), 4)}


def persistence(rows: list[dict]) -> dict:
    """Correlation between a team's surprise excess in the first and in the second half of a season."""
    per: dict[tuple, list[float]] = defaultdict(list)
    for m in rows:
        y = np.eye(3)[m["res"]]
        e = float(np.sum((m["p"] - y) ** 2) - (1 - np.sum(m["p"] ** 2)))
        for side in ("home", "away"):
            per[(m["league"], m["season"], m[side])].append(e)
    a, b = [], []
    for v in per.values():
        if len(v) >= 20:
            h = len(v) // 2
            a.append(np.mean(v[:h])), b.append(np.mean(v[h:]))
    return {"equipes_saisons": len(a), "correlation": round(float(np.corrcoef(a, b)[0, 1]), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rows = load(a.dir)
    annotate(rows)
    B = bets(rows)
    fit = [b for b in B if b["season"] in FIT_SEASONS]
    test = [b for b in B if b["season"] not in FIT_SEASONS]
    q1, q2 = np.quantile([b["inst"] for b in fit], [1 / 3, 2 / 3])
    hi = float(np.quantile([b["inst"] for b in fit], 0.9))
    band = lambda b: "stable" if b["inst"] < q1 else "moyen" if b["inst"] < q2 else "instable"  # noqa: E731
    rep = {"matchs": len(rows), "positions_victoire_1_50_2_30": len(B), "seuils": {"tiers_1": q1, "tiers_2": q2, "top_10": hi},
           "persistance": persistence(rows), "test": {}, "fit": {}}
    for name, S in (("fit", fit), ("test", test)):
        for k in ("stable", "moyen", "instable"):
            rep[name][k] = summary([b for b in S if band(b) == k])
        rep[name]["top_10_instables"] = summary([b for b in S if b["inst"] >= hi])
        rep[name]["instable_exterieur"] = summary([b for b in S if band(b) == "instable" and b["side"] == "away"])
        rep[name]["stable_exterieur"] = summary([b for b in S if band(b) == "stable" and b["side"] == "away"])
        rep[name]["adversaire_instable"] = summary([b for b in S if b["inst_opp"] is not None and b["inst_opp"] >= q2])
        rep[name]["tous"] = summary(S)
        F = [b for b in S if b["f5"]]
        rep[name]["forme5_tous"] = summary(F)
        rep[name]["forme5_mauvaise_<=5pts"] = summary([b for b in F if b["f5"]["pts"] <= 5])
        rep[name]["forme5_bonne_>=10pts"] = summary([b for b in F if b["f5"]["pts"] >= 10])
        rep[name]["forme5_sous_attentes_-3"] = summary([b for b in F if b["f5"]["pts"] - b["f5"]["xpts"] <= -3])
        rep[name]["forme5_au_dessus_+3"] = summary([b for b in F if b["f5"]["pts"] - b["f5"]["xpts"] >= 3])
        rep[name]["forme5_defense_>=8_encaisses"] = summary([b for b in F if b["f5"]["ga"] >= 8])
        rep[name]["forme5_irreguliere"] = summary([b for b in F if b["f5"]["sd"] >= 1.6])
        rep[name]["forme5_mauvaise_exterieur"] = summary([b for b in F if b["f5"]["pts"] <= 5 and b["side"] == "away"])
    cur = [m for m in rows if m["season"] == max(r["season"] for r in rows)]
    last: dict[str, tuple] = {}
    for m in cur:
        for side in ("home", "away"):
            if m[f"inst_{side}"] is not None:
                last[m[side]] = (m[f"inst_{side}"], m["league"])
    rep["saison_en_cours_plus_instables"] = sorted(([t, round(v, 4), lg] for t, (v, lg) in last.items()), key=lambda x: -x[1])[:15]
    rep["liverpool"] = [b for b in B if b["team"] == "Liverpool" and b["season"] == max(r["season"] for r in rows)]
    print(json.dumps(rep, indent=1, default=str))
    if a.out:
        a.out.write_text(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
