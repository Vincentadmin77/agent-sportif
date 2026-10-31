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


def absentees(injuries: list[dict], fx: dict, regulars: dict[int, set[int]],
              players: dict[int, "TeamPlayers"] | None = None) -> dict:
    """{"domicile": [...], "exterieur": [...]} from /injuries?fixture=ID, regular starters first.

    regulars: team id -> ids of its regular starters (regular_starters). players: team id -> TeamPlayers; when given,
    each entry also holds the player id, his share of the team's goals + assists ("part_bp") and whether he is a
    regular goalkeeper / defender ("defense_habituel"), so the absence factor is computed again with no request."""
    out: dict[str, list] = {"domicile": [], "exterieur": []}
    sides = {fx["teams"]["home"]["id"]: "domicile", fx["teams"]["away"]["id"]: "exterieur"}
    seen = set()
    for it in injuries:
        tid, p = (it.get("team") or {}).get("id"), it.get("player") or {}
        if tid not in sides or not p.get("name") or (tid, p.get("id")) in seen:
            continue
        seen.add((tid, p.get("id")))
        entry = {"joueur": p["name"], "raison": absence_reason(p), "detail": p.get("reason"),
                 "titulaire_habituel": p.get("id") in regulars.get(tid, set())}
        if players is not None:
            tp = players.get(tid)
            entry.update(id=p.get("id"), part_bp=round(tp.shares.get(p.get("id"), 0.0), 4) if tp else 0.0,
                         defense_habituel=bool(tp) and p.get("id") in tp.defenders)
        out[sides[tid]].append(entry)
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


# ---------------------------------------------------------------------------------------------------------------
# Missing key players: correction of the result markets (docs/22-absences.md).
#
# Walk-forward study on 13 559 league matches (2020-2026): a side whose players worth a share GA_out of its goals and
# assists are not in the matchday squad (neither starting nor on the bench) scores fewer goals than the match model
# expects, and a side facing an opponent without DEF_out of its regular goalkeepers / defenders scores a little more.
# Fitted on 2020-2025, tested on 2025-07 to 2026-10: 1X2 log-loss -0.0025 [-0.0045, -0.0006], over/under not
# improved, so the factor moves the result markets only (1X2, double chance, draw no bet, handicaps):
#   f = exp(k * (A * (GA_out - GA_MEAN) + B * (DEF_out_opponent - DEF_MEAN))), clipped to [ABSENCE_MIN, ABSENCE_MAX]
# k = 1 once the official line-ups are out, 0.5 from the injuries list only (it misses the coach's omissions).
ABSENCE_A, ABSENCE_B = -0.395, 0.046
ABSENCE_GA_MEAN, ABSENCE_DEF_MEAN = 0.128, 0.423
ABSENCE_MIN, ABSENCE_MAX = 0.80, 1.10
STRENGTH_LINEUPS, STRENGTH_INJURIES = 1.0, 0.5
SOURCE_LINEUPS, SOURCE_INJURIES = "compositions", "blessés"
ABSENCE_PHRASE_FROM = 0.05  # |f - 1| from which the page says it in words
DEFENSIVE_POSITIONS = ("Goalkeeper", "Defender")


def absence_factor(ga_out: float, def_out_opponent: float, strength: float = 1.0) -> float:
    """Multiplier of a side's expected goals for the result markets. strength: 1 with the official line-ups,
    0.5 with the injuries list (the exponent is halved)."""
    x = ABSENCE_A * (ga_out - ABSENCE_GA_MEAN) + ABSENCE_B * (def_out_opponent - ABSENCE_DEF_MEAN)
    return float(min(max(math.exp(strength * x), ABSENCE_MIN), ABSENCE_MAX))


@dataclass
class TeamPlayers:
    """What the absence factor needs to know about a squad, from the /players pages already fetched for it."""
    shares: dict[int, float]  # player id -> share of the team's goals + assists (this season and last, this team only)
    names: dict[int, str]
    roster: set[int]  # players of the team this season (one who left in the summer is not "missing")
    defenders: set[int]  # regular starters this season who are goalkeepers or defenders


