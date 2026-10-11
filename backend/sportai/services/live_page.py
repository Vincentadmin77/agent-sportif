"""Live follow-up of the results page's matches (API-Football, paid plan).

For each page match under way: minute, score, red cards, shots, possession and
expected goals when the feed has them, plus the in-play model's probabilities
(pre-match rates from the analysis database, scaled by the time left and
updated with in-play xG). Observations only: nothing here is a selection.
"""

from __future__ import annotations

import re
from datetime import datetime

import numpy as np

from sportai.connectors.base import LiveSnapshot
from sportai.connectors.teams import same_team
from sportai.engine.football.live import LiveState, final_score_matrix
from sportai.services.dashboard import bet_label, goal_label, settle, settle_score

LIVE_NOTE = ("Suivi en direct : probabilités recalculées avec le score, la minute, les cartons rouges et les "
             "occasions (xG) quand le flux les donne. Information seulement, pas une sélection.")
STAT_NAMES = {"Total Shots": "tirs", "Shots on Goal": "tirs_cadres", "Ball Possession": "possession",
              "Corner Kicks": "corners", "expected_goals": "xg"}
FINISHED = {"FT", "AET", "PEN"}
ODDS_NOTE = ("Cotes en direct du flux API-Football (pas forcément un bookmaker français). Modèle du direct non "
             "validé sur matchs passés : information, pas une sélection.")
MIN_VALUE, MAX_GAP = 0.05, 0.10
# Checks before a live price is called an opportunity (owner, 2026-10-11: the model offered Roma to win at 10
# while Roma played with ten and Como had the ball). A row that fails one is kept with statut "ecarte" and why.
LATE_MINUTE, MAX_LIVE_ODDS = 80, 6.0
_1X2 = {"Home": "home", "Draw": "draw", "Away": "away"}


def find_doc(current: dict[str, dict], day: str, home: str, away: str) -> str | None:
    for key, doc in current.items():
        if doc.get("jour") == day and same_team(doc.get("domicile", ""), home) and same_team(doc.get("exterieur", ""), away):
            return key
    return None


def pre_match_rates(fixtures: list[dict], home: str, away: str) -> tuple[tuple[float, float], dict] | None:
    """Expected goals and 1X2 probabilities of the latest analysis of this match (fixtures newest first)."""
    for f in fixtures:
        h, _, a = f["match"].partition(" - ")
        if same_team(h, home) and same_team(a, away) and f.get("expected_goals"):
            return tuple(f["expected_goals"]), f.get("probabilities") or {}
    return None


def stats_by_side(stats: list[dict], home_id: int) -> dict[str, list]:
    out: dict[str, list] = {}
    for block in stats:
        i = 0 if block["team"]["id"] == home_id else 1
        for s in block.get("statistics", []):
            name = STAT_NAMES.get(s.get("type"))
            if name and s.get("value") not in (None, ""):
                v = s["value"]
                v = int(v.rstrip("%")) if isinstance(v, str) and v.endswith("%") else float(v) if name == "xg" else v
                out.setdefault(name, [None, None])[i] = v
    return {k: v for k, v in out.items() if None not in v}


def _odd(v: dict) -> float | None:
    try:
        o = float(v.get("odd"))
    except (TypeError, ValueError):
        return None
    return o if o > 1 and not v.get("suspended") else None


def _against_the_match(side: str | None, odd: float, minute: float, reds: list[int], stats: dict[str, list]) -> str | None:
    """Why a live price should not be offered whatever the model says: too late, a long shot, or a win for a
    side that is a player down or outplayed (shots on target, possession, xG)."""
    if side in ("home", "away"):
        why = _outplayed(side, reds, stats)
        if why:
            return why
    if minute >= LATE_MINUTE:
        return f"Après la {LATE_MINUTE}e minute, cotes trop instables pour en tirer quelque chose."
    if odd > MAX_LIVE_ODDS:
        return "Cote trop longue : le modèle du direct n'est pas fiable sur les issues aussi rares."
    return None


def _outplayed(side: str, reds: list[int], stats: dict[str, list]) -> str | None:
    me, them = (0, 1) if side == "home" else (1, 0)
    if reds[me] > reds[them]:
        return "Cette équipe joue en infériorité numérique."
    on_target, possession, xg = stats.get("tirs_cadres"), stats.get("possession"), stats.get("xg")
    if on_target and on_target[them] >= on_target[me] + 2:
        return f"L'adversaire cadre plus ({on_target[them]} tirs cadrés contre {on_target[me]})."
    if possession and possession[me] <= 40:
        return f"Cette équipe subit le match ({possession[me]} % de possession)."
    if xg and xg[them] >= xg[me] + 0.5:
        return f"L'adversaire se crée plus d'occasions (xG {xg[them]:.2f} contre {xg[me]:.2f})."
    return None


