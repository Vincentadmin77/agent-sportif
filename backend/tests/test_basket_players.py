from sportai.services.basket_players import label, match, parse_odds


def test_names_match_across_feeds():
    cands = {"1": "Robinson Duncan", "2": "M. Raynaud", "3": "Brown Jaylen", "4": "Brown Bruce"}
    assert match("Duncan Robinson", cands) == "1"
    assert match("Mark Raynaud", cands) == "2"
    assert match("Jaylen Brown", cands) == "3"
    assert match("Moses Brown", cands) is None


def test_parse_player_odds():
    books = [{"name": "Betano", "bets": [
        {"name": "Player Points and Assists", "values": [{"value": "Cade Cunningham - Over 32.5", "odd": "1.80"},
                                                        {"value": "Cade Cunningham - Under 32.5", "odd": "1.93"}]},
        {"name": "Player Runs", "values": [{"value": "Anthony Davis - Over 11.5", "odd": "1.98"}]},
        {"name": "Player Points Milestones", "values": [{"value": "OG Anunoby - 15", "odd": "1.60"}]}]}]
    q = parse_odds(books)
    assert q[("pts_ast", "Cade Cunningham", "over", 32.5)] == {"Betano": 1.8}
    assert ("reb", "Anthony Davis", "over", 11.5) in q
    assert q[("pts", "OG Anunoby", "over", 14.5)] == {"Betano": 1.6}
    assert label({"marche_cle": "joueur_reb", "joueur": "Anthony Davis", "selection": "over", "ligne": 11.5}) == \
        "Anthony Davis : plus de 11,5 rebonds"
