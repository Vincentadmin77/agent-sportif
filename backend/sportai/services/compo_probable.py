"""Probable line-up of each side before the official one is out (display only, owner 2026-11-27).

API-Football publishes no predicted line-up, so it is always the usual one ("habituelle"): the formation played most
in the side's last finished matches (all competitions), and for each line (goalkeeper, defence, midfield, attack)
the players who started most often there, minus the injured and suspended of /injuries. A player listed "incertain"
stays in the XI. The page shows it until "compositions" arrives, then switches to the official line-up. No
probability and no choice uses it."""

from __future__ import annotations

import unicodedata
from collections import Counter

POSTE = {"G": "gardien", "D": "défense", "M": "milieu", "F": "attaque"}
LINES = ("G", "D", "M", "F")
RECENT = 8  # last finished matches looked at
REGULAR_BONUS = 3  # a season regular counts as this many extra recent starts (cup rotation does not make him a sub)
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


GAP_STRONG = 0.4  # season rating gap from which the replacement is "clearly weaker" (gravite "forte")


def season_ratings(players: list[dict], team_id: int | None = None) -> dict:
    """/players?team=&season= (this season) -> {player id: average rating over his appearances, all competitions}."""
    acc: dict = {}
    for item in players or []:
        pid = (item.get("player") or {}).get("id")
        for st in item.get("statistics") or []:
            if team_id is not None and (st.get("team") or {}).get("id") not in (None, team_id):
                continue
            g = st.get("games") or {}
            try:
                r, n = float(g.get("rating")), g.get("appearences") or 0
            except (TypeError, ValueError):
                continue
            if pid is not None and n:
                a, w = acc.get(pid, (0.0, 0))
                acc[pid] = (a + r * n, w + n)
    return {pid: round(a / w, 2) for pid, (a, w) in acc.items() if w}


def gravity(note: float | None, note_rep: float | None) -> str:
    """"forte" when the replacement is clearly weaker (or has no rating: barely plays), else "moyenne"."""
    if note is None:
        return "moyenne"
    if note_rep is None or note - note_rep >= GAP_STRONG:
        return "forte"
    return "moyenne"


def _profile(lineups: list[dict], regulars: set | None = None) -> dict:
    """What the recent line-ups say: the usual formation and how many players it puts in each line, and for each
    player his starts, last line and grid cell."""
    forms = Counter(lu.get("formation") for lu in lineups if lu.get("formation"))
    formation = max(forms, key=lambda f: (forms[f], -next(i for i, lu in enumerate(lineups)
                                                           if lu.get("formation") == f))) if forms else None
    template = next((lu for lu in lineups if lu.get("formation") == formation), lineups[0])
    pr = {"formation": formation, "need": Counter((p["player"].get("pos") or "M") for p in template["startXI"]),
          "starts": Counter(), "last": {}, "pos": {}, "grid": {}, "names": {}, "regulars": set(regulars or ())}
    for i, lu in enumerate(lineups):
        for p in lu["startXI"]:
            pl = p["player"]
            pid = pl.get("id") or pl.get("name")
            pr["starts"][pid] += 1
            if pid not in pr["last"]:
                pr["last"][pid], pr["pos"][pid], pr["grid"][pid], pr["names"][pid] = (
                    i, pl.get("pos") or "M", pl.get("grid"), pl.get("name"))
    for lu in lineups:  # substitutes: positions of players with no recent start, used only to fill a line
        for p in lu.get("substitutes") or []:
            pl = p["player"]
            pid = pl.get("id") or pl.get("name")
            if pid not in pr["pos"]:
                pr["pos"][pid], pr["names"][pid], pr["last"][pid] = pl.get("pos") or "M", pl.get("name"), len(lineups)
    return pr


_FOLD = str.maketrans({"ø": "o", "Ø": "O", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "æ": "ae", "Æ": "AE", "ı": "i"})


def _norm(name) -> str:
    """Name key without accents or case: /injuries writes « D. Doue » where /players and the line-ups write « D. Doué »."""
    name = str(name or "").translate(_FOLD)
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()


def _pick_xi(pr: dict, out_ids: set, out_names: set) -> list:
    pos = pr["pos"]
    rank = lambda pid: (-(pr["starts"][pid] + REGULAR_BONUS * (pid in pr["regulars"])), pr["last"][pid])
    out_names = {_norm(n) for n in out_names}
    free = [pid for pid in pos if pid not in out_ids and _norm(pr["names"].get(pid)) not in out_names]
    xi: list = []
    for line in LINES:
        xi += sorted((p for p in free if pos[p] == line), key=rank)[:pr["need"].get(line, 0)]
    xi += sorted((p for p in free if p not in xi and pos[p] != "G"), key=rank)[:11 - len(xi)]
    xi.sort(key=lambda p: (LINES.index(pos[p]) if pos[p] in LINES else 2, _grid(pr["grid"].get(p))))
    return xi


