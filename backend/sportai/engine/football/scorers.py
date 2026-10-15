"""Anytime goalscorer model: the chance that one player scores at least once in a match.

Owner (2026-10-15), after the 2026-10-14 Champions League night: "le joueur pro, qui a l'occasion de jouer en direct
aurait pu saisir des cotes comme buteur Haaland ou Mbappé ou sur un match analysé confiance comme Liverpool mettre un
buteur". Earlier rule (2026-10-14): every market a bookmaker offers is analysed; one the backtest has not validated is
labelled "non validé" and played on trial. This market has never been backtested: always "non validé".

    P(player scores) = 1 - exp(-lambda_team * (1 - OWN_GOALS) * share * minutes_fraction)

- lambda_team: the team's expected goals from the match model (pre-match run), or the goals still expected from the
  in-play model (live).
- OWN_GOALS: part of a team's goals that are the opponents' own goals, never a player's.
- share: the player's goals per 90 minutes over his team's goals per match (players' goals only, so own goals are out
  on both sides), this season, every competition he played for this team (API-Football /players), shrunk towards a
  rate typical of his position worth PRIOR_90S full matches, so one goal in two games is not a 50 % striker.
  Penalties count: a penalty taker's rate already holds them.
- minutes_fraction: from the official line-up when known (a starter plays his usual minutes per start, a substitute
  about BENCH_MINUTES, a player absent from the team sheet 0), else his minutes per appearance. Bookmakers refund the
  bet when the player does not play, so the chance is the one given that he plays.

The bookmakers' scorer lists are one-sided (one price per player, no "no" side): no margin can be removed, so the
market's chance shown is the implied 1/odds, margin included, and says so.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

OWN_GOALS = 0.03  # about 3 % of goals are own goals (top five leagues)
# Typical share of a team's goals per 90 minutes on the pitch, by position: a front line of two attackers, four
# midfielders and four defenders sums to about 0.88, the rest come from substitutes.
POSITION_SHARE = {"Attacker": 0.22, "Midfielder": 0.08, "Defender": 0.03, "Goalkeeper": 0.0}
POSITION_CODE = {"F": "Attacker", "M": "Midfielder", "D": "Defender", "G": "Goalkeeper"}
PRIOR_90S = 6.0  # weight of the position prior, in full matches
BENCH_MINUTES = 20.0  # a substitute who comes on plays about this long
MATCH_MINUTES = 90.0
MIN_TEAM_MATCHES = 2  # fewer matches known for the team: no share can be measured
SCORER_BETS = ("Anytime Goal Scorer", "Home Anytime Goal Scorer", "Away Anytime Goal Scorer")
MARKET_NOTE = ("Buteur à tout moment : marché jamais testé sur matchs passés (non validé). Chance du modèle : buts "
               "attendus de l'équipe × part des buts de l'équipe marqués par le joueur cette saison (ramenée vers la "
               "moyenne de son poste) × temps de jeu prévu. La chance du marché est 1/cote, marge du bookmaker "
               "comprise : une liste de buteurs n'a pas de côté « non », la marge ne peut pas être retirée. Pari "
               "remboursé si le joueur ne joue pas ; un but contre son camp ne compte pas.")


@dataclass
class PlayerSeason:
    player_id: int
    name: str  # API-Football display name, "E. Haaland"
    firstname: str = ""
    lastname: str = ""
    position: str = ""
    goals: float = 0.0
    penalties: float = 0.0
    minutes: float = 0.0
    appearances: int = 0
    starts: int = 0
    subs_in: int = 0

    def minutes_per_start(self) -> float:
        """Usual minutes when he starts: minutes left after his substitute appearances, per start."""
        if not self.starts:
            return MATCH_MINUTES * 0.8
        return min(max((self.minutes - BENCH_MINUTES * self.subs_in) / self.starts, 45.0), MATCH_MINUTES)

    def minutes_per_appearance(self) -> float:
        return min(self.minutes / self.appearances, MATCH_MINUTES) if self.appearances else MATCH_MINUTES * 0.6


@dataclass
class Squad:
    team_id: int
    players: dict[int, PlayerSeason]
    goals: float  # goals by the team's players this season (own goals by opponents not included)
    matches: int  # the team's matches this season, from its players' appearances

    def goals_per_match(self) -> float | None:
        return self.goals / self.matches if self.matches >= MIN_TEAM_MATCHES and self.goals > 0 else None


def squad_from_api(items: list[dict], team_id: int, season: int) -> Squad:
    """API-Football /players?team=&season= (all pages) -> the team's players with their season for this team only:
    statistics with another club (a player transferred in) are left out. The team's matches are, per competition, the
    most appearances of one of its players (someone, usually the goalkeeper, plays them all)."""
    players: dict[int, PlayerSeason] = {}
    per_comp: dict[object, int] = {}
    goals = 0.0
    for item in items:
        p = item.get("player") or {}
        if p.get("id") is None:
            continue
        rec = players.setdefault(p["id"], PlayerSeason(p["id"], p.get("name") or "?", p.get("firstname") or "",
                                                       p.get("lastname") or ""))
        for s in item.get("statistics") or []:
            if (s.get("team") or {}).get("id") not in (None, team_id):
                continue
            league = s.get("league") or {}
            if league.get("season") not in (None, season):
                continue
            g = s.get("games") or {}
            n = g.get("appearences") or 0
            rec.appearances += n
            rec.starts += g.get("lineups") or 0
            rec.minutes += g.get("minutes") or 0
            rec.subs_in += (s.get("substitutes") or {}).get("in") or 0
            scored = (s.get("goals") or {}).get("total") or 0
            rec.goals += scored
            rec.penalties += (s.get("penalty") or {}).get("scored") or 0
            goals += scored
            if not rec.position and g.get("position"):
                rec.position = g["position"]
            key = league.get("id") or league.get("name")
            per_comp[key] = max(per_comp.get(key, 0), n)
    return Squad(team_id, players, goals, sum(per_comp.values()))


def share(player: PlayerSeason, squad: Squad) -> float | None:
    """The player's share of his team's goals per 90 minutes on the pitch, shrunk towards his position's typical
    share. None when the team's scoring this season is unknown."""
    team = squad.goals_per_match()
    if team is None:
        return None
    pos = POSITION_SHARE.get(player.position or "", POSITION_SHARE["Midfielder"])
    rate = (player.goals + pos * team * PRIOR_90S) / (player.minutes / MATCH_MINUTES + PRIOR_90S)
    return min(rate / team, 0.9)


