"""NBA starting fives on the basket_matchs pages (display only, owner 2026-11-27): the probable five before tip-off
and the official one once ESPN publishes it. Source: ESPN's public site API (no key, no quota), the same feed the
basketball specialist read for the injuries. No probability and no choice uses it.

compo_probable[côté] = {"titulaires": 5 names (meneur, arrière, ailier, ailier fort, pivot), "absents": [{"joueur",
"raison": blessure|suspendu|incertain|absent, "poste"}], "source": "habituelle"}: the five who started most of the
side's last finished games, minus the players ESPN lists "Out". compositions[côté] has the same shape with the
official five ("source": "officielle")."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta

import httpx

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SLOTS = ("meneur", "arrière", "ailier", "ailier fort", "pivot")
POS_ORDER = {"PG": 0, "G": 1, "SG": 1, "GF": 2, "SF": 2, "F": 3, "FC": 3, "PF": 3, "C": 4}
POS_NAME = {"PG": "meneur", "G": "arrière", "SG": "arrière", "GF": "ailier", "SF": "ailier", "F": "ailier",
            "PF": "ailier fort", "FC": "ailier fort", "C": "pivot"}
RECENT = 5
COUNTS_MIN = 20.0  # an absentee counts when he started 2 of the recent games or plays this many minutes a game

_cache: dict[str, dict] = {}


def _get(path: str, params: dict | None = None) -> dict:
    key = path + json.dumps(params or {}, sort_keys=True)
    if key not in _cache:
        _cache[key] = httpx.get(f"{BASE}/{path}", params=params, timeout=20).json()
    return _cache[key]


def _nick(name: str) -> str:
    return (name or "").split()[-1].lower() if name else ""


def find_event(day_events: list[dict], home: str, away: str) -> dict | None:
    """The ESPN scoreboard event of a game (by the last word of each team name: "Knicks", "76ers", "Blazers")."""
    for e in day_events:
        teams = {c["homeAway"]: c["team"]["displayName"] for c in e["competitions"][0]["competitors"]}
        if _nick(teams.get("home")) == _nick(home) and _nick(teams.get("away")) == _nick(away):
            return e
    return None


def _box_side(summary: dict, team_id: str) -> list[dict]:
    """[{id, nom, pos, starter, min, ast, reb}] of one side in an ESPN game summary ([] before tip-off)."""
    for t in (summary.get("boxscore") or {}).get("players") or []:
        if str(t["team"]["id"]) != str(team_id):
            continue
        st = (t.get("statistics") or [{}])[0]
        labels = st.get("labels") or st.get("names") or []
        out = []
        for a in st.get("athletes") or []:
            vals = dict(zip(labels, a.get("stats") or []))
            num = lambda k: float(vals[k]) if str(vals.get(k, "")).replace(".", "", 1).isdigit() else 0.0
            out.append({"id": str(a["athlete"]["id"]), "nom": a["athlete"].get("displayName"),
                        "pos": (a["athlete"].get("position") or {}).get("abbreviation") or "F",
                        "starter": bool(a.get("starter")), "min": num("MIN"), "ast": num("AST"), "reb": num("REB")})
        return out
    return []


def order_five(players: list[dict]) -> list[dict]:
    """The five in the meneur -> pivot order: guards first (most assists first), then forwards (fewest rebounds
    first), then centres."""
    return sorted(players, key=lambda p: (POS_ORDER.get(p["pos"], 3), -p.get("ast", 0) if POS_ORDER.get(p["pos"], 3) <= 1
                                          else p.get("reb", 0)))


def reason(entry: dict) -> str | None:
    """ESPN injury entry -> blessure / suspendu / incertain; None for "Probable" (expected to play)."""
    text = json.dumps(entry.get("details") or {}).lower() + " " + json.dumps(entry.get("type") or {}).lower()
    status = (entry.get("status") or "").lower()
    if "suspen" in text or "suspen" in status:
        return "suspendu"
    if status == "out":
        return "blessure"
    if status in ("day-to-day", "questionable", "doubtful", "game time decision"):
        return "incertain"
    return None


def recent_games(team_id: str, now: datetime, n: int = RECENT) -> list[dict]:
    """The side's box score in its last n finished games, most recent first."""
    season = now.year + 1 if now.month >= 8 else now.year
    events = _get(f"teams/{team_id}/schedule", {"season": season}).get("events") or []
    done = [e for e in events if e["competitions"][0]["status"]["type"].get("completed")]
    out = []
    for e in sorted(done, key=lambda e: e["date"], reverse=True)[:n]:
        side = _box_side(_get("summary", {"event": e["id"]}), team_id)
        if sum(p["starter"] for p in side) == 5:
            out.append(side)
    return out


