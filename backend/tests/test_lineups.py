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
