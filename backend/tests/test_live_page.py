from datetime import datetime, timezone

import numpy as np

from sportai.connectors.base import LiveSnapshot
from sportai.services.live_page import find_doc, live_doc, live_odds_rows, pre_match_rates, stats_by_side

NOW = datetime(2026, 10, 10, 20, 25, tzinfo=timezone.utc)
PAGE = {"2026-10-10-lorient-paris": {"jour": "2026-10-10", "domicile": "Lorient", "exterieur": "Paris FC",
                                     "statut": "a_venir", "verdict": "aucune", "rejets": ["x"], "selections": []},
        "2026-10-10-brestois-angers": {"jour": "2026-10-10", "domicile": "Brest", "exterieur": "Angers",
                                       "statut": "en_direct", "selections": [{"pari": "Victoire Brest"}]}}
STATS = [{"team": {"id": 1}, "statistics": [{"type": "Total Shots", "value": 8}, {"type": "Ball Possession", "value": "42%"},
                                            {"type": "expected_goals", "value": "0.53"}]},
         {"team": {"id": 2}, "statistics": [{"type": "Total Shots", "value": 12}, {"type": "Ball Possession", "value": "58%"},
                                            {"type": "expected_goals", "value": "1.40"}]}]


def _fx(short, minute, hg, ag):
    return {"fixture": {"id": 9, "status": {"short": short, "elapsed": minute}}, "goals": {"home": hg, "away": ag},
            "teams": {"home": {"id": 1, "name": "Lorient"}, "away": {"id": 2, "name": "Paris FC"}}}


def _snap(minute, hg, ag, xg=(0.53, 1.40)):
    return LiveSnapshot("api-football", "9", minute, hg, ag, NOW, "real", home_xg=xg[0], away_xg=xg[1])


def test_api_football_names_find_the_page_match():
    assert find_doc(PAGE, "2026-10-10", "Stade Brestois 29", "Angers") == "2026-10-10-brestois-angers"
    assert find_doc(PAGE, "2026-10-10", "Lorient", "Paris FC") == "2026-10-10-lorient-paris"
    assert find_doc(PAGE, "2026-10-11", "Lorient", "Paris FC") is None


def test_stats_by_side():
    s = stats_by_side(STATS, 1)
    assert s == {"tirs": [8, 12], "possession": [42, 58], "xg": [0.53, 1.4]}


def test_live_probabilities_follow_the_score_and_keep_the_analysis():
    pre = pre_match_rates([{"match": "Lorient - Paris FC", "expected_goals": [1.4, 1.5],
                            "probabilities": {"1x2:home": 0.39, "1x2:draw": 0.2, "1x2:away": 0.41}}], "Lorient", "Paris FC")
    doc = live_doc(PAGE["2026-10-10-lorient-paris"], _fx("2H", 83, 1, 0), _snap(83, 1, 0), {}, pre, NOW)
    assert doc["statut"] == "en_direct" and doc["score"] == "1 - 0" and doc["minute"] == 83
    assert doc["rejets"] == ["x"] and doc["verdict"] == "aucune"
    p = doc["direct"]["proba"]
    assert p["domicile"] > 0.8 and abs(sum(p.values()) - 1) < 1e-3
    assert doc["direct"]["proba_avant"]["domicile"] == 0.39
    trailing = live_doc(PAGE["2026-10-10-lorient-paris"], _fx("2H", 60, 1, 0), _snap(60, 1, 0, (0.3, 1.2)), {}, pre, NOW)
    assert any("Paris FC domine" in a for a in trailing["direct"]["alertes"])


def test_finished_match_settles_selections_and_works_without_model():
    fx = {**_fx("FT", 90, 5, 1), "teams": {"home": {"id": 1, "name": "Brest"}, "away": {"id": 2, "name": "Angers"}}}
    doc = live_doc(PAGE["2026-10-10-brestois-angers"], fx, _snap(90, 5, 1), {}, None, NOW)
    assert doc["statut"] == "termine" and doc["selections"][0]["resultat"] == "gagne"
    assert "proba" not in doc["direct"] and doc["direct"]["sans_modele"]


def _bet(name, *values):
    return {"name": name, "values": [{"value": v, "handicap": h, "odd": o, "suspended": s} for v, h, o, s in values]}


def test_live_odds_flag_value_against_the_in_play_model():
    # Away side down to ten men while level: the model makes the home side a clear favourite.
    pre = ((1.3, 1.4), {"1x2:home": 0.36, "1x2:draw": 0.27, "1x2:away": 0.37})
    snap = LiveSnapshot("api-football", "9", 30, 0, 0, NOW, "real", away_red_cards=1)
    odds = [_bet("Fulltime Result", ("Home", None, "2.60", False), ("Draw", None, "3.10", False),
                 ("Away", None, "3.00", False)),
            _bet("Match Goals", ("Over", "2.5", "2.10", False), ("Under", "2.5", "1.70", False),
                 ("Over", "0.5", "1.05", True), ("Under", "0.5", "9.00", False)),
            _bet("Both Teams to Score", ("Yes", None, "1.90", False), ("No", None, "1.85", False))]
    doc = live_doc(PAGE["2026-10-10-lorient-paris"], _fx("1H", 30, 0, 0), snap, {}, pre, NOW, odds)
    d = doc["direct"]
    rows = {r["pari"]: r for r in d["cotes_direct"]}
    assert "Victoire Lorient" in rows and d["cotes_note"].startswith("Cotes en direct")
    win = rows["Victoire Lorient"]
    assert win["valeur"] >= 0.05 and abs(win["valeur"] - (win["proba"] * 2.6 - 1)) < 1e-3
    assert win["statut"] == ("opportunite" if win["ecart"] <= 0.10 else "ecart_trop_grand")
    assert all(r["valeur"] >= 0.05 for r in rows.values())
    assert not any("0,5" in p for p in rows)  # one side suspended: no fair price for that line
    n_opp = sum(r["statut"] == "opportunite" for r in rows.values())
    assert sum("Cote en direct" in a for a in d["alertes"]) == n_opp
    assert doc["selections"] == []


