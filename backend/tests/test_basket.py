"""NBA / WNBA in the daily passes (owner 2026-10-27): day attachment, the football rules, the feed's parsing.
No network: recorded API-Basketball samples in tests/fixtures (game of 2026-10-27, odds trimmed to two books)."""

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from sportai.connectors.api_basketball import LEAGUE_IDS, ApiBasketballProvider, season_for
from sportai.connectors.base import ProviderUnavailable
from sportai.engine.basketball.ratings import Prediction
from sportai.services import basket
from sportai.services.dashboard import COMPETITION_NAMES, doc_id
from sportai.services.prematch import journee
from sportai.services.pronostic import SELECTION_TITLE, build_pronostic

FIX = Path(__file__).parent / "fixtures"
PARIS = ZoneInfo("Europe/Paris")
GAMES = json.loads((FIX / "api_basketball_games.json").read_text())
ODDS = json.loads((FIX / "api_basketball_odds.json").read_text())


def _agent_pass():
    path = Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py"
    spec = importlib.util.spec_from_file_location("agent_pass_basket", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _paris(*a) -> datetime:
    return datetime(*a, tzinfo=PARIS)


# ---------------------------------------------------------------- the day

def test_basket_day_runs_to_four_in_the_morning():
    d = date(2026, 10, 27)
    assert journee(_paris(2026, 10, 28, 0, 30), "basket") == d
    assert journee(_paris(2026, 10, 28, 3, 59), "basket") == d
    assert journee(_paris(2026, 10, 28, 4, 0), "basket") == d
    assert journee(_paris(2026, 10, 28, 4, 1), "basket") == date(2026, 10, 28)
    assert journee(_paris(2026, 10, 27, 8, 0), "basket") == d
    assert journee(_paris(2026, 10, 27, 23, 30), "basket") == d


def test_football_day_unchanged():
    assert journee(_paris(2026, 10, 28, 1, 30)) == date(2026, 10, 27)
    assert journee(_paris(2026, 10, 28, 2, 30)) == date(2026, 10, 28)
    assert journee(_paris(2026, 10, 28, 3, 59)) == date(2026, 10, 28)
    ko = _paris(2026, 10, 28, 3, 0)
    assert doc_id(ko, "Boston Celtics", "Brooklyn Nets") == "2026-10-28-boston-celtics-brooklyn-nets"
    assert doc_id(ko, "Boston Celtics", "Brooklyn Nets", "basket") == "2026-10-27-boston-celtics-brooklyn-nets"


# ---------------------------------------------------------------- the feed

def test_wiring_and_seasons():
    ap = _agent_pass()
    assert {"NBA", "WNBA"} <= set(ap.COMPETITIONS) and "NBA" not in ap.FOOT_COMPETITIONS
    assert ap._split(["FR1", "NBA", "WNBA"]) == (["FR1"], ["NBA", "WNBA"])
    assert LEAGUE_IDS == {"NBA": 12, "WNBA": 13}
    assert COMPETITION_NAMES["NBA"] == "NBA" and COMPETITION_NAMES["WNBA"] == "WNBA"
    assert season_for("NBA", date(2026, 10, 27)) == "2026-2027"
    assert season_for("NBA", date(2027, 3, 1)) == "2026-2027"
    assert season_for("WNBA", date(2026, 10, 27)) == 2026


def _api(payload):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["x-apisports-key"] == "test-key"
        return httpx.Response(200, json=payload(request))

    return ApiBasketballProvider("test-key", transport=httpx.MockTransport(handler)), calls


def test_games_of_the_basketball_day_from_the_recorded_sample():
    api, calls = _api(lambda r: GAMES)
    games = api.games_on(["NBA"], date(2026, 10, 27))
    # 23:30Z on the 27th = 00:30 Paris on the 28th: day 27. The 00:00 and 01:30 Paris games of the 27th: day 26.
    assert [g["id"] for g in games] == [519585]
    assert {r.url.params["date"] for r in calls} == {"2026-10-27", "2026-10-28"}
    assert {r.url.params["season"] for r in calls} == {"2026-2027"}
    assert [g["id"] for g in api.games_on(["NBA"], date(2026, 10, 26))] == [519577, 519582]
    assert basket.doc_key(games[0]) == "2026-10-27-boston-celtics-brooklyn-nets"


def test_feed_refusal_is_a_provider_failure():
    api, _ = _api(lambda r: {"errors": {"requests": "limit reached"}, "response": []})
    with pytest.raises(ProviderUnavailable):
        api.games_on(["NBA"], date(2026, 10, 27))


def test_odds_parsing_of_the_recorded_sample():
    q = basket.parse_odds(ODDS["response"][0])
    assert q[("winner", "home", None)] == {"Pinnacle": 1.16, "Bet365": 1.15}
    assert q[("winner", "away", None)]["Pinnacle"] == 6.02
    # "Away -12.5" is the away side of home -12.5 (away +12.5): it pairs with "Home -12.5"
    assert q[("handicap", "home", -12.5)]["Pinnacle"] == 1.98
    assert q[("handicap", "away", -12.5)]["Pinnacle"] == 1.93
    assert all(k[2] is None or k[2] % 1 == 0.5 for k in q)  # whole lines (refunds) left out
    assert ("total", "over", 218.5) in q and ("team_total_home", "over", 116.5) in q
    assert not any(k[0] not in basket.MARKET_NAMES for k in q)  # 3-way result (48 min) not read
    assert basket.label("handicap", "away", -12.5, "Boston Celtics", "Brooklyn Nets") == \
        "Brooklyn Nets +12,5 points (handicap)"


def _pred(home=118.5, away=99.9) -> Prediction:
    return Prediction(home, away, sigma_margin=12.5, sigma_total=17.5, sigma_side=10.5)


def test_bets_follow_the_floor_and_the_validation():
    rows = basket.price(_pred(), basket.parse_odds(ODDS["response"][0]))
    win = {r["selection"]: r for r in rows if r["marche_cle"] == "winner"}
    assert abs(win["home"]["proba_marche"] + win["away"]["proba_marche"] - 1) < 1e-3
    assert win["home"]["cote"] == 1.16  # best odds over the books
    nba = basket.bets("NBA", rows, "Boston Celtics", "Brooklyn Nets")
    assert nba and all(b["cote"] >= 1.60 for b in nba)  # Boston at 1.16 never offered
    assert all(b["valide"] == (b["basket"]["marche"] == "winner") for b in nba)
    assert all(b.get("validation") == "non validé" for b in nba if not b["valide"])
    assert all(not b["valide"] for b in basket.bets("WNBA", rows, "A", "B"))  # no WNBA backtest


def test_build_doc_has_the_sport_and_no_provider_or_money_words():
    game = GAMES["response"][2]
    now = datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc)
    doc = basket.build_doc("NBA", game, _pred(), ODDS["response"][0], None, now)
    assert doc["sport"] == "basket" and doc["jour"] == "2026-10-27" and doc["statut"] == "a_venir"
    assert doc["passages"] == {"h3": now.isoformat()}
    text = json.dumps(doc, ensure_ascii=False).lower()
    for word in ("pinnacle", "bet365", "api-", "mise", "€", "bankroll", "joueur pro", "en direct"):
        assert word not in text, word
    assert doc["proba_vainqueur"]["domicile"] > 0.9


