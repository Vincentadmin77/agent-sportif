from sportai.services.basket_compo import find_event, official_five, side_probable


def _p(pid, pos, starter=True, mins=30, ast=2, reb=5):
    return {"id": pid, "nom": f"N{pid}", "pos": pos, "starter": starter, "min": mins, "ast": ast, "reb": reb}


FIVE = [_p("1", "G", ast=8), _p("2", "G", ast=3), _p("3", "F", reb=4), _p("4", "F", reb=9), _p("5", "C", reb=11),
        _p("6", "G", starter=False, mins=24), _p("7", "F", starter=False, mins=10)]


def _inj(pid, status, pos="G", detail="Ankle"):
    return {"status": status, "athlete": {"id": pid, "displayName": f"N{pid}", "position": {"abbreviation": pos}},
            "details": {"type": detail}}


def test_usual_five_minus_the_injured_in_the_point_guard_to_centre_order():
    c = side_probable([FIVE, FIVE, FIVE], [_inj("1", "Out"), _inj("4", "Day-To-Day", "F"), _inj("7", "Out", "F")])
    assert c["titulaires"] == ["N2", "N6", "N3", "N4", "N5"]  # the injured point guard replaced, the doubtful kept
    assert c["absents"] == [{"joueur": "N1", "raison": "blessure", "poste": "meneur", "remplace_par": "N6"},
                            {"joueur": "N4", "raison": "incertain", "poste": "ailier fort"}]  # N7 does not count
    assert side_probable([FIVE], [_inj("2", "Out", detail="Suspension")])["absents"][0]["raison"] == "suspendu"
    assert side_probable([], []) is None


def test_official_five_and_event_matching():
    summary = {"boxscore": {"players": [{"team": {"id": "9"}, "statistics": [{"labels": ["MIN", "AST", "REB"], "athletes": [
        {"athlete": {"id": p["id"], "displayName": p["nom"], "position": {"abbreviation": p["pos"]}},
         "starter": p["starter"], "stats": [str(p["min"]), str(p["ast"]), str(p["reb"])]} for p in FIVE]}]}]}}
    assert official_five(summary, "9")["titulaires"] == ["N1", "N2", "N3", "N4", "N5"]
    assert official_five({}, "9") is None
    ev = {"competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "Portland Trail Blazers"}},
                                            {"homeAway": "away", "team": {"displayName": "Golden State Warriors"}}]}]}
    assert find_event([ev], "Portland Trail Blazers", "Golden State Warriors") is ev
