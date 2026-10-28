from datetime import datetime, timedelta, timezone

from sportai.connectors.base import OddsQuote
from sportai.services import basket
from sportai.services.prematch import _best_prices

NOW = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def _q(book, odds, market="1x2", sel="home", line=None):
    return OddsQuote(provider="x", bookmaker=book, provider_event_id="e", home="A", away="B", kickoff=NOW + timedelta(hours=2),
                     market=market, selection=sel, line=line, odds=odds, observed_at=NOW, mode="real",
                     expires_at=NOW + timedelta(minutes=15), validation_status="valid")


def test_best_price_only_on_french_bookmakers(monkeypatch):
    quotes = [_q("Bet365", 1.70), _q("winamax_fr", 1.64), _q("betclic_fr", 1.65), _q("Bet365", 1.62, "cards", "under", 4.5)]
    monkeypatch.setenv("FRENCH_ODDS_ONLY", "1")
    best = _best_prices(quotes, NOW)
    assert [(k.market, b.bookmaker, b.odds) for k, b in best.items()] == [("1x2", "betclic_fr", 1.65)]
    monkeypatch.setenv("FRENCH_ODDS_ONLY", "0")
    assert {b.odds for b in _best_prices(quotes, NOW).values()} == {1.70, 1.62}


def test_basket_french_books_added_and_priced(monkeypatch):
    game = {"teams": {"home": {"name": "Atlanta Hawks"}, "away": {"name": "Miami Heat"}},
            "date": "2026-10-28T23:10:00+00:00", "timestamp": int(datetime(2026, 10, 28, 23, 10, tzinfo=timezone.utc).timestamp())}
    ev = {"home_team": "Atlanta Hawks", "away_team": "Miami Heat", "commence_time": "2026-10-28T23:10:00Z",
          "bookmakers": [{"key": "winamax_fr", "markets": [
              {"key": "h2h", "outcomes": [{"name": "Atlanta Hawks", "price": 1.70}, {"name": "Miami Heat", "price": 2.05}]},
              {"key": "spreads", "outcomes": [{"name": "Atlanta Hawks", "price": 1.9, "point": -3.5},
                                              {"name": "Miami Heat", "price": 1.9, "point": 3.5}]},
              {"key": "totals", "outcomes": [{"name": "Over", "price": 1.88, "point": 228.5},
                                             {"name": "Under", "price": 1.92, "point": 228.5}]}]},
              {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [{"name": "Atlanta Hawks", "price": 9.0}]}]}]}
    entry = {"bookmakers": [{"name": "Bet365", "bets": [{"name": "Home/Away", "values": [
        {"value": "Home", "odd": "1.75"}, {"value": "Away", "odd": "2.10"}]}]}]}
    out = basket.with_french_books(entry, game, [ev])
    q = basket.parse_odds(out)
    assert q[("winner", "home", None)] == {"Bet365": 1.75, "winamax_fr": 1.70}
    assert q[("handicap", "home", -3.5)] == {"winamax_fr": 1.9} and q[("handicap", "away", -3.5)] == {"winamax_fr": 1.9}
    assert q[("total", "over", 228.5)] == {"winamax_fr": 1.88}

    class P:
        def p_home_win(self):
            return 0.62
    monkeypatch.setenv("FRENCH_ODDS_ONLY", "1")
    rows = basket.price(P(), {k: v for k, v in q.items() if k[0] == "winner"})
    home = next(r for r in rows if r["selection"] == "home")
    assert home["cote"] == 1.70 and home["n_bookmakers"] == 1  # the French price, not Bet365's 1.75
    assert basket.price(P(), {("winner", "home", None): {"Bet365": 1.75}, ("winner", "away", None): {"Bet365": 2.1}}) == []