def test_settlement_overtime_included():
    assert basket.settle("winner", "home", None, 127, 121) == "gagne"
    assert basket.settle("handicap", "away", -12.5, 110, 100) == "gagne"  # away +12.5
    assert basket.settle("handicap", "home", -12.5, 113, 100) == "gagne"
    assert basket.settle("total", "over", 218.5, 110, 108) == "perdu"
    assert basket.settle("team_total_away", "under", 104.5, 120, 104) == "gagne"


def test_live_then_final_from_the_feed():
    aot = GAMES["response"][0]  # Cleveland - Minnesota, after overtime
    doc = {"sport": "basket", "domicile": "Cleveland Cavaliers", "exterieur": "Minnesota Timberwolves",
           "statut": "a_venir", "jour": "2026-10-26",
           "score_estime": {"score": "115 - 110", "domicile": 115, "exterieur": 110, "proba": 0.6}}
    now = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
    live = json.loads(json.dumps(aot))
    live["status"] = {"long": "Quarter 3", "short": "Q3", "timer": "5"}
    d = basket.follow(live, doc, now)
    assert d["statut"] == "en_direct" and d["direct"]["periode"] == "3e quart-temps"
    d = basket.follow(aot, d, now)
    hp, ap = aot["scores"]["home"]["total"], aot["scores"]["away"]["total"]
    assert d["statut"] == "termine" and d["score"] == f"{hp} - {ap}"
    assert d["score_quarts"][-1]["periode"] == "Prol."
    assert d["score_estime"]["resultat"] == ("reussi" if hp > ap else "rate")
    assert basket.follow(aot, d, now) is None  # finished: left alone


# ---------------------------------------------------------------- "Mon pronostic": the football rules

def _doc(key, ko, home="H", away="A"):
    return {"sport": "basket", "jour": "2026-10-27", "coup_envoi": ko.isoformat(), "competition": "NBA",
            "domicile": home, "exterieur": away, "statut": "a_venir"}


def _bet(pari, cote, proba, pm, valide=True, market="winner", sel="home", line=None):
    b = {"pari": pari, "marche": basket.MARKET_NAMES[market], "cote": cote, "proba": proba, "proba_marche": pm,
         "valeur": round(proba * cote - 1, 4), "valide": valide,
         "basket": {"marche": market, "selection": sel, "ligne": line}}
    return b if valide else {**b, "validation": "non validé"}


