from sportai.services.position import position


def test_position_without_value_bet_gives_side_and_reason():
    doc = {"domicile": "Palmeiras", "exterieur": "Corinthians",
           "proba_1x2": {"domicile": 0.66, "nul": 0.23, "exterieur": 0.11},
           "rejets": ["Victoire Palmeiras à 1,54 : cote trop basse, avantage +1,8 %."]}
    p = position(doc)
    assert p["issue"] == "Victoire Palmeiras" and "double_chance" not in p
    assert p["texte"] == ("Notre position : Palmeiras gagne (66 %). Pas de mise conseillée : victoire Palmeiras "
                          "à 1,54 : cote trop basse, avantage +1,8 %.")


def test_position_close_match_and_market_fallback():
    p = position({"domicile": "Troyes", "exterieur": "Marseille",
                  "cotes_1x2": {"domicile": 3.25, "nul": 3.65, "exterieur": 1.98}})
    assert p["source"] == "marche" and p["issue"] == "Victoire Marseille"
    assert p["double_chance"]["pari"] == "Marseille ou nul"
    assert position({"domicile": "A", "exterieur": "B"}) is None