def live_odds_rows(m, odds: list[dict], home: str, away: str, minute: float = 0, reds: list[int] | None = None,
                   stats: dict[str, list] | None = None) -> list[dict]:
    """Model (final score matrix m) against de-vigged in-play odds: 1X2, final total goals lines and both teams
    to score. Keeps outcomes worth at least 5 % more than their price, decided outcomes left out; an outcome the
    match itself argues against (red card, run of play, late minute, long shot) is kept as "ecarte"."""
    n = m.shape[0]
    i, j = np.indices(m.shape)
    total = i + j
    p_home, p_draw = float(m[i > j].sum()), float(m[i == j].sum())
    p_btts = float(m[1:, 1:].sum()) if n > 1 else 0.0
    markets: dict[tuple, list] = {}  # (bet, line) -> [(label, model prob, odd)]
    for bet in odds:
        name = bet.get("name")
        for v in bet.get("values") or []:
            odd, val = _odd(v), str(v.get("value", ""))
            if odd is None:
                continue
            if name == "Fulltime Result" and val in _1X2:
                sel = _1X2[val]
                p = {"home": p_home, "draw": p_draw, "away": 1 - p_home - p_draw}[sel]
                markets.setdefault((name, None), []).append((bet_label("1x2", sel, home, away), p, odd, sel))
            elif name == "Match Goals":
                side, _, rest = val.partition(" ")
                try:
                    line = float(v.get("handicap") or rest)
                except ValueError:
                    continue
                if side not in ("Over", "Under"):
                    continue
                p_over = float(m[total > line].sum())
                sel = side.lower()
                markets.setdefault((name, line), []).append(
                    (goal_label("ou", sel, line), p_over if sel == "over" else 1 - p_over, odd, None))
            elif name == "Both Teams to Score" and val in ("Yes", "No"):
                sel = "yes" if val == "Yes" else "no"
                markets.setdefault((name, None), []).append(
                    (goal_label("btts", sel, None), p_btts if sel == "yes" else 1 - p_btts, odd, None))
    rows = []
    for (name, _), outs in markets.items():
        if len(outs) != (3 if name == "Fulltime Result" else 2):
            continue  # an outcome missing or suspended: no fair market price
        book = sum(1 / o for _, _, o, _ in outs)
        for label, p, odd, side in outs:
            if not 0.001 < p < 0.999:
                continue
            pm, value = (1 / odd) / book, p * odd - 1
            if value < MIN_VALUE:
                continue
            gap = abs(p - pm)
            row = {"pari": label, "cote": odd, "proba": round(p, 4), "proba_marche": round(pm, 4),
                   "valeur": round(value, 4), "ecart": round(gap, 4),
                   "statut": "opportunite" if gap <= MAX_GAP else "ecart_trop_grand"}
            why = row["statut"] == "opportunite" and _against_the_match(side, odd, minute, reds or [0, 0], stats or {})
            if why:
                row["statut"], row["raison"] = "ecarte", why
            rows.append(row)
    return sorted(rows, key=lambda r: -r["valeur"])


