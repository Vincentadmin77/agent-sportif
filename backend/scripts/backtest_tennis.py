"""Out-of-sample backtest of the tennis models (surface Elo + aces) against the bookmakers, ATP and WTA singles.

Data: api-tennis.com history cached by scripts/fetch_tennis.py in /mnt/project-files/agent-sportif/data/api-tennis/
(results, set scores, aces, pre-match odds of many bookmakers).

Protocol: chronological walk-forward. Each match is predicted with the ratings built on the matches before it, then
the ratings are updated with its result. Players need MIN_MATCHES past matches. Test = matches from TEST_FROM.
Challenger results feed the ratings (players move between the two levels) but are not scored. Walk-overs, retirements and cancelled matches are left out (the markets are settled differently).

Measured on the same matches:
  * winner: log loss of the model, of the bookmakers (median odds without margin) and of the 50/50 blend;
  * total games, games handicap, set score, number of sets, aces 1x2: Brier of the model vs the bookmakers;
  * the house selection rule run on the history: one choice per match among the five axes, best odds of any
    bookmaker between 1.50 and 2.30, confidence = 50 % model + 50 % market, "Le modèle ne le retient pas" when the
    model has no value at the best odds or is too far from the market. Ratio = positions won / positions.

Usage: python scripts/backtest_tennis.py --out ../docs/backtest-tennis.json
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.engine.tennis import elo  # noqa: E402
from sportai.engine.tennis.aces import AcesModel, p_more_aces  # noqa: E402

CACHE = Path("/mnt/project-files/agent-sportif/data/api-tennis")
TEST_FROM = "2024-07-01"
MIN_MATCHES = 10
MIN_ODDS, MAX_ODDS, MAX_GAP, MIN_BOOKS = 1.50, 2.30, 0.12, 2
SLAMS = ("australian open", "french open", "roland garros", "wimbledon", "us open")


@dataclass
class Match:
    key: str
    tour: str
    day: str
    time: str
    a: str
    b: str
    name_a: str
    name_b: str
    tournament: str
    surface: str
    best_of: int
    status: str
    a_won: bool | None
    sets: list = field(default_factory=list)  # [(games a, games b)]
    aces: tuple | None = None
    odds: dict = field(default_factory=dict)
    main: bool = True  # False for Challenger matches (used only to rate the players)

    @property
    def games(self) -> tuple[int, int]:
        return sum(x for x, _ in self.sets), sum(y for _, y in self.sets)


def best_of(tour: str, tournament: str, qualification) -> int:
    t = tournament.lower()
    return 5 if tour == "ATP" and any(s in t for s in SLAMS) and str(qualification) != "True" else 3


def surfaces() -> dict:
    path = CACHE / "tournaments.json.gz"
    if not path.exists():
        return {}
    return {t["tournament_key"]: t.get("tournament_sourface") or "" for t in json.load(gzip.open(path))}


def parse(m: dict, tour: str, surf: dict, odds: dict) -> Match:
    sets = []
    for s in sorted(m.get("scores") or [], key=lambda s: int(s.get("score_set") or 0)):
        try:
            sets.append((int(str(s["score_first"]).split(".")[0]), int(str(s["score_second"]).split(".")[0])))
        except (ValueError, KeyError):
            pass
    aces = {}
    for st in m.get("statistics") or []:
        if st.get("stat_name") == "Aces" and st.get("stat_period") == "match":
            try:
                aces[st["player_key"]] = int(st["stat_value"])
            except (ValueError, TypeError):
                pass
    pa, pb = m["first_player_key"], m["second_player_key"]
    w = m.get("event_winner")
    return Match(
        key=str(m["event_key"]), tour=tour, day=m["event_date"], time=m.get("event_time") or "", a=str(pa), b=str(pb),
        name_a=m["event_first_player"], name_b=m["event_second_player"], tournament=m.get("tournament_name") or "",
        surface=elo.surface_of(surf.get(m.get("tournament_key"))),
        best_of=best_of(tour, m.get("tournament_name") or "", m.get("event_qualification")),
        status=m.get("event_status") or "", a_won=None if w not in ("First Player", "Second Player") else w == "First Player",
        sets=sets, aces=(aces[pa], aces[pb]) if pa in aces and pb in aces else None, odds=odds.get(str(m["event_key"]), {}),
    )


CHALLENGER_OF = {"ATP": "CHM", "WTA": "CHW"}


def load(tours=("ATP", "WTA"), challengers: bool = True) -> list[Match]:
    surf = surfaces()
    out = []
    for tour, prefix in [(t, t) for t in tours] + [(t, CHALLENGER_OF[t]) for t in tours if challengers]:
        for f in sorted(glob.glob(str(CACHE / f"fixtures_{prefix}_*.json.gz"))):
            o = Path(f.replace("fixtures_", "odds_"))
            odds = json.load(gzip.open(o)) if o.exists() else {}
            if not isinstance(odds, dict):
                odds = {}
            for m in json.load(gzip.open(f)):
                if m.get("first_player_key") and m.get("second_player_key"):
                    x = parse(m, tour, surf, odds)
                    x.main = prefix == tour
                    out.append(x)
    uniq = {m.key: m for m in out}
    return sorted(uniq.values(), key=lambda m: (m.day, m.time))


# ---------------------------------------------------------------- odds

def _books(d: dict) -> list[float]:
    out = []
    for v in (d or {}).values():
        try:
            x = float(v)
            if x > 1.0:
                out.append(x)
        except (TypeError, ValueError):
            pass
    return out


def _half(line: str) -> bool:
    try:
        return abs(float(line) * 2 % 2 - 1) < 1e-9
    except ValueError:
        return False


def markets(odds: dict) -> dict:
    """{(market, *selection): [odds per bookmaker]} for the markets we price, half lines only (no refunds)."""
    out = {}
    for side in ("Home", "Away"):
        if _books(odds.get("Home/Away", {}).get(side)):
            out[("vainqueur", side)] = _books(odds["Home/Away"][side])
    for score, books in (odds.get("Set Betting") or {}).items():
        if ":" in score and _books(books):
            out[("score_sets", score)] = _books(books)
    for k, books in (odds.get("1x2 Aces") or {}).items():
        if k in ("Home", "Draw", "Away") and _books(books):
            out[("aces", k)] = _books(books)
    ou = odds.get("Over/Under by Games in Match") or {}
    for side in ("Over", "Under"):
        for line, books in (ou.get(f"Over/Under by Games in Match {side}") or {}).items():
            if _half(line) and _books(books):
                out[("jeux", side, float(line))] = _books(books)
    ah = odds.get("Asian Handicap (Games)") or {}
    for side in ("Home", "Away"):
        for line, books in (ah.get(f"Asian Handicap (Games) {side}") or {}).items():
            if _half(line) and _books(books):
                out[("handicap_jeux", side, float(line))] = _books(books)
    return out


def group(key: tuple, mk: dict) -> list[tuple]:
    """All outcomes of the market of `key` present in mk (to remove the bookmakers' margin)."""
    m = key[0]
    if m == "vainqueur":
        return [("vainqueur", "Home"), ("vainqueur", "Away")]
    if m == "jeux":
        return [("jeux", "Over", key[2]), ("jeux", "Under", key[2])]
    if m == "handicap_jeux":
        other = "Away" if key[1] == "Home" else "Home"
        return sorted([key, ("handicap_jeux", other, -key[2])])
    return sorted(k for k in mk if k[0] == m)


def fair(key: tuple, mk: dict) -> float | None:
    g = group(key, mk)
    if any(k not in mk for k in g) or (key[0] == "aces" and len(g) != 3) or (key[0] == "score_sets" and len(g) < 4):
        return None
    inv = {k: 1 / statistics.median(mk[k]) for k in g}
    return inv[key] / sum(inv.values())


# ---------------------------------------------------------------- model prices

class Pricer:
    def __init__(self, pm: float, m: Match, ea: float | None, eb: float | None):
        self.pm, self.m = pm, m
        self.dist = elo.games_distribution(pm, m.best_of, m.tour)
        self.sets = elo.set_scores(pm, m.best_of)
        self.aces = p_more_aces(ea, eb) if ea is not None else None

    def expected_games(self) -> float:
        return sum((a + b) * p for (a, b), p in self.dist.items())

    def p(self, key: tuple) -> float | None:
        mk = key[0]
        if mk == "vainqueur":
            return self.pm if key[1] == "Home" else 1 - self.pm
        if mk == "score_sets":
            return self.sets.get(key[1].replace(":", "-"))
        if mk == "jeux":
            over = sum(p for (a, b), p in self.dist.items() if a + b > key[2])
            return over if key[1] == "Over" else 1 - over
        if mk == "handicap_jeux":
            if key[1] == "Home":
                return sum(p for (a, b), p in self.dist.items() if a + key[2] > b)
            return sum(p for (a, b), p in self.dist.items() if b + key[2] > a)
        if mk == "aces" and self.aces:
            return self.aces[("Home", "Draw", "Away").index(key[1])]
        return None


def won(key: tuple, m: Match) -> bool | None:
    ga, gb = m.games
    mk = key[0]
    if mk == "vainqueur":
        return m.a_won if key[1] == "Home" else not m.a_won
    if mk == "score_sets":
        sa = sum(x > y for x, y in m.sets)
        return key[1] == f"{sa}:{len(m.sets) - sa}"
    if mk == "jeux":
        return (ga + gb > key[2]) == (key[1] == "Over")
    if mk == "handicap_jeux":
        return ga + key[2] > gb if key[1] == "Home" else gb + key[2] > ga
    if mk == "aces":
        if not m.aces:
            return None
        x, y = m.aces
        return {"Home": x > y, "Draw": x == y, "Away": y > x}[key[1]]
    return None


LABEL = {"vainqueur": "Vainqueur", "score_sets": "Score en sets", "jeux": "Total de jeux",
         "handicap_jeux": "Handicap jeux", "aces": "Plus d'aces"}


def candidates(pr: Pricer, mk: dict) -> list[dict]:
    out = []
    for key, books in mk.items():
        p = pr.p(key)
        f = fair(key, mk)
        if p is None or f is None or len(books) < MIN_BOOKS:
            continue
        best = max(books)
        if not MIN_ODDS <= best <= MAX_ODDS:
            continue
        conf = 0.5 * p + 0.5 * f
        out.append({"key": key, "p": p, "fair": f, "best": best, "median": statistics.median(books), "conf": conf,
                    "retenu": p * best >= 1.0 and abs(p - f) <= MAX_GAP})
    return out


def choose(cands: list[dict]) -> dict | None:
    return max(cands, key=lambda c: c["conf"]) if cands else None


def level(conf: float) -> str:
    return "élevée" if conf >= 0.60 else "moyenne" if conf >= 0.50 else "faible"


# ---------------------------------------------------------------- run

def run(matches: list[Match]) -> dict:
    ratings = {t: elo.SurfaceElo() for t in ("ATP", "WTA")}
    acesm = {t: AcesModel() for t in ("ATP", "WTA")}
    ll = defaultdict(list)
    brier = defaultdict(lambda: defaultdict(list))
    picks = []
    for m in matches:
        ok = m.status == "Finished" and m.a_won is not None and m.sets
        r = ratings[m.tour]
        if ok and m.main and m.day >= TEST_FROM and r.known(m.a, MIN_MATCHES) and r.known(m.b, MIN_MATCHES):
            pm = r.p_win(m.a, m.b, m.surface)
            mk = markets(m.odds)
            ea = eb = None
            pr0 = Pricer(pm, m, None, None)
            if acesm[m.tour].serve[m.a].den > 0 and acesm[m.tour].serve[m.b].den > 0:
                ea, eb = acesm[m.tour].expected(m.a, m.b, m.tour, m.surface, pr0.expected_games())
            pr = Pricer(pm, m, ea, eb)
            fw = fair(("vainqueur", "Home"), mk)
            y = 1.0 if m.a_won else 0.0
            if fw is not None:
                for name, q in (("modele", pm), ("bookmakers", fw), ("melange", 0.5 * pm + 0.5 * fw)):
                    q = min(max(q, 1e-4), 1 - 1e-4)
                    ll[(m.tour, name)].append(-(y * math.log(q) + (1 - y) * math.log(1 - q)))
            for key in mk:
                if key[0] == "vainqueur":
                    continue
                p, f, w = pr.p(key), fair(key, mk), won(key, m)
                if p is None or f is None or w is None:
                    continue
                brier[(m.tour, key[0])]["modele"].append((p - w) ** 2)
                brier[(m.tour, key[0])]["bookmakers"].append((f - w) ** 2)
                brier[(m.tour, key[0])]["melange"].append((0.5 * p + 0.5 * f - w) ** 2)
            c = choose(candidates(pr, mk))
            if c:
                w = won(c["key"], m)
                if w is not None:
                    picks.append({**c, "won": w, "tour": m.tour, "day": m.day, "match": f"{m.name_a} - {m.name_b}",
                                  "surface": m.surface})
        if ok:
            r.update(m.a if m.a_won else m.b, m.b if m.a_won else m.a, m.surface)
            if m.aces:
                acesm[m.tour].update(m.a, m.b, m.tour, m.surface, m.aces[0], m.aces[1], sum(m.games))
    return {"ll": ll, "brier": brier, "picks": picks}


def ratio(ps: list[dict]) -> dict:
    if not ps:
        return {"positions": 0}
    n, w = len(ps), sum(p["won"] for p in ps)
    avg = statistics.mean(p["best"] for p in ps)
    return {"positions": n, "gagnees": w, "ratio": round(w / n, 3), "cote_moyenne": round(avg, 2),
            "point_mort": round(statistics.mean(1 / p["best"] for p in ps), 3)}


def summary(res: dict) -> dict:
    out = {"vainqueur_logloss": {}, "brier": {}, "selection": {}}
    for (tour, name), v in sorted(res["ll"].items()):
        out["vainqueur_logloss"].setdefault(tour, {"matchs": len(v)})[name] = round(statistics.mean(v), 4)
    for (tour, mk), d in sorted(res["brier"].items()):
        out["brier"].setdefault(tour, {})[mk] = {"n": len(d["modele"]), **{k: round(statistics.mean(v), 4) for k, v in d.items()}}
    ps = res["picks"]
    for tour in ("ATP", "WTA", "tous"):
        sub = [p for p in ps if tour == "tous" or p["tour"] == tour]
        ret = [p for p in sub if p["retenu"]]
        s = {"toutes": ratio(sub), "retenues": ratio(ret), "non_retenues": ratio([p for p in sub if not p["retenu"]])}
        s["retenues_par_confiance"] = {lv: ratio([p for p in ret if level(p["conf"]) == lv]) for lv in ("élevée", "moyenne")}
        s["retenues_par_marche"] = {LABEL[m]: ratio([p for p in ret if p["key"][0] == m]) for m in LABEL}
        s["toutes_par_marche"] = {LABEL[m]: ratio([p for p in sub if p["key"][0] == m]) for m in LABEL}
        out["selection"][tour] = s
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--test-from", default=TEST_FROM)
    a = ap.parse_args()
    globals()["TEST_FROM"] = a.test_from
    matches = load()
    res = run(matches)
    s = {"test_depuis": a.test_from, "matchs_charges": len(matches), **summary(res)}
    print(json.dumps(s, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
