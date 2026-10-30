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
    assert c["pari"] is None and "moins que le marché" in c["raison"]


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
    assert S.basket_choice(bm, "2026-12-15")["pari"] is None


def test_tennis_needs_agreement_and_no_long_layoff():
    c = {"key": ("vainqueur", "Home"), "p": 0.64, "fair": 0.60, "best": 1.62, "conf": 0.62}
    assert S.tennis_choice([c], 10) is c
    assert S.tennis_choice([c], 90) is None
    assert S.tennis_choice([{**c, "p": 0.75}], 10) is None


def test_record_freezes_and_keeps_late_matches_out_of_ratio():
    now = datetime(2026, 10, 31, 18, tzinfo=timezone.utc)
    frozen = S.record(None, {"pari": "x", "cote_prise": 1.8}, now + timedelta(minutes=30), now, True)
    assert S.record(frozen, {"pari": "y"}, now + timedelta(minutes=30), now, True)["pari"] == "x"
    late = S.record(None, {"pari": "x", "cote_prise": 1.8, "resultat": "gagne"}, now - timedelta(hours=1), now, False)
    assert late["enregistre_apres_coup_envoi"] and S.ratio([late])["positions"] == 0
