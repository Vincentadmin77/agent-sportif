"""Live follow-up of the results page's matches (API-Football, paid plan).

For each page match under way: minute, score, red cards, shots, possession and
expected goals when the feed has them, plus the in-play model's probabilities
(pre-match rates from the analysis database, scaled by the time left and
updated with in-play xG). Observations only: nothing here is a selection.
"""

from __future__ import annotations

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


def live_odds_rows(m, odds: list[dict], home: str, away: str) -> list[dict]:
    """Model (final score matrix m) against de-vigged in-play odds: 1X2, final total goals lines and both teams
    to score. Keeps outcomes worth at least 5 % more than their price, decided outcomes left out."""
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
                markets.setdefault((name, None), []).append((bet_label("1x2", sel, home, away), p, odd))
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
                    (goal_label("ou", sel, line), p_over if sel == "over" else 1 - p_over, odd))
            elif name == "Both Teams to Score" and val in ("Yes", "No"):
                sel = "yes" if val == "Yes" else "no"
                markets.setdefault((name, None), []).append(
                    (goal_label("btts", sel, None), p_btts if sel == "yes" else 1 - p_btts, odd))
    rows = []
    for (name, _), outs in markets.items():
        if len(outs) != (3 if name == "Fulltime Result" else 2):
            continue  # an outcome missing or suspended: no fair market price
        book = sum(1 / o for _, _, o in outs)
        for label, p, odd in outs:
            if not 0.001 < p < 0.999:
                continue
            pm, value = (1 / odd) / book, p * odd - 1
            if value < MIN_VALUE:
                continue
            gap = abs(p - pm)
            rows.append({"pari": label, "cote": odd, "proba": round(p, 4), "proba_marche": round(pm, 4),
                         "valeur": round(value, 4), "ecart": round(gap, 4),
                         "statut": "opportunite" if gap <= MAX_GAP else "ecart_trop_grand"})
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
            rows = live_odds_rows(m, odds, doc["domicile"], doc["exterieur"])
            direct["cotes_direct"], direct["cotes_note"] = rows, ODDS_NOTE
            for r in rows:
                if r["statut"] == "opportunite":
                    alerts.append(f"Cote en direct intéressante : {r['pari']} à {str(r['cote']).replace('.', ',')} (modèle "
                                  f"{r['proba']:.0%}, marché {r['proba_marche']:.0%}, valeur {r['valeur']:+.0%}).")
    else:
        direct["sans_modele"] = "Pas d'analyse d'avant-match pour ce match : score et statistiques seulement."
    doc["direct"] = direct
    return doc
