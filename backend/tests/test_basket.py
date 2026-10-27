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
    assert basket.doc_key(games[0]) == "519585"  # basket_matchs/<game id>


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


MATCH_KEYS = {"sport", "competition", "code_ligue", "jour", "coup_envoi", "domicile", "exterieur", "statut", "score",
              "score_estime", "proba_1x2", "cotes_1x2", "points", "contexte", "passages"}
PICK_KEYS = {"pari", "marche", "match", "match_id", "competition", "coup_envoi", "cote", "proba", "proba_marche",
             "confiance", "niveau", "valide", "fige"}
FORBIDDEN = ("pinnacle", "bet365", "api-", "mise", "€", "bankroll", "joueur pro", "bookmaker")


def _no_forbidden_words(doc):
    text = json.dumps(doc, ensure_ascii=False).lower()
    for word in FORBIDDEN:
        assert word not in text, word


def test_build_doc_follows_the_page_format():
    game = GAMES["response"][2]
    now = datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc)
    doc, offers = basket.build_doc("NBA", game, _pred(), ODDS["response"][0], None, now)
    assert MATCH_KEYS <= doc.keys()
    assert doc["sport"] == "basket" and doc["code_ligue"] == "NBA" and doc["competition"] == "NBA"
    assert doc["jour"] == "2026-10-27" and doc["statut"] == "a_venir" and doc["score"] is None
    assert doc["coup_envoi"] == "2026-10-27T23:30:00Z"
    assert doc["passages"] == {"h3": now.isoformat(), "analyse": now.isoformat()}
    assert doc["score_estime"] == {"domicile": 118, "exterieur": 100}  # 118.5 rounds half to even
    assert doc["proba_1x2"]["domicile"] > 0.9
    assert abs(doc["proba_1x2"]["domicile"] + doc["proba_1x2"]["exterieur"] - 1) < 1e-3
    assert doc["cotes_1x2"] == {"domicile": 1.15, "exterieur": 5.88}  # median of the books, not the best
    assert doc["points"] == {"total_prevu": 218.4, "ecart_prevu": 18.6}
    assert doc["contexte"]["phrases"] and all(isinstance(p, str) for p in doc["contexte"]["phrases"])
    assert "q1" not in doc  # Q1 is not priced
    assert all(b["cote"] >= 1.60 for b in offers)
    _no_forbidden_words(doc)


def test_settlement_overtime_included():
    assert basket.settle("winner", "home", None, 127, 121) == "gagne"
    assert basket.settle("handicap", "away", -12.5, 110, 100) == "gagne"  # away +12.5
    assert basket.settle("handicap", "home", -12.5, 113, 100) == "gagne"
    assert basket.settle("total", "over", 218.5, 110, 108) == "perdu"
    assert basket.settle("team_total_away", "under", 104.5, 120, 104) == "gagne"


def test_live_then_final_from_the_feed():
    aot = GAMES["response"][0]  # Cleveland - Minnesota, after overtime
    doc = {"sport": "basket", "domicile": "Cleveland Cavaliers", "exterieur": "Minnesota Timberwolves",
           "statut": "a_venir", "score": None, "jour": "2026-10-26", "score_estime": {"domicile": 115, "exterieur": 110}}
    now = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
    live = json.loads(json.dumps(aot))
    live["status"] = {"long": "Quarter 3", "short": "Q3", "timer": "5"}
    d = basket.follow(live, doc, now)
    assert d["statut"] == "en_direct" and d["direct"]["periode"] == "3e quart-temps"
    d = basket.follow(aot, d, now)
    assert d["statut"] == "termine" and d["score"] == "127-125"
    assert d["score_quarts"][-1]["periode"] == "Prol."
    assert d["score_estime"]["resultat"] == "reussi"
    assert basket.follow(aot, d, now) is None  # finished: left alone


# ---------------------------------------------------------------- basket_pronostic: the football rules