def test_live_odds_statuses_and_no_odds_keeps_old_behaviour():
    m = np.zeros((3, 3))
    m[1, 0], m[0, 0], m[0, 1] = 0.6, 0.25, 0.15
    odds = [_bet("Fulltime Result", ("Home", None, "1.90", False), ("Draw", None, "3.60", False),
                 ("Away", None, "6.00", False)),
            _bet("Match Goals", ("Over", "0.5", "1.10", False), ("Under", "0.5", "6.50", False)),
            _bet("Both Teams to Score", ("Yes", None, "3.00", False), ("No", None, "1.30", False))]
    rows = {r["pari"]: r for r in live_odds_rows(m, odds, "Côme", "Roma")}
    assert rows["Victoire Côme"]["statut"] == "opportunite"  # 0.6 * 1.9 - 1 = 0.14, market about 0.52
    assert rows["Moins de 0,5 buts"]["statut"] == "ecart_trop_grand"  # 0.25 * 6.5 - 1 = 0.63, market about 0.14
    assert "Match nul" not in rows  # 0.25 * 3.6 - 1 < 0.05
    assert not any(p.startswith("Les deux équipes") for p in rows)  # btts impossible here: decided, left out
    pre = ((1.3, 1.4), {})
    doc = live_doc(PAGE["2026-10-10-lorient-paris"], _fx("2H", 60, 1, 0), _snap(60, 1, 0), {}, pre, NOW)
    assert "cotes_direct" not in doc["direct"]
    done = live_doc(PAGE["2026-10-10-lorient-paris"], _fx("FT", 90, 1, 0), _snap(90, 1, 0), {}, pre, NOW, [odds[0]])
    assert "cotes_direct" not in done["direct"]


def test_live_odds_checked_against_the_match():
    # Como - Roma, 2026-10-11: Roma a player down, Como with the ball. The model's Roma win is not offered.
    m = np.zeros((3, 3))
    m[1, 0], m[0, 0], m[0, 1] = 0.25, 0.5, 0.25
    odds = [_bet("Fulltime Result", ("Home", None, "2.10", False), ("Draw", None, "2.10", False),
                 ("Away", None, "5.50", False))]
    rows = {r["pari"]: r for r in live_odds_rows(m, odds, "Côme", "Roma", 70, [0, 1],
                                                 {"possession": [58, 42], "tirs_cadres": [2, 2]})}
    assert rows["Victoire Roma"]["statut"] == "ecarte" and "infériorité" in rows["Victoire Roma"]["raison"]
    rows = {r["pari"]: r for r in live_odds_rows(m, odds, "Côme", "Roma", 70, [0, 0],
                                                 {"possession": [62, 38]})}
    assert rows["Victoire Roma"]["statut"] == "ecarte" and "possession" in rows["Victoire Roma"]["raison"]
    late = live_odds_rows(m, odds, "Côme", "Roma", 85, [0, 0], {})
    assert all(r["statut"] != "opportunite" for r in late)
    calm = {r["pari"]: r for r in live_odds_rows(m, odds, "Côme", "Roma", 70, [0, 0], {})}
    assert calm["Victoire Roma"]["statut"] == "opportunite"


def test_fixture_identity_gives_crests_and_round():
    from sportai.services.live_page import fixture_identity
    fx = {"teams": {"home": {"id": 85, "logo": "https://media.api-sports.io/football/teams/85.png"},
                    "away": {"id": 91, "logo": "https://media.api-sports.io/football/teams/91.png"}},
          "league": {"logo": "https://media.api-sports.io/football/leagues/61.png", "round": "Regular Season - 8"}}
    assert fixture_identity(fx) == {"id_domicile": 85, "id_exterieur": 91,
                                    "logo_domicile": "https://media.api-sports.io/football/teams/85.png",
                                    "logo_exterieur": "https://media.api-sports.io/football/teams/91.png",
                                    "logo_competition": "https://media.api-sports.io/football/leagues/61.png",
                                    "journee": 8}


def test_recent_form_and_head_to_head():
    from sportai.services.live_page import head_to_head_rows, recent_form

    def fx(day, h, a, hg, ag, hid=1, aid=2):
        return {"fixture": {"date": f"{day}T18:00:00+00:00"}, "league": {"name": "Ligue 1"},
                "teams": {"home": {"id": hid, "name": h}, "away": {"id": aid, "name": a}},
                "goals": {"home": hg, "away": ag}}
    games = [fx("2026-10-04", "Rennes", "Lyon", 2, 1), fx("2026-09-27", "Nice", "Rennes", 0, 0, 3, 1),
             fx("2026-10-11", "Rennes", "Lens", None, None)]
    assert recent_form(games, 1) == [
        {"resultat": "N", "score": "0-0", "adversaire": "Nice", "domicile": False, "date": "2026-09-27",
         "competition": "Ligue 1"},
        {"resultat": "V", "score": "2-1", "adversaire": "Lyon", "domicile": True, "date": "2026-10-04",
         "competition": "Ligue 1"}]
    assert [r["date"] for r in head_to_head_rows(games)] == ["2026-10-04", "2026-09-27"]
