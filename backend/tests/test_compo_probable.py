from sportai.services.compo_probable import recent_lineups, side_probable


def _lu(formation, players, ts, team=1):
    xi = [{"player": {"id": pid, "name": f"P{pid}", "pos": pos, "grid": grid}} for pid, pos, grid in players]
    return {"fixture": {"timestamp": ts, "status": {"short": "FT"}},
            "lineups": [{"team": {"id": team}, "formation": formation, "startXI": xi, "substitutes": []}]}


BASE = [(1, "G", "1:1"), (2, "D", "2:4"), (3, "D", "2:3"), (4, "D", "2:2"), (5, "D", "2:1"),
        (6, "M", "3:3"), (7, "M", "3:2"), (8, "M", "3:1"), (9, "F", "4:3"), (10, "F", "4:2"), (11, "F", "4:1")]


def test_usual_line_up_minus_the_injured_with_the_most_played_formation():
    other = [p if p[0] != 9 else (12, "F", "4:3") for p in BASE]
    fx = [_lu("4-3-3", BASE, 3), _lu("4-3-3", other, 2), _lu("3-5-2", BASE, 1),
          {"fixture": {"timestamp": 4, "status": {"short": "NS"}}, "lineups": []}]
    past = recent_lineups(fx, 1)
    assert len(past) == 3
    absents = [{"joueur": "P9", "id": 9, "raison": "blessure"}, {"joueur": "P7", "id": 7, "raison": "incertain"},
               {"joueur": "Réserviste", "id": 99, "raison": "blessure"}]
    c = side_probable(past, absents, ratings={9: 7.3, 12: 6.7, 7: 7.0})
    assert c["formation"] == "4-3-3" and c["source"] == "habituelle"
    assert c["titulaires"][0] == "P1" and "P9" not in c["titulaires"] and "P12" in c["titulaires"]
    assert "P7" in c["titulaires"]  # doubtful: still expected to start
    assert c["absents"] == [{"joueur": "P9", "raison": "blessure", "poste": "attaque", "remplace_par": "P12",
                             "note": 7.3, "note_remplacant": 6.7, "gravite": "forte"},
                            {"joueur": "P7", "raison": "incertain", "poste": "milieu"}]  # a non-starter is not listed


def test_no_line_up_when_eleven_players_cannot_be_named():
    past = recent_lineups([_lu("4-3-3", BASE, 1)], 1)
    assert side_probable(past, [{"joueur": "P2", "id": 2, "raison": "suspendu"}]) is None
    assert side_probable([], []) is None


def test_official_absents_name_the_benched_and_the_injured_with_their_replacement():
    from sportai.services.compo_probable import official_absents
    past = recent_lineups([_lu("4-3-3", BASE, 2), _lu("4-3-3", BASE, 1)], 1)
    today = [p for p in BASE if p[0] not in (5, 10)] + [(20, "D", "2:1"), (21, "F", "4:2")]
    team = {"startXI": [{"player": {"id": pid, "name": f"P{pid}", "pos": pos, "grid": g}} for pid, pos, g in today],
            "substitutes": [{"player": {"id": 10, "name": "P10", "pos": "F"}}]}
    out = official_absents(team, past, [{"joueur": "P5", "raison": "suspendu"}], {5: 6.9, 20: 6.8, 10: 7.0})
    assert out == [{"joueur": "P5", "raison": "suspendu", "poste": "défense", "remplace_par": "P20", "note": 6.9,
                    "note_remplacant": 6.8, "gravite": "moyenne"},
                   {"joueur": "P10", "raison": "banc", "poste": "attaque", "remplace_par": "P21", "note": 7.0,
                    "note_remplacant": None, "gravite": "forte"}]