def anytime_proba(team_xg: float, part: float, minutes: float) -> float:
    """Chance of at least one goal: Poisson with the team's expected goals (own goals out), the player's share and
    the fraction of the match he plays."""
    lam = max(team_xg, 0.0) * (1 - OWN_GOALS) * max(part, 0.0) * min(max(minutes, 0.0), MATCH_MINUTES) / MATCH_MINUTES
    return 1 - math.exp(-lam)


def expected_minutes(player: PlayerSeason, lineup: str | None) -> float:
    """Minutes he should play given that he plays: lineup "titulaire", "remplacant", "absent" or None (unknown)."""
    if lineup == "absent":
        return 0.0
    if lineup == "titulaire":
        return player.minutes_per_start()
    if lineup == "remplacant":
        return BENCH_MINUTES
    return player.minutes_per_appearance()


# --- names ---------------------------------------------------------------------------------------------------------

def _tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9.]+", s.replace("-", " ").replace("'", "")) if t]


def _initial(t: str) -> bool:
    return len(t.rstrip(".")) == 1


def _player_tokens(p: PlayerSeason) -> tuple[set[str], set[str], set[str]]:
    """(surname tokens, given-name tokens, initials) of an API-Football player."""
    shown = [t.rstrip(".") for t in _tokens(p.name)]
    last = {t.rstrip(".") for t in _tokens(p.lastname)}
    first = {t.rstrip(".") for t in _tokens(p.firstname)}
    full = [t for t in shown if len(t) > 1]
    surnames = last | (set(full[-1:]) if full else set())
    given = first | set(full)
    initials = {t[0] for t in first} | {t for t in shown if len(t) == 1}
    return surnames | (set(full) if len(full) == 1 else set()), given, initials


def name_score(bookmaker_name: str, p: PlayerSeason) -> int:
    """How well a bookmaker's spelling ("Erling Haaland", "E. Haaland", "Haaland") names this player: 0 for no
    match, more for more tokens confirmed. Accents, case and hyphens ignored; initials accepted for given names."""
    toks = [t.rstrip(".") for t in _tokens(bookmaker_name)]
    if not toks:
        return 0
    surnames, given, initials = _player_tokens(p)
    if toks[-1] not in surnames and toks[-1] not in given:
        return 0
    score = 2 if toks[-1] in surnames else 1
    for t in toks[:-1]:
        if len(t) == 1:
            if t not in initials:
                return 0
            score += 1
        elif t in given or t in surnames:
            score += 2
        elif t[0] in initials and len(toks) == 2 and toks[-1] in surnames:
            score += 0  # a nickname or another spelling of the first name (Yegor / Yehor): surname decides
        else:
            return 0
    return score


