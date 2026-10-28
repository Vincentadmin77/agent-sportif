"""Every player's decayed goal record, across seasons and clubs, for the anytime goalscorer model.

Walk-forward study of 2026-10-28 (docs/23-buteurs-stars.md): 15,591 finished league fixtures of the cache, 479,104
player-match rows. Tuned on 2022-07 to 2025-06, tested on 2025-07 to 2026-10 (starters): log-loss 0.2637 against
0.2722 for the this-season-only share it replaces, well calibrated, and 0.6125 against 0.6715 (old) and 0.6823
(market 1/odds) on the page's 113 decided scorer offers of 15-27 October. For a player who starts:

    G_H, M_H   goals (own goals and missed penalties out, penalties in) and minutes of all his past matches, any club,
               each match weighted 0.5 ** (age in days / H)
    prior      POSITION_SHARE[pos] * team_att      (F 0.22, M 0.08, D 0.03, G 0)
    team_att   his team's goals per match, half-life 150 days, shrunk to the league average with 5 matches
    rate_form  (G_90 + 16 * prior) / (M_90 / 90 + 16)
    rate_long  (G_3650 + 20 * prior) / (M_3650 / 90 + 20)
    rate90     0.25 * rate_form + 0.75 * rate_long
    mult       (opponent's goals conceded per match / league average) * 1.15 at home, / 1.15 away
    minutes    min((decayed minutes of his starts + 3 * 80) / (decayed starts + 3), 90), half-life 180 days
    lambda     0.975 * rate90 * minutes / 90 * mult ;  P = 1 - exp(-lambda)

The league average is the league's goals per team-match, half-life 365 days, shrunk to 1.35 with 20 team-matches.

The state is order-free: a match older than a record's date is added with its own decay, so the cache can take new
fixtures in any order and a fixture is never counted twice (its id is remembered).
"""

from __future__ import annotations

import math
from collections import Counter

POSITION_SHARE = {"F": 0.22, "M": 0.08, "D": 0.03, "G": 0.0}
HALF_FORM, SHRINK_FORM = 90.0, 16.0
HALF_LONG, SHRINK_LONG = 3650.0, 20.0
BLEND_FORM = 0.25
HALF_TEAM, TEAM_PSEUDO = 150.0, 5.0
HALF_LEAGUE, LEAGUE_PSEUDO, LEAGUE_PRIOR = 365.0, 20.0, 1.35
HALF_START, START_PSEUDO, START_PRIOR_MINUTES = 180.0, 3.0, 80.0
HOME_FACTOR = 1.15
SCALE = 0.975
MAX_MINUTES = 90.0
MATCH_CAP = 120.0  # minutes of one match counted at most (extra time)
FINISHED = ("FT", "AET", "PEN")
DAY = 86400.0
# The opponent is not in the history (a European or cup opponent from another league): the match model's expected
# goals for the team over the team's usual goals per match stand in for the opponent and venue, within these bounds.
XG_MULT_BOUNDS = (0.5, 2.0)


def decay(dt_seconds: float, half_days: float) -> float:
    return 0.5 ** (dt_seconds / DAY / half_days)


def _add(rec: list, ts: float, values: list[float], half: float | list[float]) -> None:
    """rec = [ts, v1, v2, ...] decayed to rec[0]; add values observed at ts (before or after rec[0])."""
    halves = half if isinstance(half, list) else [half] * len(values)
    if ts >= rec[0]:
        for i, h in enumerate(halves, 1):
            rec[i] *= decay(ts - rec[0], h)
        rec[0] = ts
        w = [1.0] * len(values)
    else:
        w = [decay(rec[0] - ts, h) for h in halves]
    for i, v in enumerate(values, 1):
        rec[i] += v * w[i - 1]


def _at(rec: list, ts: float, half: float, i: int) -> float:
    return rec[i] * decay(ts - rec[0], half) if ts >= rec[0] else rec[i]


