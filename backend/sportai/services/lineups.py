"""Official line-ups and goalscorer estimates (API-Football, paid plan).

Line-ups come out about an hour before kick-off, so they are read at the
45 min pass. Two things are published, both for information only:

- the starting XI of each side and its notable absentees: the regular
  starters of the season (most minutes played) who are neither starting nor on
  the bench;
- for each starter, the probability of scoring at least once. The side's
  expected goals come from the match model; they are shared out between the
  starters by their scoring rate per 90 minutes (this season and last,
  shrunk towards a rate typical of their position, so a striker with two
  matches played is not credited with an absurd rate). Penalties, substitutes
  and set-piece takers are not modelled: this market is not validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Typical goals per 90 minutes by position, and how many matches of that prior a player's record weighs against.
POSITION_RATE = {"Attacker": 0.33, "Midfielder": 0.11, "Defender": 0.04, "Goalkeeper": 0.0}
POSITION_CODE = {"F": "Attacker", "M": "Midfielder", "D": "Defender", "G": "Goalkeeper"}
PRIOR_MATCHES = 6.0
STARTER_SHARE = 0.85  # share of a side's goals scored by its starting XI (the rest by substitutes)
SCORER_WARNING = ("Marché non validé : estimation à partir des buts par 90 minutes de chaque titulaire et des buts "
                  "attendus de son équipe. Ni pénaltys ni remplaçants pris en compte. Information seulement, jamais une sélection.")
SCORER_NO_ODDS = "Aucun bookmaker ne propose ce marché chez notre fournisseur de cotes pour ce match : pas de cote à comparer."
SCORER_WITH_ODDS = "Cotes des bookmakers chiffrées par le modèle buteur : voir « cotes » (marché non validé)."


@dataclass
class PlayerRecord:
    player_id: int
    name: str
    position: str
    minutes: float = 0.0
    goals: float = 0.0
    minutes_this_season: float = 0.0


def merge_players(pages: list[list[dict]], current_season: int) -> dict[int, PlayerRecord]:
    """API-Football /players responses (several seasons, several pages) -> one record per player."""
    out: dict[int, PlayerRecord] = {}
    for page in pages:
        for item in page:
            p = item["player"]
            rec = out.setdefault(p["id"], PlayerRecord(p["id"], p.get("name") or "?", ""))
            for s in item.get("statistics", []):
                mins = s["games"].get("minutes") or 0
                rec.minutes += mins
                rec.goals += s["goals"].get("total") or 0
                if not rec.position and s["games"].get("position"):
                    rec.position = s["games"]["position"]
                if (s.get("league") or {}).get("season") == current_season:
                    rec.minutes_this_season += mins
    return out


def scoring_rate(rec: PlayerRecord | None, position: str) -> float:
    """Goals per 90 minutes, shrunk towards the position's typical rate."""
    pos = (rec.position if rec and rec.position else None) or POSITION_CODE.get(position, "Midfielder")
    prior = POSITION_RATE.get(pos, 0.1)
    if not rec:
        return prior
    return (rec.goals + prior * PRIOR_MATCHES) / (rec.minutes / 90 + PRIOR_MATCHES)


def scorer_probabilities(team_xg: float, starters: list[dict], records: dict[int, PlayerRecord]) -> list[dict]:
    """starters: API-Football startXI entries ({"player": {id, name, pos}}). Probability of scoring at least once."""
    rates = []
    for s in starters:
        p = s["player"]
        rates.append((p, scoring_rate(records.get(p["id"]), p.get("pos") or "")))
    total = sum(r for _, r in rates)
    if total <= 0:
        return []
    out = []
    for p, r in rates:
        lam = team_xg * STARTER_SHARE * r / total
        out.append({"joueur": p.get("name") or "?", "proba": round(1 - math.exp(-lam), 4), "titulaire": True})
    return sorted(out, key=lambda x: -x["proba"])


def notable_absentees(records: dict[int, PlayerRecord], starters: list[dict], bench: list[dict], top: int = 11) -> list[str]:
    """Regular starters this season (most minutes) who are neither in the XI nor on the bench."""
    named = {s["player"]["id"] for s in starters + bench}
    regulars = sorted((r for r in records.values() if r.minutes_this_season > 0), key=lambda r: -r.minutes_this_season)[:top]
    return [r.name for r in regulars if r.player_id not in named]


def lineup_block(team: dict, records: dict[int, PlayerRecord], team_xg: float | None) -> dict:
    starters, bench = team.get("startXI") or [], team.get("substitutes") or []
    block = {"formation": team.get("formation"), "titulaires": [s["player"]["name"] for s in starters],
             "absents_notables": notable_absentees(records, starters, bench)}
    benched = {s["player"]["id"] for s in bench}
    regulars = sorted(records.values(), key=lambda r: -r.minutes_this_season)[:11]
    block["remplacants_notables"] = [r.name for r in regulars if r.player_id in benched]
    if team_xg is not None:
        block["buteurs"] = scorer_probabilities(team_xg, starters, records)
    return block


LINEUP_WAIT = "Compositions officielles pas encore publiées."
# A player is a "titulaire habituel" when he started at least half of his team's league matches this season:
# below that he is a rotation player whose absence says little about the side's strength.
REGULAR_SHARE = 0.5