def _mdoc(ko, home="H", away="A", **kw):
    return {"sport": "basket", "jour": "2026-10-27", "coup_envoi": ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "competition": "NBA", "code_ligue": "NBA", "domicile": home, "exterieur": away, "statut": "a_venir",
            "score": None, **kw}


def _bet(pari, cote, proba, pm, valide=True, market="winner", sel="home", line=None):
    b = {"pari": pari, "marche": basket.MARKET_NAMES[market], "cote": cote, "proba": proba, "proba_marche": pm,
         "valeur": round(proba * cote - 1, 4), "valide": valide,
         "basket": {"marche": market, "selection": sel, "ligne": line}}
    return b if valide else {**b, "validation": "non validé"}


def test_floor_one_bet_per_match_and_top_confiance_only_on_elevee_validated():
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    ko = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    matchs = {k: _mdoc(ko) for k in "abcd"}
    offers = {
        "a": [_bet("Victoire H", 1.55, 0.70, 0.66)],  # under 1.60: never retained
        "b": [_bet("Plus de 220,5 points dans le match", 1.70, 0.66, 0.62, False, "total", "over", 220.5)],
        "c": [_bet("Victoire H", 1.65, 0.64, 0.61), _bet("Victoire A", 2.6, 0.36, 0.39, sel="away")],
        "d": [_bet("Victoire A", 1.90, 0.56, 0.54, sel="away")],
    }
    doc = basket.build_pronostic("2026-10-27", matchs, offers, None, now, "matin")
    assert doc["jour"] == "2026-10-27" and doc["sport"] == "basket" and doc["passage"] == "matin"
    assert doc["combines"] == []
    picks = doc["paris_retenus"]
    assert picks["a"]["pari"] is None and picks["a"]["raison"]
    assert PICK_KEYS <= picks["c"].keys() and picks["c"]["pari"] == "Victoire H"  # one bet: the most likely
    assert picks["c"]["match"] == "H - A" and picks["c"]["match_id"] == "c" and not picks["c"]["fige"]
    assert picks["b"]["niveau"] == "Confiance élevée" and not picks["b"]["valide"]
    assert picks["d"]["niveau"] == "Confiance moyenne"
    assert [s["match_id"] for s in doc["selections"]] == ["c"]  # b is not validated, d not élevée
    _no_forbidden_words(doc)
    del offers["c"]
    assert basket.build_pronostic("2026-10-27", matchs, offers, None, now, "matin")["selections"] == []


def test_retained_bet_frozen_at_m45_then_settled():
    ko = datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)
    matchs = {"c": _mdoc(ko)}
    early = basket.build_pronostic("2026-10-27", matchs, {"c": [_bet("Victoire H", 1.65, 0.64, 0.61)]}, None,
                                   ko - timedelta(hours=3), "passage")
    assert not early["paris_retenus"]["c"]["fige"]
    first = basket.build_pronostic("2026-10-27", matchs, {"c": [_bet("Victoire H", 1.65, 0.64, 0.61)]}, early,
                                   ko - timedelta(minutes=40), "passage")
    assert first["paris_retenus"]["c"]["fige"] and first["selections"][0]["match_id"] == "c"
    # a later pass offering something else keeps the frozen bet
    again = basket.build_pronostic("2026-10-27", matchs, {"c": [_bet("Victoire A", 2.5, 0.45, 0.42, sel="away")]},
                                   first, ko - timedelta(minutes=10), "passage")
    assert again["paris_retenus"]["c"]["pari"] == "Victoire H" and again["paris_retenus"]["c"]["fige"]
    matchs["c"] = {**matchs["c"], "statut": "termine", "score": "101-99"}
    later = basket.build_pronostic("2026-10-27", matchs, {}, again, ko + timedelta(hours=3), "resultats")
    assert later["paris_retenus"]["c"]["resultat"] == "gagne"
    assert later["selections"][0]["resultat"] == "gagne"
    matchs["c"] = {**matchs["c"], "score": "99-101"}
    assert basket.build_pronostic("2026-10-27", matchs, {}, again, ko + timedelta(hours=3),
                                  "resultats")["paris_retenus"]["c"]["resultat"] == "perdu"
    postponed = {"c": {**_mdoc(ko), "reporte": True}}
    assert basket.build_pronostic("2026-10-27", postponed, {}, again, ko + timedelta(hours=3),
                                  "resultats")["paris_retenus"]["c"]["resultat"] == "rembourse"


