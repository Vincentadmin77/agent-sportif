"""Probable line-up of each side before the official one is out (display only, owner 2026-11-27).

API-Football publishes no predicted line-up, so it is always the usual one ("habituelle"): the formation played most
in the side's last finished matches (all competitions), and for each line (goalkeeper, defence, midfield, attack)
the players who started most often there, minus the injured and suspended of /injuries. A player listed "incertain"
stays in the XI. The page shows it until "compositions" arrives, then switches to the official line-up. No
probability and no choice uses it."""

from __future__ import annotations

from collections import Counter

POSTE = {"G": "gardien", "D": "défense", "M": "milieu", "F": "attaque"}
LINES = ("G", "D", "M", "F")
RECENT = 5  # last finished matches looked at
OUT_REASONS = {"blessure", "suspendu", "malade", "absent", "raisons personnelles", "en sélection"}
FINISHED = {"FT", "AET", "PEN"}


def _grid(g: str | None) -> tuple[int, int]:
    try:
        r, c = (g or "").split(":")
        return int(r), int(c)
    except ValueError:
        return 99, 99


def recent_lineups(fixtures: list[dict], team_id: int, n: int = RECENT) -> list[dict]:
    """The side's line-up in each of its last n finished fixtures (API-Football /fixtures?ids=, lineups included),
    most recent first."""
    done = [f for f in fixtures if (f.get("fixture") or {}).get("status", {}).get("short") in FINISHED]
    done.sort(key=lambda f: -(f["fixture"].get("timestamp") or 0))
    out = []
    for f in done:
        lu = next((x for x in f.get("lineups") or [] if (x.get("team") or {}).get("id") == team_id), None)
        if lu and len(lu.get("startXI") or []) == 11:
            out.append(lu)
        if len(out) == n:
            break
    return out


CODE = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}


def squad_positions(players: list[dict]) -> dict:
    """/players?team=&season= -> {player id: "G" | "D" | "M" | "F"}."""
    out = {}
    for item in players or []:
        pid = (item.get("player") or {}).get("id")
        for st in item.get("statistics") or []:
            c = CODE.get((st.get("games") or {}).get("position"))
            if pid is not None and c:
                out.setdefault(pid, c)
    return out


def side_probable(lineups: list[dict], absents: list[dict], is_regular=lambda pid, name: False,
                  positions: dict | None = None) -> dict | None:
    """{"formation", "titulaires" (11 names, goalkeeper -> defence -> midfield -> attack), "absents", "source"}.

    lineups: recent_lineups (most recent first). absents: the side's /injuries entries as lineups.absentees writes
    them ({"joueur", "raison", "id"?}). Only absentees who count are listed: a regular starter (is_regular) or a
    player who started at least 2 of the recent matches. None when fewer than 11 players can be named."""
    if not lineups:
        return None
    forms = Counter(lu.get("formation") for lu in lineups if lu.get("formation"))
    formation = max(forms, key=lambda f: (forms[f], -next(i for i, lu in enumerate(lineups)
                                                           if lu.get("formation") == f))) if forms else None
    template = next((lu for lu in lineups if lu.get("formation") == formation), lineups[0])
    need = Counter((p["player"].get("pos") or "M") for p in template["startXI"])
    starts, last_seen, pos, grid, names = Counter(), {}, {}, {}, {}
    for i, lu in enumerate(lineups):
        for p in lu["startXI"]:
            pl = p["player"]
            pid = pl.get("id") or pl.get("name")
            starts[pid] += 1
            if pid not in last_seen:
                last_seen[pid], pos[pid], grid[pid], names[pid] = i, pl.get("pos") or "M", pl.get("grid"), pl.get("name")
    for lu in lineups:  # substitutes: positions of players with no recent start, used only to fill a line
        for p in lu.get("substitutes") or []:
            pl = p["player"]
            pid = pl.get("id") or pl.get("name")
            if pid not in pos:
                pos[pid], names[pid], last_seen[pid] = pl.get("pos") or "M", pl.get("name"), len(lineups)
    out_ids = {a.get("id") for a in absents if a.get("raison") in OUT_REASONS} - {None}
    out_names = {a.get("joueur") for a in absents if a.get("raison") in OUT_REASONS}
    free = [pid for pid in pos if pid not in out_ids and names.get(pid) not in out_names]
    rank = lambda pid: (-starts[pid], last_seen[pid])
    xi: list = []
    for line in LINES:
        xi += sorted((p for p in free if pos[p] == line), key=rank)[:need.get(line, 0)]
    xi += sorted((p for p in free if p not in xi and pos[p] != "G"), key=rank)[:11 - len(xi)]
    if len(xi) < 11:
        return None
    xi.sort(key=lambda p: (LINES.index(pos[p]) if pos[p] in LINES else 2, _grid(grid.get(p))))
    listed = []
    for a in absents:
        pid = a.get("id")
        if not (is_regular(pid, a.get("joueur")) or starts.get(pid, 0) >= 2):
            continue
        poste = POSTE.get(pos.get(pid)) or POSTE.get((positions or {}).get(pid) or "")
        entry = {"joueur": a.get("joueur"), "raison": a.get("raison") if a.get("raison") in
                 ("blessure", "suspendu", "incertain") else "absent"}
        if poste:
            entry["poste"] = poste
        listed.append(entry)
    return {"formation": formation, "titulaires": [names[p] for p in xi], "absents": listed, "source": "habituelle"}
