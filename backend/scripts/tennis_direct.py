"""Le M in play, tennis, for the record only ("à blanc", never shown to the public, never in the official ratio).

Every LOOP_S seconds during the day's matches: reads the live scores and live odds of api-tennis.com, prices the
markets of the five axes from the current score (elo.live_distribution, same holds as before the match) and lets
Le M take at most ONE position per match, with the in-play rule given by the M thread:
  odds 1.50-2.30, bookmakers' chance without margin >= 0.60, light not red (same thresholds as before the match),
  never before the first game is over, odds not suspended and updated less than 5 minutes ago.
The position goes to tennis/<date>.json, matchs[].m_direct = {position, cle, cote, proba_modele,
proba_bookmakers, feu, score_au_moment, heure, resultat}; the result is filled in once the match is over.

The loop stops (exit 0) as soon as a NEW position was taken or a result was filled in, so the caller can copy the
file to the page's database, then starts it again. It also stops at 02:30 the next night (end of the house day).

Usage: python scripts/tennis_direct.py --date 2026-10-30 --out /mnt/project-files/agent-sportif/tennis/2026-10-30.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_tennis as bt  # noqa: E402
import fetch_tennis  # noqa: E402
import tennis_jour as tj  # noqa: E402
from sportai.engine.tennis import elo  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
LOOP_S = 300
MAX_AGE = timedelta(minutes=5)
SINGLES = ("Atp Singles", "Wta Singles")


def live_markets(raw: dict, now: datetime) -> dict:
    """{key: odds} from get_live_odds, fresh and not suspended, keys as in backtest_tennis.markets()."""
    out = {}
    for o in raw.get("live_odds") or []:
        if o.get("suspended") != "No":
            continue
        try:
            upd = datetime.strptime(o["upd"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=PARIS)
            v = float(o["value"])
        except (KeyError, ValueError, TypeError):
            continue
        if now - upd > MAX_AGE or v <= 1.0:
            continue
        name, side, line = o.get("odd_name"), o.get("type"), o.get("handicap")
        if name == "To Win" and side in ("Home", "Away"):
            out[("vainqueur", side)] = [v]
        elif name == "Set Betting" and side and ":" in side:
            out[("score_sets", side)] = [v]
        elif name == "Total Games in Match" and side in ("Over", "Under") and line and bt._half(line):
            out[("jeux", side, float(line))] = [v]
        elif name == "Match Handicap" and side in ("Home", "Away") and line and bt._half(line):
            out[("handicap_jeux", side, float(line))] = [v]  # live: each side quoted with its own line
    return out


def live_p(key: tuple, dist_g: dict, dist_w: dict) -> float | None:
    mk = key[0]
    if mk == "vainqueur":
        pa = sum(p for (_, _, x, y), p in dist_w.items() if x > y)
        return pa if key[1] == "Home" else 1 - pa
    if mk == "score_sets":
        x, y = (int(t) for t in key[1].split(":"))
        return sum(p for (_, _, a, b), p in dist_w.items() if (a, b) == (x, y))
    if mk == "jeux":
        over = sum(p for (a, b, _, _), p in dist_g.items() if a + b > key[2])
        return over if key[1] == "Over" else 1 - over
    if mk == "handicap_jeux":
        if key[1] == "Home":
            return sum(p for (a, b, _, _), p in dist_g.items() if a + key[2] > b)
        return sum(p for (a, b, _, _), p in dist_g.items() if b + key[2] > a)
    return None


def state(live: dict) -> tuple[list, tuple, bool, int] | None:
    """(finished sets, current set games, A serves next, games played) from a livescore row."""
    sets = []
    for s in sorted(live.get("scores") or [], key=lambda s: int(s.get("score_set") or 0)):
        try:
            sets.append((int(str(s["score_first"]).split(".")[0]), int(str(s["score_second"]).split(".")[0])))
        except (ValueError, KeyError):
            return None
    if not sets:
        return None
    cur = sets[-1]
    done = sets[:-1]
    a, b = cur
    if (a >= 6 or b >= 6) and abs(a - b) >= 2 or a == 7 or b == 7:  # the last set listed is already over
        done, cur = sets, (0, 0)
    # The serve shown is the game being played; the point score of that game is ignored.
    return done, cur, live.get("event_serve") == "First Player", sum(x + y for x, y in sets)


def run_once(a, history_surf) -> bool:
    """One pass. True when the file changed in a way the page must see (new position or new result)."""
    path = Path(a.out)
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    by_id = {m["id"]: m for m in data.get("matchs", [])}
    now = datetime.now(PARIS)
    changed = False
    lives = fetch_tennis.call("get_livescore", timezone="Europe/Paris").get("result") or []
    lives = {str(x["event_key"]): x for x in lives if x.get("event_type_type") in SINGLES}
    odds = fetch_tennis.call("get_live_odds").get("result") or {}
    for key, live in lives.items():
        row = by_id.get(key)
        if not row or row.get("m_direct") or not row.get("joueurs_connus"):
            continue
        st = state(live)
        if not st or st[3] < 1:
            continue  # never before the first game is over
        done, cur, a_serving, played = st
        best_of = row["sets_gagnants"] * 2 - 1
        tour = row["circuit"]
        pm = row["proba_victoire_j1"]
        dist_g = elo.live_distribution(pm, best_of, tour, done, cur, a_serving)
        dist_w = elo.live_distribution(pm, best_of, tour, done, cur, a_serving, sharpen=False)
        mk = live_markets(odds.get(key) or {}, now)
        cands = []
        for k, books in mk.items():
            p, f = live_p(k, dist_g, dist_w), bt.fair(k, mk)
            if p is None or f is None or not bt.MIN_ODDS <= books[0] <= bt.MAX_ODDS:
                continue
            feu = tj.feu_du_m(p, f)
            if f >= 0.60 and feu != "rouge":
                cands.append((f, k, p, books[0], feu))
        if not cands:
            continue
        f, k, p, cote, feu = max(cands)
        m = bt.Match(key=key, tour=tour, day=data["date"], time="", a="", b="", name_a=row["joueur_1"],
                     name_b=row["joueur_2"], tournament="", surface=row["surface"], best_of=best_of, status="", a_won=None)
        score = " ".join(f"{x}-{y}" for x, y in done + [cur])
        row["m_direct"] = {"position": tj.label(k, m), "cle": list(k), "marche": bt.LABEL[k[0]], "cote": cote,
                           "proba_modele": round(p, 3), "proba_bookmakers": round(f, 3), "feu": feu,
                           "score_au_moment": score, "heure": now.strftime("%H:%M")}
        changed = True
        print("M direct:", row["joueur_1"], "-", row["joueur_2"], score, row["m_direct"]["position"], "@", cote, flush=True)
    # Results of the positions taken in play, from the day's fixtures.
    open_ = [m for m in data["matchs"] if m.get("m_direct") and "resultat" not in m["m_direct"]]
    if open_:
        surf = history_surf
        day = datetime.strptime(data["date"], "%Y-%m-%d").date()
        span = {"date_start": str(day), "date_stop": str(day + timedelta(days=1)), "timezone": "Europe/Paris"}
        fixtures = {}
        for tour, tkey in fetch_tennis.TOURS.items():
            for raw in fetch_tennis.call("get_fixtures", event_type_key=tkey, **span).get("result") or []:
                fixtures[str(raw["event_key"])] = (tour, raw)
        for row in open_:
            if row["id"] in fixtures:
                tour, raw = fixtures[row["id"]]
                tj.settle(row["m_direct"], bt.parse(raw, tour, surf, {}))
                if "resultat" in row["m_direct"]:
                    changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    surf = bt.surfaces()
    while True:
        if run_once(a, surf) or a.once:
            return
        end = datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=PARIS) + timedelta(days=1, hours=2, minutes=30)
        if datetime.now(PARIS) > end:
            print("fin de journée", flush=True)
            return
        time.sleep(LOOP_S)


if __name__ == "__main__":
    main()
