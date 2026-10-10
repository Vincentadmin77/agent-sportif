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
SCORER_NO_ODDS = "Aucun bookmaker français ne propose ce marché chez notre fournisseur de cotes : pas de cote à comparer."


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
