"""Backtest of the tennis specialist (sportai/services/specialistes.py) against the current tennis choice, on the
api-tennis.com history with the bookmakers' odds (same walk-forward as backtest_tennis.py: ratings updated after
each match, priced before it). Football and basketball are replayed on the page's own documents instead (no odds
history for them here): see `specialistes.py --rejouer`.

    python scripts/backtest_specialistes.py [--out ../docs/backtest-specialistes-tennis.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_tennis as bt  # noqa: E402
from sportai.engine.tennis import elo  # noqa: E402
from sportai.engine.tennis.aces import AcesModel  # noqa: E402
from sportai.services import specialistes as S  # noqa: E402


def layoff(last: dict, m: bt.Match) -> float | None:
    d = [(date.fromisoformat(m.day) - date.fromisoformat(last[p])).days for p in (m.a, m.b) if p in last]
    return max(d) if d else None


def run(matches: list[bt.Match]) -> dict:
    ratings = {t: elo.SurfaceElo() for t in ("ATP", "WTA")}
    acesm = {t: AcesModel() for t in ("ATP", "WTA")}
    last: dict[str, str] = {}
    cur, spec = [], []
    for m in matches:
        ok = m.status == "Finished" and m.a_won is not None and m.sets
        r = ratings[m.tour]
        if ok and m.main and m.day >= bt.TEST_FROM and r.known(m.a, bt.MIN_MATCHES) and r.known(m.b, bt.MIN_MATCHES):
            pm = r.p_win(m.a, m.b, m.surface)
            pr = bt.Pricer(pm, m, None, None)
            if acesm[m.tour].serve[m.a].den > 0 and acesm[m.tour].serve[m.b].den > 0:
                ea, eb = acesm[m.tour].expected(m.a, m.b, m.tour, m.surface, pr.expected_games())
                pr = bt.Pricer(pm, m, ea, eb)
            cands = bt.candidates(pr, bt.markets(m.odds))
            c = bt.choose(cands)
            if c and c["retenu"]:
                w = bt.won(c["key"], m)
                if w is not None:
                    cur.append({**c, "won": w, "tour": m.tour})
            s = S.tennis_choice(cands, layoff(last, m), bt.markets(m.odds))
            if s:
                w = bt.won(s["key"], m)
                if w is not None:
                    spec.append({**s, "won": w, "tour": m.tour})
        if ok:
            r.update(m.a if m.a_won else m.b, m.b if m.a_won else m.a, m.surface)
            if m.aces:
                acesm[m.tour].update(m.a, m.b, m.tour, m.surface, m.aces[0], m.aces[1], sum(m.games))
        if m.status == "Finished":
            last[m.a] = last[m.b] = m.day
    out = {}
    for name, ps in (("choix_actuel_retenus", cur), ("specialiste", spec)):
        out[name] = {"tous": bt.ratio(ps), **{t: bt.ratio([p for p in ps if p["tour"] == t]) for t in ("ATP", "WTA")},
                     "par_marche": {bt.LABEL[k]: bt.ratio([p for p in ps if p["key"][0] == k]) for k in bt.LABEL
                                    if any(p["key"][0] == k for p in ps)}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    matches = sorted(bt.load(), key=lambda m: (m.day, m.time))
    res = {"periode": f"{bt.TEST_FROM} -> aujourd'hui", "regles": {
        "marches": list(S.TENNIS_MARKETS), "poids_marche": S.TENNIS_MARKET_WEIGHT,
        "valeur_min": S.TENNIS_MIN_VALUE, "retour_apres_jours": S.TENNIS_LAYOFF_DAYS}, **run(matches)}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    Path(a.out).write_text(text) if a.out else print(text)


if __name__ == "__main__":
    main()