def team_players_index(current: list[dict], previous: list[dict], team_id: int, league_id: int | None) -> TeamPlayers:
    """current / previous: /players?team=&season= of this season and of the last one."""
    ga: dict[int, float] = {}
    names: dict[int, str] = {}
    roster: set[int] = set()
    positions: dict[int, str] = {}
    for page, this_season in ((current, True), (previous, False)):
        for item in page:
            p = item.get("player") or {}
            pid = p.get("id")
            if pid is None:
                continue
            names.setdefault(pid, p.get("name") or "?")
            for s in item.get("statistics", []):
                if (s.get("team") or {}).get("id") not in (None, team_id):
                    continue
                if this_season:
                    roster.add(pid)
                g = s.get("goals") or {}
                ga[pid] = ga.get(pid, 0.0) + (g.get("total") or 0) + (g.get("assists") or 0)
                pos = (s.get("games") or {}).get("position")
                if pos and pid not in positions:
                    positions[pid] = pos
    total = sum(ga.values())
    shares = {pid: v / total for pid, v in ga.items() if v > 0} if total > 0 else {}
    defenders = {pid for pid in regular_starters(current, team_id, league_id) if positions.get(pid) in DEFENSIVE_POSITIONS}
    return TeamPlayers(shares, names, roster, defenders)


@dataclass
class SideAbsence:
    ga_out: float  # share of the side's goals + assists missing
    missing: list[tuple[str, float]]  # (player, share) of the missing contributors, biggest first
    def_out: int  # regular goalkeepers / defenders missing
    def_missing: list[str]


def side_from_lineup(lineup: dict, tp: TeamPlayers) -> SideAbsence:
    """Official line-up of one side (/fixtures/lineups item): every player of this season's roster neither in the XI
    nor on the bench is missing. A benched player is not."""
    named = {(s.get("player") or {}).get("id") for s in (lineup.get("startXI") or []) + (lineup.get("substitutes") or [])}
    out = tp.roster - named
    missing = sorted(((tp.names.get(pid, "?"), tp.shares[pid]) for pid in out if tp.shares.get(pid)), key=lambda x: -x[1])
    defs = sorted(tp.names.get(pid, "?") for pid in out & tp.defenders)
    return SideAbsence(sum(s for _, s in missing), missing, len(defs), defs)


def side_from_absents(entries: list[dict]) -> SideAbsence:
    """One side of a stored "absents" block (absentees with players): those listed as missing, the doubtful left out."""
    sure = [e for e in entries if e.get("raison") != "incertain"]
    missing = sorted(((e["joueur"], float(e["part_bp"])) for e in sure if e.get("part_bp")), key=lambda x: -x[1])
    defs = sorted(e["joueur"] for e in sure if e.get("defense_habituel"))
    return SideAbsence(sum(s for _, s in missing), missing, len(defs), defs)


def absents_ready(absents: dict | None, day: str) -> bool:
    """A stored "absents" block of that day that already holds what the factor needs (no request to make again)."""
    if not absents or absents.get("jour") != day or absents.get("indisponible"):
        return False
    return all("part_bp" in e for side in ("domicile", "exterieur") for e in absents.get(side) or [])


def _names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " et " + xs[-1]


def absence_phrase(team: str, opponent: str, f: float, side: SideAbsence, opp: SideAbsence) -> str | None:
    """One plain sentence when the factor moves a side's attack by 5 % or more."""
    if abs(f - 1) < ABSENCE_PHRASE_FROM:
        return None
    if f < 1 and side.missing:
        who = [n for n, s in side.missing if s >= 0.05][:3] or [side.missing[0][0]]
        return (f"Sans {_names(who)} ({round(side.ga_out * 100):.0f} % des buts et passes décisives), "
                f"l'attaque de {team} est revue à la baisse.")
    if f > 1 and opp.def_missing:
        return f"Sans {_names(opp.def_missing[:3])} en défense chez {opponent}, l'attaque de {team} est revue à la hausse."
    return None


def absence_effect(home: SideAbsence, away: SideAbsence, home_name: str, away_name: str, source: str) -> dict:
    """Both sides' factors and their sentences: {"domicile", "exterieur", "source", "phrases"}."""
    strength = STRENGTH_LINEUPS if source == SOURCE_LINEUPS else STRENGTH_INJURIES
    f_h = absence_factor(home.ga_out, away.def_out, strength)
    f_a = absence_factor(away.ga_out, home.def_out, strength)
    phrases = [p for p in (absence_phrase(home_name, away_name, f_h, home, away),
                           absence_phrase(away_name, home_name, f_a, away, home)) if p]
    return {"domicile": round(f_h, 4), "exterieur": round(f_a, 4), "source": source, "phrases": phrases}