class History:
    """data: {"players": {pid: P}, "teams": {tid: T}, "leagues": {lid: L}, "fixtures": [ids], "files": [names]}
    P = [name, pos, ts, G_form, M_form, G_long, M_long, ts_start, starts, start_minutes, team]
    T = [ts, scored, conceded, matches, league]      L = [ts, goals, team_matches]   (all decayed to their ts)."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.players: dict[str, list] = data.get("players") or {}
        self.teams: dict[str, list] = data.get("teams") or {}
        self.leagues: dict[str, list] = data.get("leagues") or {}
        self.fixtures: set[int] = set(data.get("fixtures") or [])
        self.files: set[str] = set(data.get("files") or [])
        self.built_at: str | None = data.get("built_at")

    def to_dict(self) -> dict:
        r = lambda v: round(v, 5) if isinstance(v, float) else v  # noqa: E731
        return {"version": 1, "built_at": self.built_at,
                "players": {k: [r(v) for v in p] for k, p in self.players.items()},
                "teams": {k: [r(v) for v in t] for k, t in self.teams.items()},
                "leagues": {k: [r(v) for v in lg] for k, lg in self.leagues.items()},
                "fixtures": sorted(self.fixtures), "files": sorted(self.files)}

    # --- building --------------------------------------------------------------------------------------------------

    def add_fixture(self, x: dict) -> bool:
        """One API-Football /fixtures?ids= item (events, lineups, players). False when it is not usable (not
        finished, no player statistics) or already counted."""
        fid = (x.get("fixture") or {}).get("id")
        if fid is None or fid in self.fixtures or ((x["fixture"].get("status") or {}).get("short") not in FINISHED
                                                   or not x.get("players")):
            return False
        ts = float(x["fixture"]["timestamp"])
        lid = str((x.get("league") or {}).get("id"))
        h, a = x["teams"]["home"]["id"], x["teams"]["away"]["id"]
        gh, ga = x["goals"]["home"] or 0, x["goals"]["away"] or 0
        goals = Counter()
        for e in x.get("events") or []:
            if e.get("type") == "Goal" and e.get("detail") not in ("Own Goal", "Missed Penalty"):
                goals[(e.get("player") or {}).get("id")] += 1
        starters = {p["player"]["id"] for lu in x.get("lineups") or [] for p in lu.get("startXI") or []
                    if (p.get("player") or {}).get("id")}
        for tp in x["players"]:
            tid = tp["team"]["id"]
            for p in tp.get("players") or []:
                pid = p["player"]["id"]
                s = (p.get("statistics") or [{}])[0]
                g = s.get("games") or {}
                mins = min(g.get("minutes") or 0, MATCH_CAP)
                if mins <= 0:
                    continue
                started = pid in starters if starters else g.get("substitute") is False
                rec = self.players.get(str(pid))
                if rec is None:
                    rec = self.players[str(pid)] = [p["player"].get("name") or "?", None, ts, 0.0, 0.0, 0.0, 0.0,
                                                    ts, 0.0, 0.0, tid]
                newest = ts >= rec[2]
                vals = rec[2:7]
                _add(vals, ts, [goals.get(pid, 0), mins, goals.get(pid, 0), mins],
                     [HALF_FORM, HALF_FORM, HALF_LONG, HALF_LONG])
                rec[2:7] = vals
                if started:
                    st = rec[7:10]
                    _add(st, ts, [1.0, mins], HALF_START)
                    rec[7:10] = st
                if newest:
                    rec[10] = tid
                    rec[0] = p["player"].get("name") or rec[0]
                if g.get("position") and (newest or rec[1] is None):
                    rec[1] = g["position"]
        for tid, scored, conceded in ((h, gh, ga), (a, ga, gh)):
            t = self.teams.setdefault(str(tid), [ts, 0.0, 0.0, 0.0, lid])
            newest = ts >= t[0]
            vals = t[:4]
            _add(vals, ts, [scored, conceded, 1.0], HALF_TEAM)
            t[:4] = vals
            if newest:
                t[4] = lid
            lg = self.leagues.setdefault(lid, [ts, 0.0, 0.0])
            _add(lg, ts, [scored, 1.0], HALF_LEAGUE)
        self.fixtures.add(fid)
        return True

    # --- reading ---------------------------------------------------------------------------------------------------

    def knows(self, pid) -> bool:
        return str(pid) in self.players

    def league_avg(self, league, ts: float) -> float:
        lg = self.leagues.get(str(league))
        if not lg:
            return LEAGUE_PRIOR
        d = decay(ts - lg[0], HALF_LEAGUE) if ts >= lg[0] else 1.0
        return (lg[1] * d + LEAGUE_PRIOR * LEAGUE_PSEUDO) / (lg[2] * d + LEAGUE_PSEUDO)

    def _team(self, team, ts: float, i: int, league=None) -> tuple[float, float] | None:
        """(team's decayed goals scored (i=1) or conceded (i=2) per match shrunk to its league, that league's
        average); None for a team not in the history."""
        t = self.teams.get(str(team))
        if not t:
            return None
        lavg = self.league_avg(league if league is not None and str(league) in self.leagues else t[4], ts)
        d = decay(ts - t[0], HALF_TEAM) if ts >= t[0] else 1.0
        return (t[i] * d + TEAM_PSEUDO * lavg) / (t[3] * d + TEAM_PSEUDO), lavg

    def team_att(self, team, ts: float, league=None) -> float | None:
        r = self._team(team, ts, 1, league)
        return None if r is None else r[0]

    def multiplier(self, team, opponent, home: bool | None, ts: float, league=None,
                   team_xg: float | None = None) -> tuple[float, str]:
        """Opponent's defence over the league average, times the home factor (home True, away False, neutral None).
        Opponent not in the history: team_xg over the team's usual goals (bounded), else the venue alone."""
        venue = 1.0 if home is None else (HOME_FACTOR if home else 1 / HOME_FACTOR)
        opp = self._team(opponent, ts, 2, league)
        if opp is not None:
            return opp[0] / opp[1] * venue, "adversaire"
        att = self.team_att(team, ts, league)
        if team_xg and att:
            lo, hi = XG_MULT_BOUNDS
            return min(max(team_xg / att, lo), hi), "buts attendus du match"
        return venue, "neutre"

    def rate90(self, pid, ts: float, team=None, position: str | None = None, league=None) -> float | None:
        """Goals per 90 minutes: decayed record (90 days and 10 years) shrunk to his position's share of his team's
        goals; position (F, M, D, G) is the line-up's when known, else his last one. None when he is not in the
        history."""
        rec = self.players.get(str(pid))
        if rec is None:
            return None
        pos = POSITION_SHARE.get(position or rec[1] or "M", POSITION_SHARE["M"])
        att = self.team_att(team if team is not None else rec[10], ts, league)
        if att is None:  # his team is not in the history (a club of another league): a league-average attack
            att = self.league_avg(league, ts)
        prior = pos * att
        d = lambda h: decay(ts - rec[2], h) if ts >= rec[2] else 1.0  # noqa: E731
        g_f, m_f = rec[3] * d(HALF_FORM), rec[4] * d(HALF_FORM)
        g_l, m_l = rec[5] * d(HALF_LONG), rec[6] * d(HALF_LONG)
        form = (g_f + SHRINK_FORM * prior) / (m_f / 90 + SHRINK_FORM)
        long = (g_l + SHRINK_LONG * prior) / (m_l / 90 + SHRINK_LONG)
        return BLEND_FORM * form + (1 - BLEND_FORM) * long

    def start_minutes(self, pid, ts: float) -> float | None:
        """Minutes he usually plays when he starts (decayed, 180 days), shrunk to 80 with 3 starts."""
        rec = self.players.get(str(pid))
        if rec is None:
            return None
        d = decay(ts - rec[7], HALF_START) if ts >= rec[7] else 1.0
        return min((rec[9] * d + START_PSEUDO * START_PRIOR_MINUTES) / (rec[8] * d + START_PSEUDO), MAX_MINUTES)


def star_lambda90(rate90: float, mult: float) -> float:
    """Expected goals of the player per 90 minutes in this match."""
    return SCALE * max(rate90, 0.0) * max(mult, 0.0)


def star_proba(rate90: float, mult: float, minutes: float) -> float:
    return 1 - math.exp(-star_lambda90(rate90, mult) * min(max(minutes, 0.0), MAX_MINUTES) / MAX_MINUTES)
