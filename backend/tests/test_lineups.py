from sportai.services.lineups import lineup_block, merge_players, scoring_rate


def _item(pid, name, pos, minutes, goals, season):
    return {"player": {"id": pid, "name": name},
            "statistics": [{"games": {"minutes": minutes, "position": pos}, "goals": {"total": goals}, "league": {"season": season}}]}


PAGES = [[_item(1, "Buteur", "Attacker", 600, 5, 2026), _item(2, "Milieu", "Midfielder", 630, 0, 2026),
          _item(3, "Blessé", "Attacker", 620, 4, 2026)],
         [_item(1, "Buteur", "Attacker", 2700, 15, 2025)]]
XI = [{"player": {"id": 1, "name": "Buteur", "pos": "F"}}, {"player": {"id": 2, "name": "Milieu", "pos": "M"}},
      {"player": {"id": 9, "name": "Nouveau", "pos": "D"}}]


def test_records_merge_seasons_and_rates_are_shrunk():
    recs = merge_players(PAGES, 2026)
    assert recs[1].minutes == 3300 and recs[1].goals == 20 and recs[1].minutes_this_season == 600
    assert 0.33 < scoring_rate(recs[1], "F") < 20 / (3300 / 90)  # pulled towards the striker prior
    assert scoring_rate(None, "D") == 0.04  # unknown player: position prior


def test_lineup_block_lists_absentees_and_shares_team_goals():
    block = lineup_block({"formation": "4-3-3", "startXI": XI, "substitutes": []}, merge_players(PAGES, 2026), 1.5)
    assert block["absents_notables"] == ["Blessé"]
    scorers = block["buteurs"]
    assert scorers[0]["joueur"] == "Buteur" and all(0 < s["proba"] < 1 for s in scorers)
    # P(at least one) from the starters' share of 1.5 expected goals
    import math
    total = sum(-math.log(1 - s["proba"]) for s in scorers)
    assert abs(total - 1.5 * 0.85) < 0.01


from sportai.services.lineups import (LINEUP_WAIT, absence_reason, absentees, fill_compositions, regular_starters,
                                      scenario_with_squads)

FX = {"fixture": {"id": 77}, "league": {"id": 135, "season": 2026},
      "teams": {"home": {"id": 10, "name": "Juventus"}, "away": {"id": 20, "name": "Inter"}}}


def _season(pid, name, apps, starts, team=10, league=135):
    return {"player": {"id": pid, "name": name},
            "statistics": [{"team": {"id": team}, "league": {"id": league, "season": 2026},
                            "games": {"appearences": apps, "lineups": starts, "minutes": starts * 90, "position": "Attacker"},
                            "goals": {"total": 0}}]}


def test_regular_starters_need_half_of_the_league_matches():
    players = [_season(1, "Gardien", 8, 8), _season(2, "Vlahović", 6, 4), _season(3, "Remplaçant", 6, 3),
               _season(4, "Jeune", 2, 1), _season(5, "Coupe", 9, 9, league=137)]  # cup matches do not count
    assert regular_starters(players, 10, 135) == {1, 2}
    assert regular_starters([], 10, 135) == set()


def test_absentees_with_reasons_and_regulars_first():
    injuries = [{"team": {"id": 10}, "player": {"id": 7, "name": "Remplaçant", "type": "Missing Fixture", "reason": "Knee Injury"}},
                {"team": {"id": 10}, "player": {"id": 2, "name": "Vlahović", "type": "Missing Fixture", "reason": "Muscle Injury"}},
                {"team": {"id": 10}, "player": {"id": 2, "name": "Vlahović", "type": "Missing Fixture", "reason": "Muscle Injury"}},
                {"team": {"id": 20}, "player": {"id": 9, "name": "Barella", "type": "Missing Fixture", "reason": "Suspended"}},
                {"team": {"id": 20}, "player": {"id": 8, "name": "Doute", "type": "Questionable", "reason": "Knock"}},
                {"team": {"id": 99}, "player": {"id": 5, "name": "Autre", "type": "Missing Fixture", "reason": "Injury"}}]
    out = absentees(injuries, FX, {10: {2}, 20: {9}})
    assert [(a["joueur"], a["raison"], a["titulaire_habituel"]) for a in out["domicile"]] == [
        ("Vlahović", "blessure", True), ("Remplaçant", "blessure", False)]
    assert [(a["joueur"], a["raison"]) for a in out["exterieur"]] == [("Barella", "suspendu"), ("Doute", "incertain")]
    assert absence_reason({"type": "Missing Fixture", "reason": "Something new"}) == "absent"  # never guessed


def test_fill_compositions_waits_then_fills_without_player_requests_before():
    calls = []
    pages_for = lambda tid, s: calls.append((tid, s)) or []
    doc = {"domicile": "Juventus", "exterieur": "Inter"}
    assert not fill_compositions(doc, FX, [], pages_for, None)
    assert doc["compositions"] == {"attente": LINEUP_WAIT} and calls == []
    lineups = [{"team": {"id": t}, "formation": "3-5-2",
                "startXI": [{"player": {"id": t * 100 + i, "name": f"J{t}-{i}", "pos": "M"}} for i in range(11)],
                "substitutes": []} for t in (10, 20)]
    assert fill_compositions(doc, FX, lineups, pages_for, (1.4, 1.1))
    assert len(doc["compositions"]["domicile"]["titulaires"]) == 11
    assert sorted(calls) == [(10, 2025), (10, 2026), (20, 2025), (20, 2026)]
    assert doc["buteurs"]["joueurs"]


def test_scenario_appends_absentees_and_xi_once():
    doc = {"domicile": "Juventus", "exterieur": "Inter",
           "scenario": "Buts attendus : Juventus 1,4. Estimation du modèle, pas un pari.",
           "absents": {"jour": "2026-10-11", "domicile": [
               {"joueur": "D. Vlahović", "raison": "blessure", "titulaire_habituel": True},
               {"joueur": "Bremer", "raison": "suspendu", "titulaire_habituel": True}], "exterieur": []},
           "compositions": {"attente": LINEUP_WAIT}}
    text = scenario_with_squads(doc)
    assert text.endswith("pas un pari. Absents chez Juventus : D. Vlahović (blessure), Bremer (suspendu), "
                         "dont 2 titulaires habituels.")
    assert "Onze de départ" not in text
    doc["scenario"] = text
    doc["compositions"] = {"domicile": {"formation": "3-5-2", "titulaires": ["A", "B"]},
                           "exterieur": {"formation": None, "titulaires": ["C"]}}
    text = scenario_with_squads(doc)
    assert text.count("Absents chez") == 1
    assert text.endswith("Onze de départ publié. Juventus (3-5-2) : A, B ; Inter : C.")
    doc["scenario"] = text
    assert scenario_with_squads(doc) == text  # rebuilt, never repeated
    assert scenario_with_squads({"domicile": "A", "exterieur": "B"}) is None
