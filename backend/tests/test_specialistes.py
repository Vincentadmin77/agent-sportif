from datetime import datetime, timedelta, timezone

from sportai.services import specialistes as S


def _foot(rows):
    return {"domicile": "Lille", "exterieur": "Lens", "tous_marches": {"analyses": rows}}


def test_foot_takes_value_when_model_agrees_with_market():
    m = _foot([{"pari": "Plus de 2,5 buts", "cote": 1.70, "proba": 0.62, "proba_marche": 0.58},
               {"pari": "Victoire Lille", "cote": 1.60, "proba": 0.75, "proba_marche": 0.60}])
    c = S.foot_choice(m)
    assert c["pari"] == "Plus de 2,5 buts"  # Lille: model 15 pts above the market -> refused


def test_foot_passes_without_value():
    c = S.foot_choice(_foot([{"pari": "Plus de 2,5 buts", "cote": 1.60, "proba": 0.55, "proba_marche": 0.60}]))
    assert c.get("retenu") is False and "moins que le marché" in c["raison"]


def _lines(main, other_odds=1.90):
    rows = []
    for ln in (main - 1, main, main + 1):
        o = 1.90 if ln == main else other_odds
        rows += [{"marche": "total", "selection": "over", "ligne": ln, "cote": o, "proba": 0.5, "proba_marche": 0.5},
                 {"marche": "total", "selection": "under", "ligne": ln, "cote": 1.90 if ln == main else 1.60,
                  "proba": 0.5, "proba_marche": 0.5}]
    return rows


def test_basket_bets_the_main_total_when_projection_is_far_but_not_too_far():
    bm = {"competition": "NBA", "domicile": "A", "exterieur": "B", "points": {"total_prevu": 220.0, "ecart_prevu": 0.0},
          "cotes_cloture": {"lignes": _lines(226.5, 2.2)}}
    c = S.basket_choice(bm, "2026-12-15")
    assert c["selection"] == "under" and c["ligne"] == 226.5
    bm["points"]["total_prevu"] = 210.0  # 16.5 points away: probably an absence the ratings miss
    assert S.basket_choice(bm, "2026-12-15").get("retenu") is False


def test_tennis_blends_mostly_market_and_needs_value():
    mk = {("vainqueur", "Home"): [1.60, 1.62], ("vainqueur", "Away"): [2.30, 2.35]}
    c = {"key": ("vainqueur", "Home"), "p": 0.75, "fair": 0.59, "best": 1.75, "conf": 0.67}
    got = S.tennis_choice([c], 10, mk)
    assert got and 0.59 < got["conf"] < 0.62  # Elo only nudges the market
    assert S.tennis_choice([{**c, "best": 1.62}], 10, mk) is None  # no 3 % value at 1.62
    assert S.tennis_choice([c], 90, mk) is None  # back after 90 days


def test_record_freezes_and_keeps_late_matches_out_of_ratio():
    now = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)
    frozen = S.record(None, {"pari": "x", "cote_prise": 1.8}, now + timedelta(minutes=30), now, True)
    assert S.record(frozen, {"pari": "y"}, now + timedelta(minutes=30), now, True)["pari"] == "x"
    late = S.record(None, {"pari": "x", "cote_prise": 1.8, "resultat": "gagne"}, now - timedelta(hours=1), now, False)
    assert late["enregistre_apres_coup_envoi"] and S.ratio([late])["positions"] == 0


def test_foot_never_backs_the_lucky_team():
    m = _foot([{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.59}])
    assert S.foot_choice(m, (0.5, 0.0))["pari"] == "Victoire Lille"
    c = S.foot_choice(m, (3.0, 0.0))
    assert c.get("retenu") is False and "chance" in c["raison"]


def test_basket_passes_on_doubtful_key_player_and_uses_absences():
    bm = {"competition": "NBA", "domicile": "A", "exterieur": "B", "points": {"total_prevu": 226.5, "ecart_prevu": 0.0},
          "cotes_cloture": {"lignes": _lines(226.5, 2.2)}}
    adj = {"marge": 0.0, "total": -6.0, "domicile": {"incertains": [], "absents": ["X"]},
           "exterieur": {"incertains": [], "absents": []}}
    assert S.basket_choice(bm, "2026-12-15", adj)["selection"] == "under"
    adj["exterieur"]["incertains"] = ["Star (25 pts, 35 min)"]
    assert "incertain" in S.basket_choice(bm, "2026-12-15", adj)["raison"]


