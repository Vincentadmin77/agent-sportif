from datetime import datetime, timezone

from sportai.connectors.base import LiveSnapshot
from sportai.services.live_page import find_doc, live_doc, pre_match_rates, stats_by_side

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