def live_doc(doc: dict, fx: dict, snap: LiveSnapshot, stats: dict[str, list], pre: tuple | None, now: datetime,
             odds: list[dict] | None = None) -> dict:
    doc = dict(doc)
    status = fx["fixture"]["status"]["short"]
    hg, ag = snap.home_goals, snap.away_goals
    doc["score"] = f"{hg} - {ag}"
    doc["minute"] = int(snap.minute)
    doc["passages"] = {**(doc.get("passages") or {}), "direct": now.isoformat()}
    direct = {"minute": int(snap.minute), "periode": status, "score": doc["score"],
              "cartons_rouges": [snap.home_red_cards, snap.away_red_cards], "stats": stats, "maj": now.isoformat(),
              "note": LIVE_NOTE, "alertes": []}
    if status in FINISHED:
        doc["statut"] = "termine"
        doc["selections"] = [{**s, "resultat": settle(s["pari"], doc["domicile"], doc["exterieur"], hg, ag)}
                             for s in doc.get("selections") or []]
        settle_score(doc, hg, ag)
    else:
        doc["statut"] = "en_direct"
    if pre:
        (lh, la), probs = pre
        state = LiveState(snap.minute, hg, ag, snap.home_red_cards, snap.away_red_cards, snap.home_xg, snap.away_xg,
                          snap.observed_at, snap.provider)
        m = final_score_matrix(lh, la, state)
        n = m.shape[0]
        p_home = float(sum(m[i, j] for i in range(n) for j in range(n) if i > j))
        p_draw = float(sum(m[i, i] for i in range(n)))
        direct["proba"] = {"domicile": round(p_home, 4), "nul": round(p_draw, 4), "exterieur": round(1 - p_home - p_draw, 4)}
        if probs:
            direct["proba_avant"] = {"domicile": probs.get("1x2:home"), "nul": probs.get("1x2:draw"),
                                     "exterieur": probs.get("1x2:away")}
        alerts = direct["alertes"]
        fav = "home" if probs.get("1x2:home", 0) >= 0.55 else "away" if probs.get("1x2:away", 0) >= 0.55 else None
        if fav and status not in FINISHED and ((fav == "home" and hg < ag) or (fav == "away" and ag < hg)):
            name = doc["domicile"] if fav == "home" else doc["exterieur"]
            alerts.append(f"Le favori ({name}) est mené à la {int(snap.minute)}e minute.")
        if snap.home_xg is not None and snap.away_xg is not None:
            gap = snap.home_xg - snap.away_xg
            if gap >= 0.6 and hg < ag:
                alerts.append(f"{doc['domicile']} domine (xG {snap.home_xg:.2f} contre {snap.away_xg:.2f}) mais est mené.")
            elif gap <= -0.6 and ag < hg:
                alerts.append(f"{doc['exterieur']} domine (xG {snap.away_xg:.2f} contre {snap.home_xg:.2f}) mais est mené.")
        if odds and status not in FINISHED:
            rows = live_odds_rows(m, odds, doc["domicile"], doc["exterieur"], snap.minute,
                                  [snap.home_red_cards, snap.away_red_cards], stats)
            direct["cotes_direct"], direct["cotes_note"] = rows, ODDS_NOTE
            for r in rows:
                if r["statut"] == "opportunite":
                    alerts.append(f"Cote en direct intéressante : {r['pari']} à {str(r['cote']).replace('.', ',')} (modèle "
                                  f"{r['proba']:.0%}, marché {r['proba_marche']:.0%}, valeur {r['valeur']:+.0%}).")
    else:
        direct["sans_modele"] = "Pas d'analyse d'avant-match pour ce match : score et statistiques seulement."
    doc["direct"] = direct
    return doc


def fixture_identity(fx: dict) -> dict:
    """Team ids, crests, competition logo and round of an API-Football fixture, for the page."""
    out = {"id_domicile": fx["teams"]["home"].get("id"), "id_exterieur": fx["teams"]["away"].get("id"),
           "logo_domicile": fx["teams"]["home"].get("logo"), "logo_exterieur": fx["teams"]["away"].get("logo"),
           "logo_competition": (fx.get("league") or {}).get("logo")}
    m = re.search(r"(\d+)\s*$", str((fx.get("league") or {}).get("round") or ""))
    if m:
        out["journee"] = int(m.group(1))
    return {k: v for k, v in out.items() if v is not None}


def _played(fx: dict) -> tuple[str, str, str, int, int, str] | None:
    g = fx.get("goals") or {}
    if g.get("home") is None or g.get("away") is None:
        return None
    return (str(fx["fixture"]["date"])[:10], fx["teams"]["home"]["name"], fx["teams"]["away"]["name"],
            int(g["home"]), int(g["away"]), (fx.get("league") or {}).get("name") or "")


def recent_form(fixtures: list[dict], team_id: int) -> list[dict]:
    """A team's last matches for the page, oldest first, result from that team's point of view."""
    out = []
    for fx in fixtures:
        m = _played(fx)
        if not m:
            continue
        day, home, away, hg, ag, comp = m
        at_home = fx["teams"]["home"].get("id") == team_id
        mine, theirs = (hg, ag) if at_home else (ag, hg)
        out.append({"resultat": "V" if mine > theirs else "N" if mine == theirs else "D", "score": f"{hg}-{ag}",
                    "adversaire": away if at_home else home, "domicile": at_home, "date": day, "competition": comp})
    return sorted(out, key=lambda r: r["date"])[-5:]


def head_to_head_rows(fixtures: list[dict]) -> list[dict]:
    """The last meetings for the page, most recent first."""
    rows = [{"date": m[0], "domicile": m[1], "exterieur": m[2], "score": f"{m[3]}-{m[4]}", "competition": m[5]}
            for m in map(_played, fixtures) if m]
    return sorted(rows, key=lambda r: r["date"], reverse=True)[:5]