def side_probable(games: list[list[dict]], injuries: list[dict]) -> dict | None:
    """compo_probable of one side from recent_games and the side's ESPN injury entries."""
    if not games:
        return None
    starts, mins, info, last = Counter(), Counter(), {}, {}
    for i, g in enumerate(games):
        for p in g:
            starts[p["id"]] += p["starter"]
            mins[p["id"]] += p["min"] / len(games)
            if p["id"] not in info:
                info[p["id"]], last[p["id"]] = p, i
    rank = lambda pid: (-starts[pid], -mins[pid], last[pid])
    usual = {p["id"]: SLOTS[i] for i, p in enumerate(order_five([info[q] for q in sorted(info, key=rank)[:5]]))}
    out_ids, absents = set(), []
    for it in injuries:
        r, a = reason(it), it.get("athlete") or {}
        pid = str(a.get("id"))
        if r in ("blessure", "suspendu"):
            out_ids.add(pid)
        if r is None or not (starts[pid] >= 2 or mins[pid] >= COUNTS_MIN):
            continue
        pos = (a.get("position") or {}).get("abbreviation") or (info.get(pid) or {}).get("pos")
        poste = usual.get(pid) or POS_NAME.get(pos)  # a usual starter: his slot in the usual five
        absents.append({"joueur": a.get("displayName"), "raison": r, **({"poste": poste} if poste else {})})
    free = sorted((pid for pid in info if pid not in out_ids), key=rank)[:5]
    if len(free) < 5:
        return None
    newcomers = [pid for pid in free if pid not in usual]
    group = lambda pid: POS_ORDER.get(info[pid]["pos"], 3)
    for a, it in zip(absents, [it for it in injuries if reason(it) is not None
                               and (starts[str((it.get("athlete") or {}).get("id"))] >= 2
                                    or mins[str((it.get("athlete") or {}).get("id"))] >= COUNTS_MIN)]):
        pid = str((it.get("athlete") or {}).get("id"))
        if pid in usual and pid in out_ids and newcomers:  # the player who takes his place in the five
            rep = min(newcomers, key=lambda q: abs(group(q) - group(pid)))
            newcomers.remove(rep)
            a["remplace_par"] = info[rep]["nom"]
    return {"titulaires": [p["nom"] for p in order_five([info[pid] for pid in free])], "absents": absents,
            "source": "habituelle"}


def official_five(summary: dict, team_id: str) -> dict | None:
    side = _box_side(summary, team_id)
    starters = [p for p in side if p["starter"]]
    if len(starters) != 5:
        return None
    return {"titulaires": [p["nom"] for p in order_five(starters)], "absents": [], "source": "officielle"}


def fill(docs: dict[str, dict], now: datetime, official_only: bool = False) -> list[str]:
    """compo_probable (and compositions when the official fives are out) on the NBA documents of the next 24 h.
    official_only (live loop): only the official fives of the games from 40 min before tip-off to 30 min after.
    Returns the keys of the documents changed. Each game is tried on its own: a failure leaves it as it was."""
    changed = []
    days = {}
    for key, d in docs.items():
        if d.get("code_ligue", d.get("competition")) != "NBA" or not d.get("coup_envoi"):
            continue
        ko = datetime.fromisoformat(d["coup_envoi"])
        if not (now - timedelta(minutes=30) < ko <= now + (timedelta(minutes=40) if official_only else timedelta(hours=24))):
            continue
        if (d.get("compositions") or {}).get("domicile"):
            continue
        try:
            us_day = (ko - timedelta(hours=6)).strftime("%Y%m%d")  # ESPN's scoreboard runs on US Eastern days
            if us_day not in days:
                days[us_day] = _get("scoreboard", {"dates": us_day}).get("events") or []
            ev = find_event(days[us_day], d.get("domicile"), d.get("exterieur"))
            if not ev:
                continue
            ids = {c["homeAway"]: str(c["team"]["id"]) for c in ev["competitions"][0]["competitors"]}
            summary = _get("summary", {"event": ev["id"]}) if ko > now else httpx.get(
                f"{BASE}/summary", params={"event": ev["id"]}, timeout=20).json()
            inj = {str(t["team"]["id"]): t.get("injuries") or [] for t in summary.get("injuries") or []}
            official = {s: official_five(summary, ids[h]) for s, h in (("domicile", "home"), ("exterieur", "away"))}
            if all(official.values()):
                for side, c in official.items():  # the injured who count, as listed before tip-off
                    c["absents"] = ((d.get("compo_probable") or {}).get(side) or {}).get("absents") or []
                d["compositions"] = official
                changed.append(key)
                continue
            if ko <= now or official_only:
                continue
            probable = {s: side_probable(recent_games(ids[h], now), inj.get(ids[h], []))
                        for s, h in (("domicile", "home"), ("exterieur", "away"))}
            if all(probable.values()):
                d["compo_probable"] = probable
                changed.append(key)
        except Exception as e:  # noqa: BLE001 - display only
            print(f"basket compo unavailable for {key} ({str(e)[:120]})", file=sys.stderr)
    return changed
