from sportai.services.pronostic import odd_even


def test_odd_even_positions_are_never_taken():
    assert odd_even({"pari": "Nombre de buts pair", "marche": "Buts pair ou impair"})
    assert odd_even({"pari": "1re mi-temps : nombre de buts impair", "marche": "Buts par mi-temps"})
    assert not odd_even({"pari": "Plus de 2,5 buts", "marche": "Nombre de buts"})