def replacements(missing: list, usual: list, xi: list, pos: dict, grid: dict) -> dict:
    """{missing player: the player who takes his place in xi}: a newcomer (in xi, not in the usual XI) of the same
    line, the closest grid column first, each newcomer used once; then any newcomer left."""
    newcomers = [p for p in xi if p not in usual]
    out = {}
    for m in missing:
        if m not in usual or not newcomers:
            continue
        col = _grid(grid.get(m))[1]
        same = [p for p in newcomers if pos.get(p) == pos.get(m)] or newcomers
        best = min(same, key=lambda p: abs(_grid(grid.get(p))[1] - col))
        out[m] = best
        newcomers.remove(best)
    return out


def _entry(name, raison, pid, rep, pr, ratings, positions) -> dict:
    poste = POSTE.get(pr["pos"].get(pid)) or POSTE.get((positions or {}).get(pid) or "")
    e = {"joueur": name, "raison": raison}
    if poste:
        e["poste"] = poste
    if rep is not None:
        note, note_rep = (ratings or {}).get(pid), (ratings or {}).get(rep)
        e.update(remplace_par=pr["names"][rep], note=note, note_remplacant=note_rep, gravite=gravity(note, note_rep))
    return e


def side_probable(lineups: list[dict], absents: list[dict], is_regular=lambda pid, name: False,
                  positions: dict | None = None, ratings: dict | None = None,
                  regulars: set | None = None) -> dict | None:
    """{"formation", "titulaires" (11 names, goalkeeper -> defence -> midfield -> attack), "absents", "source"}.

    lineups: recent_lineups (most recent first). absents: the side's /injuries entries as lineups.absentees writes
    them ({"joueur", "raison", "id"?}). Only absentees who count are listed: a regular starter (is_regular) or a
    player who started at least 2 of the recent matches; each with the player taking his place (remplace_par),
    both season ratings and the gravity. None when fewer than 11 players can be named."""
    if not lineups:
        return None
    pr = _profile(lineups, regulars)
    out_ids = {a.get("id") for a in absents if a.get("raison") in OUT_REASONS} - {None}
    out_names = {a.get("joueur") for a in absents if a.get("raison") in OUT_REASONS}
    xi = _pick_xi(pr, out_ids, out_names)
    if len(xi) < 11:
        return None
    usual = _pick_xi(pr, set(), set())
    by_name = {_norm(n): pid for pid, n in pr["names"].items()}
    gone = [a.get("id") if a.get("id") in pr["pos"] else by_name.get(_norm(a.get("joueur"))) for a in absents
            if a.get("raison") in OUT_REASONS]
    reps = replacements([g for g in gone if g is not None], usual, xi, pr["pos"], pr["grid"])
    listed = []
    for a in absents:
        pid = a.get("id") if a.get("id") in pr["pos"] else by_name.get(_norm(a.get("joueur")), a.get("id"))
        if not (is_regular(a.get("id"), a.get("joueur")) or pr["starts"].get(pid, 0) >= 2):
            continue
        raison = a.get("raison") if a.get("raison") in ("blessure", "suspendu", "incertain") else "absent"
        listed.append(_entry(a.get("joueur"), raison, pid, reps.get(pid), pr, ratings, positions))
    return {"formation": pr["formation"], "titulaires": [pr["names"][p] for p in xi], "absents": listed,
            "source": "habituelle"}


def official_absents(team_lineup: dict, lineups: list[dict], absents: list[dict], ratings: dict | None = None,
                     positions: dict | None = None, regulars: set | None = None) -> list[dict]:
    """absents[] of an official line-up: the players of the usual XI (recent line-ups) who do not start, "banc" when
    on the bench, else the injuries list's reason (or "absent"), each with the starter who takes his place."""
    if not lineups:
        return []
    pr = _profile(lineups, regulars)
    usual = _pick_xi(pr, set(), set())
    xi, bench = [], set()
    for p in team_lineup.get("startXI") or []:
        pl = p["player"]
        pid = pl.get("id") or pl.get("name")
        xi.append(pid)
        pr["pos"].setdefault(pid, pl.get("pos") or "M")
        pr["names"][pid] = pl.get("name")
        pr["grid"][pid] = pl.get("grid")  # today's cell
    for p in team_lineup.get("substitutes") or []:
        bench.add(p["player"].get("id") or p["player"].get("name"))
    why = {a.get("id") or a.get("joueur"): a.get("raison") for a in absents}
    why.update({_norm(a.get("joueur")): a.get("raison") for a in absents})
    missing = [p for p in usual if p not in xi]
    reps = replacements(missing, usual, xi, pr["pos"], pr["grid"])
    out = []
    for m in missing:
        name = pr["names"].get(m)
        r = "banc" if m in bench else why.get(m) or why.get(_norm(name)) or "absent"
        r = r if r in ("banc", "blessure", "suspendu", "incertain") else "absent"
        out.append(_entry(name, r, m, reps.get(m), pr, ratings, positions))
    return out
