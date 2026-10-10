"""Live follow-up of the results page's matches (API-Football, paid plan).

For each page match under way: minute, score, red cards, shots, possession and
expected goals when the feed has them, plus the in-play model's probabilities
(pre-match rates from the analysis database, scaled by the time left and
updated with in-play xG). Observations only: nothing here is a selection.
"""

from __future__ import annotations

from datetime import datetime

from sportai.connectors.base import LiveSnapshot
from sportai.connectors.teams import same_team
from sportai.engine.football.live import LiveState, final_score_matrix
from sportai.services.dashboard import settle

LIVE_NOTE = ("Suivi en direct : probabilités recalculées avec le score, la minute, les cartons rouges et les "
             "occasions (xG) quand le flux les donne. Information seulement, pas une sélection.")
STAT_NAMES = {"Total Shots": "tirs", "Shots on Goal": "tirs_cadres", "Ball Possession": "possession",
              "Corner Kicks": "corners", "expected_goals": "xg"}
FINISHED = {"FT", "AET", "PEN"}


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


def live_doc(doc: dict, fx: dict, snap: LiveSnapshot, stats: dict[str, list], pre: tuple | None, now: datetime) -> dict:
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
    else:
        direct["sans_modele"] = "Pas d'analyse d'avant-match pour ce match : score et statistiques seulement."
    doc["direct"] = direct
    return doc