def test_basket_winner_axis_when_handicap_price_out_of_range():
    lines = [{"marche": "handicap", "selection": s, "ligne": -9.5, "cote": c, "proba": .5, "proba_marche": .5}
             for s, c in (("home", 1.90), ("away", 1.90))]
    lines += [{"marche": "handicap", "selection": "home", "ligne": -5.5, "cote": 1.45, "proba": .6, "proba_marche": .6},
              {"marche": "winner", "selection": "home", "ligne": None, "cote": 1.55, "proba": .7, "proba_marche": .64}]
    bm = {"competition": "NBA", "domicile": "A", "exterieur": "B", "points": {"total_prevu": None, "ecart_prevu": 14.0},
          "cotes_cloture": {"lignes": lines}}
    c = S.basket_choice(bm, "2026-12-15")
    assert c["marche"] == "handicap" and c["selection"] == "home"  # main line -9.5 at 1.90 is in range
    for r in lines[:2]:
        r["cote"] = 1.40
    lines[0]["cote"], lines[1]["cote"] = 1.45, 1.45
    assert S.basket_choice(bm, "2026-12-15")["marche"] == "winner"


def test_choices_not_kept_stay_out_of_the_ratio():
    rows = [{"pari": "a", "resultat": "gagne", "cote_prise": 1.8},
            {"pari": "b", "resultat": "perdu", "cote_prise": 1.8, "retenu": False}]
    assert S.ratio(rows)["positions"] == 1 and S.ratio_tous(rows)["positions"] == 2


def _xi(prefix):
    return [f"{prefix}{i}" for i in range(11)]


def _with_lineups(m, home_out=(), home_bench=(), home_xi=None):
    m["compositions"] = {"domicile": {"titulaires": home_xi or _xi("L"), "absents_notables": list(home_out),
                                      "remplacants_notables": list(home_bench)},
                         "exterieur": {"titulaires": _xi("R"), "absents_notables": [], "remplacants_notables": []}}
    return m


def test_key_players_are_regular_starters_ranked_by_goals_and_assists():
    def p(pid, name, lineups, apps, goals, rating):
        return {"player": {"id": pid, "name": name}, "statistics": [{"team": {"id": 1}, "games": {
            "lineups": lineups, "appearences": apps, "rating": str(rating)}, "goals": {"total": goals, "assists": 0}}]}
    players = [p(1, "Buteur", 10, 10, 8, 7.2), p(2, "Gardien", 10, 10, 0, 6.9), p(3, "Remplaçant", 2, 9, 5, 6.8),
               p(4, "Meneur", 9, 10, 3, 7.5), p(5, "Ailier", 8, 10, 2, 7.0), p(6, "Défenseur", 10, 10, 0, 6.6)]
    assert S.key_players(players, 1) == ["Buteur", "Meneur", "Ailier", "Gardien"]


def test_foot_never_backs_a_very_weakened_side():
    rows = [{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.60},
            {"pari": "Plus de 2,5 buts", "cote": 1.80, "proba": 0.57, "proba_marche": 0.56}]
    m = _with_lineups(_foot(rows), home_xi=_xi("L"))
    w = S.weakened(m, {"domicile": ["A", "B", "C", "L0"], "exterieur": []})  # 3 of the 4 key players not in the XI
    assert w["domicile"]["niveau"] == "tres_diminuee"
    c = S.foot_choice(m, None, w)
    assert c["pari"] == "Plus de 2,5 buts" and "très diminuée" in c["raison"]
    m = _with_lineups(_foot(rows), home_out=["A", "B", "C", "D"])  # 4 usual starters out of the squad
    assert S.weakened(m)["domicile"]["niveau"] == "tres_diminuee"


def test_foot_rotation_lowers_the_side_a_little():
    rows = [{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.60}]
    m = _with_lineups(_foot(rows), home_bench=["A", "B", "C"])
    w = S.weakened(m)
    assert w["domicile"]["niveau"] == "a_la_baisse"
    assert S.foot_choice(m, None, w)["chance"] == round(S.foot_choice(m)["chance"] - S.ROTATION_PENALTY, 4)
    assert S.weakened(_foot(rows)) is None  # line-ups not out yet: nothing changes