# ---------------------------------------------------------------- agent_pass: the collections written

def _feed(request: httpx.Request) -> dict:
    return ODDS if request.url.path.endswith("/odds") else GAMES


def _offline_basket(ap, monkeypatch, payload=_feed):
    api, calls = _api(payload)
    monkeypatch.setattr(ap, "_basket_api", lambda: api)
    monkeypatch.setattr(basket, "load_season", lambda *a, **k: [])
    monkeypatch.setattr(basket, "predictions", lambda history, games: {g["id"]: _pred() for g in games})
    return calls


def _tree(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}


def test_prematch_writes_only_the_basket_collections(tmp_path, monkeypatch):
    ap = _agent_pass()
    _offline_basket(ap, monkeypatch)
    page, out = tmp_path / "page", tmp_path / "out"
    page.mkdir()
    ap.prematch(["NBA"], str(page), str(out), datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc))
    assert _tree(out) == {"basket_matchs/519585.json", "basket_pronostic/2026-10-27.json", "etat.json",
                          "manifest.json"}
    doc = json.loads((out / "basket_matchs/519585.json").read_text())
    assert MATCH_KEYS <= doc.keys() and doc["domicile"] == "Boston Celtics"
    prono = json.loads((out / "basket_pronostic/2026-10-27.json").read_text())
    assert set(prono) >= {"jour", "sport", "maj", "passage", "paris_retenus", "selections", "combines"}
    assert set(prono["paris_retenus"]) == {"519585"}
    p = prono["paris_retenus"]["519585"]
    assert p["match_id"] == "519585" and p["match"] == "Boston Celtics - Brooklyn Nets"
    assert p["pari"] is None or (PICK_KEYS <= p.keys() and p["cote"] >= 1.60)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["matchs"] == [] and manifest["basket_matchs"] == ["519585"]
    for d in (doc, prono):
        _no_forbidden_words(d)


def test_m45_pass_reuses_the_frozen_bet_from_the_page(tmp_path, monkeypatch):
    ap = _agent_pass()
    _offline_basket(ap, monkeypatch)
    page, out = tmp_path / "page", tmp_path / "out"
    (page / "basket_pronostic").mkdir(parents=True)
    frozen = {"pari": "Brooklyn Nets +12,5 points (handicap)", "marche": "Handicap de points",
              "match": "Boston Celtics - Brooklyn Nets", "match_id": "519585", "competition": "NBA",
              "coup_envoi": "2026-10-27T23:30:00Z", "cote": 1.9, "proba": 0.55, "proba_marche": 0.52,
              "confiance": 0.535, "niveau": "Confiance moyenne", "valide": False, "fige": True,
              "regle": {"marche": "handicap", "selection": "away", "ligne": -12.5}}
    (page / "basket_pronostic/2026-10-27.json").write_text(json.dumps(
        {"jour": "2026-10-27", "paris_retenus": {"519585": frozen}, "selections": [], "combines": []}))
    ap.prematch(["NBA"], str(page), str(out), datetime(2026, 10, 27, 22, 45, tzinfo=timezone.utc))
    prono = json.loads((out / "basket_pronostic/2026-10-27.json").read_text())
    assert prono["paris_retenus"]["519585"] == frozen


