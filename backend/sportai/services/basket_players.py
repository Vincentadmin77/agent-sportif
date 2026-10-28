"""NBA player bets: points, 3-pointers, points + assists, points + rebounds + assists.

The model (sportai/engine/basketball/players.py, backtest docs/backtest-nba-joueurs.json) is replayed over the cached
box scores (players_nba_<season>.json.gz, scripts/fetch_nba_players.py) and prices the player lines the odds feed
quotes. Everything here is "non validé": no historical prop lines, no injury feed.

Who plays: only players who played one of their team's last two games are priced (the feed has no injury list).
Names: the odds feed writes "Duncan Robinson", the box scores "Robinson Duncan" or "D. Robinson"; a player matches
when his name tokens are the same, or the last name is the same and the first initials agree, within the two teams.
Feed bet names whose meaning is not sure ("Player Runs", "Player Singles") are left out.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import median

from sportai.engine.basketball import players as P

CACHE = Path(os.getenv("BASKET_CACHE", "/mnt/project-files/agent-sportif/data/api-basketball"))
BASE = "https://v1.basketball.api-sports.io"

FEED_MARKETS = {"player points": "pts", "player triples": "fg3", "player points and assists": "pts_ast",
                "player points, assists and rebounds": "pra",
                # Betano's "Player Runs" lines are rebounds: correlation 0.91 with the model's expected rebounds over
                # 700 lines on 28/10/2026 (0.05 with assists, -0.38 with 3-pointers).
                "player runs": "reb"}
MILESTONES = "player points milestones"  # "Name - 15" = 15 points or more, quoted on one side only
MILESTONE_MARGIN = 1.07  # bookmakers' usual margin, to read a fair chance from a one-sided quote
MARKET_NAMES = {"pts": "Points du joueur", "fg3": "Paniers à 3 points du joueur", "reb": "Rebonds du joueur",
                "pts_ast": "Points + passes du joueur",
                "pra": "Points + rebonds + passes du joueur"}
UNITS = {"pts": "points", "fg3": "paniers à 3 points", "reb": "rebonds", "pts_ast": "points + passes", "pra": "points + rebonds + passes"}
OTHER = {"over": "under", "under": "over"}


# ---------------------------------------------------------------- box scores

def _path(season: str) -> Path:
    return CACHE / f"players_nba_{season}.json.gz"


def refresh(season: str, games: list[dict], api_key: str, budget: int = 200) -> int:
    """Download the box scores of finished games of the season missing from the cache (at most `budget`)."""
    import httpx
    path = _path(season)
    store = json.load(gzip.open(path, "rt")) if path.exists() else {}
    todo = [g["id"] for g in games if g["status"]["short"] in ("FT", "AOT") and str(g["id"]) not in store][:budget]
    if not todo:
        return 0
    client = httpx.Client(base_url=BASE, headers={"x-apisports-key": api_key}, timeout=30)
    got = 0
    for gid in todo:
        try:
            data = client.get("/games/statistics/players", params={"id": gid}).json()
        except Exception:  # noqa: BLE001 - next pass retries
            continue
        if data.get("errors") or not data.get("response"):
            continue
        store[str(gid)] = data["response"]
        got += 1
        time.sleep(0.2)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(store, f)
    tmp.replace(path)
    return got


def _line(r: dict) -> P.Line:
    return P.Line(P.parse_minutes(r.get("minutes")), r.get("points") or 0,
                  (r.get("threepoint_goals") or {}).get("total") or 0,
                  (r.get("rebounds") or {}).get("total") or 0, r.get("assists") or 0)


class Roster:
    """The model replayed over every cached box score, plus who played each team's last two games."""

    def __init__(self, seasons: list[str], games_by_season: dict[str, list[dict]]):
        self.model = P.PlayerModel()
        self.names: dict[str, str] = {}
        self.recent: dict[int, list[set[str]]] = defaultdict(list)  # team id -> players of its games, oldest first
        rows = []
        for s in seasons:
            if not _path(s).exists():
                continue
            ts = {g["id"]: g["timestamp"] for g in games_by_season.get(s, [])}
            for gid, box in json.load(gzip.open(_path(s), "rt")).items():
                if int(gid) in ts:
                    rows.append((ts[int(gid)], box))
        rows.sort(key=lambda x: x[0])
        for _, box in rows:
            teams: dict[int, set[str]] = defaultdict(set)
            for r in box:
                pid = str(r["player"]["id"])
                ln = _line(r)
                self.names[pid] = r["player"]["name"]
                if ln.minutes > 0:
                    self.model.update(pid, ln)
                    teams[r["team"]["id"]].add(pid)
            for t, ps in teams.items():
                self.recent[t] = (self.recent[t] + [ps])[-2:]

    def candidates(self, team_ids: list[int]) -> dict[str, str]:
        out = {}
        for t in team_ids:
            for ps in self.recent.get(t, []):
                for pid in ps:
                    out[pid] = self.names[pid]
        return out


