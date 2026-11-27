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
    # adapt rather than drop: its double chance stays possible, 3 pts lower
    dc = [{"pari": "Lille (remboursé si nul)", "cote": 1.55, "proba": 0.80, "proba_marche": 0.78}]
    m2 = _with_lineups(_foot(dc), home_xi=_xi("L"))
    opts = S.foot_options(m2, None, S.weakened(m2, {"domicile": ["A", "B", "C", "L0"], "exterieur": []}))
    assert opts[0]["chance"] == round(S.foot_options(m2)[0]["chance"] - S.ROTATION_PENALTY, 4)
    m = _with_lineups(_foot(rows), home_out=["A", "B", "C", "D"])  # 4 usual starters out of the squad
    assert S.weakened(m)["domicile"]["niveau"] == "tres_diminuee"


def test_foot_rotation_alone_no_longer_lowers_the_side():
    rows = [{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.60}]
    m = _with_lineups(_foot(rows), home_bench=["A", "B", "C"])
    w = S.weakened(m)
    assert w["domicile"]["niveau"] == "a_la_baisse"
    c = S.foot_choice(m, None, w)
    assert c["chance"] == S.foot_choice(m)["chance"] and "choix du coach" in c["raison"]


def test_favourite_with_its_usual_eleven_before_the_champions_league_is_lowered():
    rows = [{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.60},
            {"pari": "Lille (remboursé si nul)", "cote": 1.55, "proba": 0.80, "proba_marche": 0.78}]
    m = _with_lineups(_foot(rows))
    m["proba_1x2"] = {"domicile": 0.6, "nul": 0.22, "exterieur": 0.18}
    w = S.weakened(m)
    base = {o["pari"]: o["chance"] for o in S.foot_options(m, None, w)}
    moved = {o["pari"]: o["chance"] for o in S.foot_options(m, None, w, {"domicile": 3})}
    assert all(moved[k] == round(base[k] - S.UCL_PENALTY, 4) for k in base)
    assert "avant la Ligue des champions" in S.foot_choice(m, None, w, {"domicile": 3})["raison"]
    assert S.foot_options(m, None, w, {"exterieur": 3}) == S.foot_options(m, None, w)  # not the favourite
    rot = _with_lineups(_foot(rows), home_bench=["A", "B", "C"])  # rotating before the UCL: no penalty
    rot["proba_1x2"] = m["proba_1x2"]
    wr = S.weakened(rot)
    assert S.foot_options(rot, None, wr, {"domicile": 3}) == S.foot_options(rot, None, wr)
    assert S.foot_options(m, None, None, {"domicile": 3}) == S.foot_options(m)  # line-ups not out: nothing
    assert S.weakened(_foot(rows)) is None  # line-ups not out yet: nothing changes


def test_goals_lean_under_when_both_attacks_are_missing_and_over_without_the_keeper():
    rows = [{"pari": "Plus de 2,5 buts", "cote": 1.80, "proba": 0.58, "proba_marche": 0.56},
            {"pari": "Moins de 2,5 buts", "cote": 1.90, "proba": 0.44, "proba_marche": 0.44}]
    m = _with_lineups(_foot(rows))
    m["compositions"]["exterieur"]["titulaires"] = _xi("R")
    roles = {"domicile": {"att": ["A1", "A2"], "gk": ["L0"]}, "exterieur": {"att": ["B1", "B2"], "gk": ["R0"]}}
    w = S.weakened(m, None, roles)  # A1, A2, B1, B2 not in the XIs; both keepers play
    base = {o["pari"]: o["chance"] for o in S.foot_options(m)}
    moved = {o["pari"]: o["chance"] for o in S.foot_options(m, None, w)}
    assert moved["Plus de 2,5 buts"] == round(base["Plus de 2,5 buts"] - S.GOALS_SHIFT, 4)
    assert moved["Moins de 2,5 buts"] == round(base["Moins de 2,5 buts"] + S.GOALS_SHIFT, 4)
    roles["domicile"] = {"att": [], "gk": ["G"]}  # home keeper missing, home attack complete
    w = S.weakened(m, None, roles)
    moved = {o["pari"]: o["chance"] for o in S.foot_options(m, None, w)}
    assert moved["Plus de 2,5 buts"] == round(base["Plus de 2,5 buts"] + S.GOALS_SHIFT, 4)
    assert "gardien habituel absent" in S.foot_choice(m, None, w)["raison"].lower()


def test_balance_and_keeper_on_every_market():
    rows = [{"pari": "Victoire Lens", "cote": 2.10, "proba": 0.45, "proba_marche": 0.44},
            {"pari": "Les deux équipes marquent : oui", "cote": 1.80, "proba": 0.55, "proba_marche": 0.54},
            {"pari": "Plus de 1,5 buts", "cote": 1.50, "proba": 0.70, "proba_marche": 0.69}]
    m = _with_lineups(_foot(rows), home_out=["A", "B", "C", "D"])  # Lille very weakened
    base = {o["pari"]: o["chance"] for o in S.foot_options(m)}
    w = S.weakened(m, None, {"domicile": {"att": [], "gk": ["G"]}, "exterieur": {"att": [], "gk": ["R0"]}})
    moved = {o["pari"]: o["chance"] for o in S.foot_options(m, None, w)}
    assert moved["Victoire Lens"] == round(base["Victoire Lens"] + S.GOALS_SHIFT, 4)  # the balance
    assert moved["Les deux équipes marquent : oui"] == round(base["Les deux équipes marquent : oui"] + S.GOALS_SHIFT, 4)
    assert moved["Plus de 1,5 buts"] == base["Plus de 1,5 buts"]  # only the 2.5 and 3.5 lines move


def test_weakened_names_the_post_of_each_missing_player_for_display():
    def p(pid, name, pos, lineups):
        return {"player": {"id": pid, "name": name}, "statistics": [{"team": {"id": 1}, "games": {
            "lineups": lineups, "appearences": 10, "position": pos}, "goals": {}}]}
    roles = {"domicile": S.regular_roles([p(1, "A", "Attacker", 10), p(2, "B", "Defender", 10),
                                          p(3, "C", "Midfielder", 9), p(4, "G", "Goalkeeper", 10)], 1)}
    m = _with_lineups(_foot([{"pari": "Victoire Lille", "cote": 1.70, "proba": 0.62, "proba_marche": 0.60}]),
                      home_out=["B"], home_bench=["C"])
    w = S.weakened(m, {"domicile": ["A"], "exterieur": []}, roles)
    assert w["domicile"]["postes"] == {"A": "attaque", "B": "défense", "C": "milieu"}
    assert w["exterieur"]["postes"] == {}
    base = S.foot_options(m)
    assert [o["chance"] for o in S.foot_options(m, None, S.weakened(m, None, None))] == [o["chance"] for o in base]