def fill_compositions(doc: dict, fx: dict, lineups: list[dict], pages_for, xg: tuple | None) -> bool:
    """Write the official line-ups (and goalscorer estimates) of an API-Football fixture into a page document.

    lineups: /fixtures/lineups response; pages_for(team_id, season) -> /players response (list of items);
    xg: each side's expected goals from the match model, or None. While both line-ups are not out, marks the
    document as waiting, makes no player request and returns False."""
    teams = {t["team"]["id"]: t for t in lineups}
    home, away = fx["teams"]["home"], fx["teams"]["away"]
    if home["id"] not in teams or away["id"] not in teams:
        doc["compositions"] = {"attente": LINEUP_WAIT}
        return False
    season = fx["league"]["season"]
    xg = xg or (None, None)
    blocks, scorers = {}, []
    for side, team, team_xg in (("domicile", home, xg[0]), ("exterieur", away, xg[1])):
        pages = [pages_for(team["id"], s) for s in (season, season - 1)]
        block = lineup_block(teams[team["id"]], merge_players(pages, season), team_xg)
        scorers += [{**p, "equipe": doc.get(side) or team.get("name")} for p in block.pop("buteurs", [])]
        blocks[side] = block
    doc["compositions"] = blocks
    if scorers:
        # merged: the bookmakers' priced scorer odds ("cotes") and the live match sheet stay
        doc["buteurs"] = {**(doc.get("buteurs") or {}), "avertissement": SCORER_WARNING,
                          "raison": SCORER_NO_ODDS if not (doc.get("buteurs") or {}).get("cotes") else SCORER_WITH_ODDS,
                          "joueurs": sorted(scorers, key=lambda p: -p["proba"])[:8]}
    return True


def lineup_published(doc: dict) -> bool:
    c = doc.get("compositions") or {}
    return bool(c) and "attente" not in c


def regular_starters(players: list[dict], team_id: int, league_id: int | None) -> set[int]:
    """Ids of the players who started at least REGULAR_SHARE of the team's league matches this season.

    players: /players?team=&season= (current season). The team's matches played is taken as the highest
    number of appearances of one of its players in that league (someone, usually the goalkeeper, plays
    every match): at worst a slight underestimate, never invented."""
    starts: dict[int, int] = {}
    played = 0
    for item in players:
        for s in item.get("statistics", []):
            if (s.get("team") or {}).get("id") not in (None, team_id):
                continue
            if league_id is not None and (s.get("league") or {}).get("id") not in (None, league_id):
                continue
            g = s.get("games") or {}
            played = max(played, g.get("appearences") or 0)
            pid = item["player"]["id"]
            starts[pid] = starts.get(pid, 0) + (g.get("lineups") or 0)
    if played == 0:
        return set()
    return {pid for pid, n in starts.items() if n / played >= REGULAR_SHARE}


_REASONS = (("suspen", "suspendu"), ("card", "suspendu"), ("injur", "blessure"), ("knock", "blessure"),
            ("muscle", "blessure"), ("illness", "malade"), ("virus", "malade"), ("personal", "raisons personnelles"),
            ("international", "en sélection"))


def absence_reason(player: dict) -> str:
    """API-Football /injuries entry -> plain French reason ("absent" when the provider's reason is unknown)."""
    if player.get("type") == "Questionable":
        return "incertain"
    low = (player.get("reason") or "").lower()
    return next((fr for word, fr in _REASONS if word in low), "absent")


def absentees(injuries: list[dict], fx: dict, regulars: dict[int, set[int]]) -> dict:
    """{"domicile": [...], "exterieur": [...]} from /injuries?fixture=ID, regular starters first.

    regulars: team id -> ids of its regular starters (regular_starters)."""
    out: dict[str, list] = {"domicile": [], "exterieur": []}
    sides = {fx["teams"]["home"]["id"]: "domicile", fx["teams"]["away"]["id"]: "exterieur"}
    seen = set()
    for it in injuries:
        tid, p = (it.get("team") or {}).get("id"), it.get("player") or {}
        if tid not in sides or not p.get("name") or (tid, p.get("id")) in seen:
            continue
        seen.add((tid, p.get("id")))
        out[sides[tid]].append({"joueur": p["name"], "raison": absence_reason(p), "detail": p.get("reason"),
                                "titulaire_habituel": p.get("id") in regulars.get(tid, set())})
    for side in out:
        out[side].sort(key=lambda a: not a["titulaire_habituel"])
    return out


SQUAD_PREFIXES = ("Absents chez ", "Onze de départ publié")


def scenario_with_squads(doc: dict) -> str | None:
    """The match scenario followed by the absentees and the starting XIs, rebuilt from the document's data:
    whatever an earlier pass appended is replaced, never repeated."""
    base = doc.get("scenario") or ""
    cuts = [i for i in (base.find(p) for p in SQUAD_PREFIXES) if i >= 0]
    base = (base[:min(cuts)] if cuts else base).strip()
    extra = []
    for side in ("domicile", "exterieur"):
        missing = (doc.get("absents") or {}).get(side) or []
        if missing:
            n = sum(a["titulaire_habituel"] for a in missing)
            names = ", ".join(f"{a['joueur']} ({a['raison']})" for a in missing)
            tail = "" if not n else ", dont 1 titulaire habituel" if n == 1 else f", dont {n} titulaires habituels"
            extra.append(f"Absents chez {doc.get(side, side)} : {names}{tail}.")
    if lineup_published(doc):
        parts = []
        for side in ("domicile", "exterieur"):
            b = doc["compositions"].get(side) or {}
            if b.get("titulaires"):
                form = f" ({b['formation']})" if b.get("formation") else ""
                parts.append(f"{doc.get(side, side)}{form} : {', '.join(b['titulaires'])}")
        if parts:
            extra.append("Onze de départ publié. " + " ; ".join(parts) + ".")
    return " ".join(([base] if base else []) + extra) or None