def match_player(bookmaker_name: str, squads: list[Squad]) -> tuple[Squad, PlayerSeason] | None:
    """The one player of either squad this name designates; None when no player matches (the feed can list a
    player of another club) or when two match equally (cannot tell which)."""
    best, tie, top = None, False, 0
    for sq in squads:
        for p in sq.players.values():
            s = name_score(bookmaker_name, p)
            if s > top:
                best, tie, top = (sq, p), False, s
            elif s == top and s > 0:
                tie = True
    return None if tie or best is None else best


def best_scorer_odds(bookmakers: list[dict], names: tuple[str, ...] = SCORER_BETS) -> dict[str, dict]:
    """Best price per player name over the bookmakers of one fixture (API-Football /odds item "bookmakers", or the
    bets of /odds/live wrapped as one bookmaker): {name: {"cote", "bookmaker", "bookmakers"}}."""
    out: dict[str, dict] = {}
    for bk in bookmakers or []:
        book = bk.get("name") or str(bk.get("id"))
        for bet in bk.get("bets") or []:
            if bet.get("name") not in names:
                continue
            for v in bet.get("values") or []:
                try:
                    odd = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                name = str(v.get("value") or "").strip()
                if odd <= 1 or not name or v.get("suspended") or name.lower().startswith("no goal"):
                    continue
                o = out.setdefault(name, {"cote": odd, "bookmaker": book, "books": set()})
                o["books"].add(book)
                if odd > o["cote"]:
                    o["cote"], o["bookmaker"] = odd, book
    return {k: {"cote": v["cote"], "bookmaker": v["bookmaker"], "bookmakers": len(v["books"])} for k, v in out.items()}


def price_scorers(odds: dict[str, dict], squads: dict[str, Squad], team_xg: dict[str, float],
                  lineups: dict[str, dict[int, str]] | None = None) -> tuple[list[dict], list[str]]:
    """Each bookmaker selection priced by the model.

    squads / team_xg: by side ("domicile", "exterieur"); lineups: by side, player id -> "titulaire" / "remplacant"
    (a player of the squad missing from a published team sheet is "absent"). Returns the rows, best value first, and
    the names left out (not a player of either team, ambiguous, absent, or no record)."""
    rows, dropped = [], []
    sides = list(squads)
    for name, o in sorted(odds.items()):
        found = match_player(name, [squads[s] for s in sides])
        if found is None:
            dropped.append(f"{name} : pas un joueur de l'une des deux équipes (ou nom ambigu)")
            continue
        sq, p = found
        side = next(s for s in sides if squads[s] is sq)
        sheet = (lineups or {}).get(side)
        lineup = None if not sheet else sheet.get(p.player_id, "absent")
        part = share(p, sq)
        minutes = expected_minutes(p, lineup)
        if part is None or side not in team_xg:
            dropped.append(f"{name} : buts de l'équipe cette saison inconnus")
            continue
        if minutes <= 0:
            dropped.append(f"{name} : absent de la feuille de match")
            continue
        proba = anytime_proba(team_xg[side], part, minutes)
        rows.append({"joueur": name, "joueur_id": p.player_id, "nom_api": p.name, "cote_equipe": side,
                     "cote": o["cote"], "bookmaker": o["bookmaker"], "bookmakers": o.get("bookmakers", 1),
                     "proba": round(proba, 4), "proba_marche": round(1 / o["cote"], 4),
                     "valeur": round(proba * o["cote"] - 1, 4), "part": round(part * (1 - OWN_GOALS), 4),
                     "minutes": round(minutes, 1), "minutes_par_titularisation": round(p.minutes_per_start(), 1),
                     "composition": lineup or "inconnue", "buts_saison": p.goals, "minutes_saison": p.minutes,
                     "poste": p.position or None, "valide": False})
    return sorted(rows, key=lambda r: -r["valeur"]), dropped


def live_scorer_proba(remaining_xg: float, part: float, minute: float, usual_minutes: float | None,
                      on_pitch: bool, came_on: bool = False, match_length: float = 95.0) -> float:
    """In play: chance he scores from now on. part already excludes own goals (price_scorers' "part"). A player off
    the pitch (substituted, sent off, never on) has 0; a starter is expected to stay until his usual minutes."""
    if not on_pitch:
        return 0.0
    left = max(match_length - minute, 1.0)
    stay = 1.0 if came_on or not usual_minutes else min(max((usual_minutes - minute) / left, 0.3), 1.0)
    return 1 - math.exp(-max(remaining_xg, 0.0) * max(part, 0.0) * stay)