def _pronostic(page, now):
    legs = basket.legs(page, "2026-10-27")
    return build_pronostic(legs, page, None, now, "passage", date(2026, 10, 27))


def test_floor_one_bet_per_match_and_top_confiance_only_on_elevee_validated():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    ko = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    page = {}
    # a: very likely, but under 1.60: never retained
    page["a"] = {**_doc("a", ko), "paris_basket": [_bet("Victoire H", 1.55, 0.70, 0.66)]}
    # b: "non validé" total, élevée: retained on its match, never the Top confiance
    page["b"] = {**_doc("b", ko), "paris_basket": [
        _bet("Plus de 220,5 points dans le match", 1.70, 0.66, 0.62, False, "total", "over", 220.5)]}
    # c: validated winner, confidence ~0.62 (élevée): the Top confiance
    page["c"] = {**_doc("c", ko), "paris_basket": [_bet("Victoire H", 1.65, 0.64, 0.61)]}
    # d: validated winner, "moyenne": retained, not the Top confiance
    page["d"] = {**_doc("d", ko), "paris_basket": [_bet("Victoire A", 1.90, 0.56, 0.54, sel="away")]}
    doc = _pronostic(page, now)
    picks = doc["paris_retenus"]
    assert picks["a"]["pari"] is None
    assert picks["b"]["pari"] and picks["b"]["niveau"] == "Confiance élevée" and picks["b"]["sport"] == "basket"
    assert picks["d"]["niveau"] == "Confiance moyenne"
    assert [s["match_id"] for s in doc["selections"]] == ["c"] and doc["selections"][0]["titre"] == SELECTION_TITLE
    # combos: disjoint pairs away from the Top confiance; b + d not "retenu" (d is not élevée)
    assert [sorted(l["match_id"] for l in c["jambes"]) for c in doc["combines"]] == [["b", "d"]]
    assert not doc["combines"][0]["retenu"]
    assert '"bookmaker"' not in json.dumps(doc)  # no bookmaker named on the page

    # nothing élevée and validated: no Top confiance at all
    del page["c"]
    assert _pronostic(page, now)["selections"] == []


def test_retained_bet_settles_from_the_final_score():
    ko = datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)
    page = {"c": {**_doc("c", ko), "paris_basket": [_bet("Victoire H", 1.65, 0.64, 0.61)]}}
    early = _pronostic(page, ko - timedelta(hours=3))
    assert early["selections"][0]["match_id"] == "c" and not early["paris_retenus"]["c"]["fige"]
    first = build_pronostic(basket.legs(page, "2026-10-27"), page, early, ko - timedelta(minutes=40), "passage",
                            date(2026, 10, 27))  # fixed 45 min before tip-off
    assert first["paris_retenus"]["c"]["fige"] and first["selections"][0]["match_id"] == "c"
    page["c"] = {**page["c"], "statut": "termine", "score": "101 - 99"}
    later = build_pronostic(basket.legs(page, "2026-10-27"), page, first, ko + timedelta(hours=3), "resultats",
                            date(2026, 10, 27))
    assert later["paris_retenus"]["c"]["resultat"] == "gagne"
    assert later["selections"][0]["resultat"] == "gagne"


# ---------------------------------------------------------------- the plan

def test_plan_schedules_basketball_steps():
    ap = _agent_pass()
    now = datetime(2026, 10, 27, 6, 50, tzinfo=timezone.utc)
    tips = {"2026-10-27T23:30:00+00:00": {"NBA"}, "2026-10-28T02:00:00+00:00": {"NBA"}}
    foot = {"2026-10-27T19:45:00+00:00": {"IT1"}}
    out = ap.plan_steps(foot, tips, now)
    steps = {(s["at"], s["kind"]): s["competitions"] for s in out["steps"]}
    assert steps[("2026-10-27T20:30:00+00:00", "h3")] == ["NBA"]
    assert steps[("2026-10-27T22:45:00+00:00", "m45")] == ["NBA"]
    assert ("2026-10-27T23:15:00+00:00", "m15") not in steps  # no 15-min look for basketball
    assert steps[("2026-10-28T00:00:00+00:00", "live")] == ["NBA"]
    assert steps[("2026-10-28T04:30:00+00:00", "final")] == ["NBA"]  # 05:30 Paris
    assert steps[("2026-10-27T22:00:00+00:00", "final")] == ["IT1"]  # football unchanged
    assert steps[("2026-10-27T19:30:00+00:00", "m15")] == ["IT1"]
    assert ap._basket_final_days({"x": {"sport": "basket", "jour": "2026-10-27"}}, {},
                                 datetime(2026, 10, 28, 4, 30, tzinfo=timezone.utc)) == ["2026-10-27"]
