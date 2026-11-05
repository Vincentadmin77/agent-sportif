from sportai.services import explication_m as E

TM = {"joueur_1": "J-L. Struff", "joueur_2": "C. Ruud", "surface": "hard",
      "enquete": {"forme_j1": {"joues": 5, "victoires": 4}, "forme_j2": {"joues": 5, "victoires": 3},
                  "surface_j1": {"surface": "dur", "sur_la_surface": {"pourcentage": 57}},
                  "surface_j2": {"surface": "dur", "sur_la_surface": {"pourcentage": 59}},
                  "face_a_face": {"rencontres": 2, "victoires_j1": 1, "victoires_j2": 1}}}


def test_a_straight_tennis_win_says_why():
    c = {"pari": "C. Ruud gagne le match", "cote": 1.53, "proba_marche": 0.639, "proba": 0.614, "feu": "vert"}
    t = E.tennis(c, TM, {"pari": "C. Ruud handicap -0.5 jeux"})
    assert t.startswith("Pourquoi :") and "64 %" in t and "61 %" in t and "1,53" in t
    assert "C. Ruud a gagné 3 de ses 5 derniers matchs" in t
    assert "J-L. Struff est en forme (4 victoires sur 5)" in t
    assert "« C. Ruud handicap -0.5 jeux »" in t
    for banned in ("pari", "€", "Bet365", "Pinnacle"):
        assert banned not in t


def test_live_and_foot_and_basket():
    c = {"pari": "C. Ruud gagne le match", "cote": 1.5, "proba_marche": 0.63, "proba": 0.6, "feu": "vert",
         "score": "3-3"}
    assert "en direct, à 3-3" in E.tennis(c, TM, None, live=c)
    m = {"domicile": "A", "exterieur": "B", "contexte": {"series": {
        "domicile": {"matchs": 10, "victoires": 7, "defaites": 2, "plus_2_5": 8},
        "exterieur": {"matchs": 10, "victoires": 2, "defaites": 6, "plus_2_5": 3}}}}
    t = E.foot({"pari": "Victoire A", "cote": 1.6, "proba_marche": 0.61, "proba": 0.66, "feu": "vert"}, m)
    assert "A a gagné 7 de ses 10 derniers matchs" in t and "B a perdu 6" in t and "à domicile" in t
    b = E.basket({"pari": "X handicap -4.5 points", "cote": 1.6, "proba_marche": 0.6, "proba": 0.62},
                 {"domicile": "X", "exterieur": "Y", "points": {"ecart_prevu": 7.2, "total_prevu": 220}})
    assert "gagner d'environ 7 points" in b
    assert E.tennis({"pari": None}, TM) is None