# ---------------------------------------------------------------- names

def _tokens(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    return [t for t in re.split(r"[^a-z]+", s) if t]


def match(feed_name: str, cands: dict[str, str]) -> str | None:
    ft = _tokens(feed_name)
    if not ft:
        return None
    hits = [pid for pid, n in cands.items() if sorted(_tokens(n)) == sorted(ft)]
    if len(hits) == 1:
        return hits[0]
    first, last = ft[0], ft[-1]
    hits = []
    for pid, n in cands.items():
        bt = _tokens(n)
        if last in bt and any(t[0] == first[0] for t in bt if t != last):
            hits.append(pid)
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------- odds

def parse_odds(bookmakers: list[dict]) -> dict[tuple, dict[str, float]]:
    """{(market, player name as quoted, over/under, line): {bookmaker: odds}} for the player markets we price."""
    out: dict[tuple, dict[str, float]] = {}
    for bk in bookmakers or []:
        for bet in bk.get("bets") or []:
            bname = (bet.get("name") or "").strip().lower()
            if bname == MILESTONES:
                for v in bet.get("values") or []:
                    mm = re.match(r"^(.*?)\s+-\s+(\d+)$", str(v.get("value", "")).strip())
                    try:
                        odd = float(v["odd"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if mm and odd > 1.0:
                        out.setdefault(("pts", mm.group(1).strip(), "over", int(mm.group(2)) - 0.5), {})[bk["name"]] = odd
                continue
            m = FEED_MARKETS.get(bname)
            if not m:
                continue
            for v in bet.get("values") or []:
                mm = re.match(r"^(.*?)\s+-\s+(Over|Under)\s+([\d.]+)$", str(v.get("value", "")).strip())
                if not mm:
                    continue
                try:
                    odd, line = float(v["odd"]), float(mm.group(3))
                except (TypeError, ValueError):
                    continue
                if odd <= 1.0 or line % 1 != 0.5:
                    continue
                out.setdefault((m, mm.group(1).strip(), mm.group(2).lower(), line), {})[bk["name"]] = odd
    return out


def price(roster: Roster, team_ids: list[int], quotes: dict[tuple, dict[str, float]]) -> list[dict]:
    """Each quoted player line with the model's chance, the bookmakers' de-vigged chance and the median odds."""
    cands = roster.candidates(team_ids)
    ids: dict[str, str | None] = {}
    rows = []
    for (m, name, side, line), books in quotes.items():
        if name not in ids:
            ids[name] = match(name, cands)
        pid = ids[name]
        if pid is None or not roster.model.known(pid):
            continue
        other = quotes.get((m, name, OTHER[side], line)) or {}
        fair = [(1 / o) / (1 / o + 1 / other[b]) for b, o in books.items() if b in other]
        if not fair:  # one-sided quote (points milestones): the usual margin taken off
            fair = [1 / o / MILESTONE_MARGIN for o in books.values()]
        exp = roster.model.expected(pid)
        p_over = roster.model.p_over(exp[m], m, line)
        p = p_over if side == "over" else 1 - p_over
        med = median(books.values())
        rows.append({"marche_cle": "joueur_" + m, "joueur": name, "joueur_id": pid, "selection": side, "ligne": line,
                     "attendu": round(exp[m], 1), "minutes_attendues": round(exp["minutes"], 1),
                     "cote": round(max(books.values()), 2), "cote_mediane": round(med, 2), "proba": round(p, 4),
                     "proba_marche": round(sum(fair) / len(fair), 4), "valeur": round(p * max(books.values()) - 1, 4),
                     "valeur_mediane": round(p * med - 1, 4), "n_bookmakers": len(books)})
    return sorted(rows, key=lambda r: -r["valeur_mediane"])


def _n(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def label(row: dict) -> str:
    m = row["marche_cle"].removeprefix("joueur_")
    word = "plus" if row["selection"] == "over" else "moins"
    return f"{row['joueur']} : {word} de {_n(row['ligne'])} {UNITS[m]}"


def settle(row: dict, box: list[dict]) -> str | None:
    """gagne / perdu from the game's box score; rembourse when the player did not play; None when not available."""
    for r in box or []:
        if str(r["player"]["id"]) == row["joueur_id"]:
            ln = _line(r)
            if ln.minutes <= 0:
                return "rembourse"
            m = row["marche_cle"].removeprefix("joueur_")
            v = sum(getattr(ln, s) for s in P.COMBOS[m])
            return "gagne" if (v > row["ligne"]) == (row["selection"] == "over") else "perdu"
    return "rembourse" if box else None