def test_final_scores_settle_in_the_basket_collections(tmp_path, monkeypatch):
    ap = _agent_pass()
    _offline_basket(ap, monkeypatch)
    page, out = tmp_path / "page", tmp_path / "out"
    (page / "basket_matchs").mkdir(parents=True)
    (page / "basket_pronostic").mkdir()
    (page / "matchs").mkdir()
    ko = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)
    (page / "basket_matchs/519577.json").write_text(json.dumps(
        _mdoc(ko, "Cleveland Cavaliers", "Minnesota Timberwolves", jour="2026-10-26")))
    bet = {"pari": "Victoire Minnesota Timberwolves", "marche": "Vainqueur (prolongation comprise)",
           "match": "Cleveland Cavaliers - Minnesota Timberwolves", "match_id": "519577", "competition": "NBA",
           "coup_envoi": "2026-10-26T23:00:00Z", "cote": 2.1, "proba": 0.5, "proba_marche": 0.47,
           "confiance": 0.485, "niveau": "Confiance moyenne", "valide": True, "fige": True,
           "regle": {"marche": "winner", "selection": "away", "ligne": None}}
    (page / "basket_pronostic/2026-10-26.json").write_text(json.dumps(
        {"jour": "2026-10-26", "paris_retenus": {"519577": bet}, "selections": [], "combines": []}))
    ap.scores(["NBA"], str(page), str(out), True, datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc))
    assert not (out / "matchs").exists() and not (out / "pronostic").exists() and not (out / "stats").exists()
    doc = json.loads((out / "basket_matchs/519577.json").read_text())
    assert doc["statut"] == "termine" and doc["score"] == "127-125"
    assert [q["periode"] for q in doc["score_quarts"]][-1] == "Prol."
    prono = json.loads((out / "basket_pronostic/2026-10-26.json").read_text())
    assert prono["paris_retenus"]["519577"]["resultat"] == "perdu"
    assert prono["passage"] == "resultats"


def test_mixed_live_keeps_basket_out_of_football(tmp_path, monkeypatch):
    ap = _agent_pass()
    _offline_basket(ap, monkeypatch)
    seen = {}

    def fake_live_foot(comps, page, out, now, docs):
        seen.update(comps=comps, docs=docs)
        ap._write(out, {}, "foot", now)

    monkeypatch.setattr(ap, "_live_foot", fake_live_foot)
    page, out = tmp_path / "page", tmp_path / "out"
    (page / "basket_matchs").mkdir(parents=True)
    ko = datetime(2026, 10, 27, 0, 30, tzinfo=timezone.utc)
    (page / "basket_matchs/519582.json").write_text(json.dumps(
        _mdoc(ko, "Houston Rockets", "Atlanta Hawks", jour="2026-10-26")))
    ap.live(["FR1", "NBA"], str(page), str(out), datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc))
    assert seen == {"comps": ["FR1"], "docs": {}}  # football gets no basketball document
    assert json.loads((out / "basket_matchs/519582.json").read_text())["score"] == "120-110"
    assert (out / "basket_pronostic/2026-10-26.json").exists()
    assert not list((out / "matchs").glob("*.json"))
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["matchs"] == [] and manifest["basket_matchs"] == ["519582"]


def test_football_pronostic_has_no_basket_legs(tmp_path):
    ap = _agent_pass()
    assert not hasattr(basket, "legs")
    page, out = tmp_path / "page", tmp_path / "out"
    (page / "matchs").mkdir(parents=True)
    old = {**_mdoc(datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)),
           "paris_basket": [_bet("Victoire H", 1.65, 0.64, 0.61)]}
    (page / "matchs/2026-10-27-h-a.json").write_text(json.dumps(old))  # left by an older pass
    assert ap._load_page(str(page)) == {}
    ap._pronostic(None, str(page), {}, str(out), datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc), "maj",
                  "2026-10-27")
    doc = json.loads((out / "pronostic/2026-10-27.json").read_text())
    assert "2026-10-27-h-a" not in doc["paris_retenus"] and "basket" not in json.dumps(doc)
    assert "basket" not in (out / "stats/bilan.json").read_text()


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
