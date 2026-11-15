"""NBA injuries for V2 (the basketball specialist, à blanc): who is out or doubtful tonight, and what it costs.

Source: ESPN's public injuries feed (site.api.espn.com), for the internal test only: a licensed feed (BallDontLie or
SportsDataIO, see recherche/complement-basket.md) is needed before anything is sold. Each snapshot is kept with its
time (the research's rule: never judge with an injury status learnt after the decision).

Points lost when a player is out (research dossier, "Points perdus"): about 4-6 points for a top-5 player, 1-2 for a
solid starter. Without an impact rating (EPM, BPM) we scale on the player's scoring and minutes over his last 20
games: delta = IMPACT_PER_POINT x points per game x minutes / 34, at most MAX_DELTA, for players of MIN_MINUTES or
more. A player who has missed his team's last games is already out of the ratings (no adjustment). The team total
drops by TOTAL_SHARE of that (the bench scores part of it back).
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
DATA = Path("/mnt/project-files/agent-sportif/data/api-basketball")
SNAPSHOTS = Path("/mnt/project-files/agent-sportif/data/blessures-nba")
IMPACT_PER_POINT = 0.18
MAX_DELTA = 6.0
MIN_MINUTES = 20.0
TOTAL_SHARE = 0.6
KEY_POINTS = 15.0  # a doubtful player scoring this much: V2 passes on the game (research: no position while a key
#                    player is uncertain)
RECENT_GAMES = 3  # out of the ratings when he played none of his team's last 3 games
FORM_GAMES = 20
DOUBTFUL = ("day-to-day", "questionable", "doubtful", "game time decision")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub("[^a-z ]", "", s).strip()


def fetch(now: datetime) -> dict:
    import httpx

    data = httpx.get(ESPN_URL, timeout=30).json()
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    (SNAPSHOTS / f"{now.strftime('%Y-%m-%dT%H%M')}.json").write_text(json.dumps(data, ensure_ascii=False))
    return data


def box_scores(seasons=("2025-2026", "2026-2027")) -> dict:
    """team -> list of (date, {surname: (points, minutes)}) for every finished game, oldest first."""
    out = defaultdict(list)
    for season in seasons:
        gp, pp = DATA / f"games_nba_{season}.json.gz", DATA / f"players_nba_{season}.json.gz"
        if not gp.exists() or not pp.exists():
            continue
        games = {g["id"]: g for g in json.load(gzip.open(gp)).get("response", [])
                 if (g.get("status") or {}).get("short") in ("FT", "AOT")}
        players = json.load(gzip.open(pp))
        for gid, rows in players.items():
            g = games.get(int(gid))
            if not g:
                continue
            names = {g["teams"]["home"]["id"]: g["teams"]["home"]["name"], g["teams"]["away"]["id"]: g["teams"]["away"]["name"]}
            per = defaultdict(dict)
            for r in rows:
                team = names.get((r.get("team") or {}).get("id"))
                mins = str(r.get("minutes") or "0:0").split(":")
                try:
                    m = int(mins[0]) + int(mins[1] if len(mins) > 1 else 0) / 60
                except ValueError:
                    m = 0.0
                if team and m > 0:
                    surname = _norm((r.get("player") or {}).get("name", "")).split(" ")[0]
                    per[team][surname] = (r.get("points") or 0, m)
            for team, ps in per.items():
                out[_norm(team)].append((g["date"], ps))
    for v in out.values():
        v.sort(key=lambda x: x[0])
    return out


def team_report(team: str, injuries: dict, boxes: dict) -> dict:
    """{"marge": points lost by the team, "total": its total change, "absents": [...], "incertains": [...]}."""
    t = _norm(team)
    games = boxes.get(t) or []
    rep = {"marge": 0.0, "total": 0.0, "absents": [], "incertains": []}
    for block in injuries.get("injuries") or []:
        if _norm(block.get("displayName")) != t:
            continue
        for inj in block.get("injuries") or []:
            ath = inj.get("athlete") or {}
            surname = _norm(ath.get("lastName") or ath.get("displayName", "")).split(" ")[-1]
            status = (inj.get("status") or "").lower()
            seen = [ps[surname] for _, ps in games[-FORM_GAMES:] if surname in ps]
            if not seen:
                continue
            ppg, mpg = sum(p for p, _ in seen) / len(seen), sum(m for _, m in seen) / len(seen)
            recent = any(surname in ps for _, ps in games[-RECENT_GAMES:])
            who = f"{ath.get('displayName')} ({ppg:.0f} pts, {mpg:.0f} min)"
            if status == "out":
                if mpg >= MIN_MINUTES and recent:
                    d = min(MAX_DELTA, IMPACT_PER_POINT * ppg * mpg / 34)
                    rep["marge"] += d
                    rep["total"] += TOTAL_SHARE * d
                    rep["absents"].append(who)
            elif any(s in status for s in DOUBTFUL) and ppg >= KEY_POINTS and recent:
                rep["incertains"].append(who)
    rep["marge"], rep["total"] = round(rep["marge"], 1), round(rep["total"], 1)
    return rep


def game_adjustment(home: str, away: str, injuries: dict, boxes: dict) -> dict:
    h, a = team_report(home, injuries, boxes), team_report(away, injuries, boxes)
    return {"marge": round(a["marge"] - h["marge"], 1), "total": round(-(h["total"] + a["total"]), 1),
            "domicile": h, "exterieur": a, "maj": datetime.now(timezone.utc).isoformat(timespec="minutes")}
