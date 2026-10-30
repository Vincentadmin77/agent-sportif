from sportai.connectors.the_odds_api import bookmaker_country, french_only


def test_the_site_shows_a_country_for_the_bookmaker_never_its_name():
    assert bookmaker_country("winamax_fr") == "france"
    assert bookmaker_country("Pinnacle") == "curacao"
    assert bookmaker_country("Bet365") == "royaume_uni"
    assert bookmaker_country("betfair_sb_uk") == "royaume_uni"
    assert bookmaker_country("Betano") == "malte"
    assert bookmaker_country("Tipico") == "autre"
    assert bookmaker_country(None) is None


def test_positions_are_on_every_bookmaker_by_default(monkeypatch):
    monkeypatch.delenv("FRENCH_ODDS_ONLY", raising=False)
    assert not french_only()
    monkeypatch.setenv("FRENCH_ODDS_ONLY", "1")
    assert french_only()
