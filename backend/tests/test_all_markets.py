"""Every market of the odds feed: API-Football parser, pricing from the score matrix, settlement, selection on
trial ("non validé"), tickets and the player."""

from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from sportai.connectors.api_football import ApiFootballProvider
from sportai.connectors.api_football_odds import map_value, parse_odds
from sportai.connectors.base import Fixture, OddsQuote
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey, market_group, outcome, outcomes, settle_score
from sportai.engine.value import Outcome
from sportai.selection.engine import Candidate, SelectionConfig, select
from sportai.services.dashboard import bet_label, settle

NOW = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
KO = NOW + timedelta(hours=2)
M = dc.score_matrix(1.45, 1.15, -0.06)


def P(market, sel, line=None):
    return outcome(M, MarketKey(market, sel, line))


def api_item(fid=101, books=None):
    books = books or [
        {"id": 8, "name": "Bet365", "bets": [
            {"id": 1, "name": "Match Winner", "values": [{"value": "Home", "odd": "2.10"}, {"value": "Draw", "odd": "3.40"},
                                                          {"value": "Away", "odd": "3.50"}]},
            {"id": 4, "name": "Asian Handicap", "values": [{"value": "Home -1.25", "odd": "5.75"},
                                                           {"value": "Away -1.25", "odd": "1.14"}]},
            {"id": 9, "name": "Handicap Result", "values": [{"value": "Draw -1", "odd": "5.50"}]},
            {"id": 38, "name": "Exact Goals Number", "values": [{"value": 0, "odd": "19.00"},
                                                                {"value": "more 7", "odd": "13.00"}]},
            {"id": 49, "name": "Total Goals/Both Teams To Score", "values": [{"value": "o/yes 2.5", "odd": "1.73"}]},
            {"id": 45, "name": "Corners Over Under", "values": [{"value": "Over 10.5", "odd": "1.90"}]},
            {"id": 13, "name": "First Half Winner", "values": [{"value": "Home", "odd": "2.80"}]},
        ]},
        {"id": 4, "name": "Pinnacle", "bets": [
            {"id": 1, "name": "Match Winner", "values": [{"value": "Home", "odd": "2.15"}]},
            {"id": 45, "name": "Corners Over Under", "values": [{"value": "Over 10.5", "odd": "1.95"}]},
        ]},
    ]
    return {"fixture": {"id": fid, "date": KO.isoformat()}, "update": "2026-10-14T12:00:57+00:00", "bookmakers": books}


def test_parser_maps_every_score_bet_and_lists_the_others():
    teams = {"101": ("Feyenoord", "Como", KO)}
    quotes, other = parse_odds([api_item(), api_item(fid=999)], teams, timedelta(hours=4))
    keys = {(q.bookmaker, q.market, q.selection, q.line): q.odds for q in quotes}
    assert keys[("Bet365", "1x2", "home", None)] == 2.10 and keys[("Pinnacle", "1x2", "home", None)] == 2.15
    # handicap numbers are the home side's, on both sides of the line
    assert keys[("Bet365", "asian_handicap", "home", -1.25)] == 5.75
    assert keys[("Bet365", "asian_handicap", "away", -1.25)] == 1.14
    assert keys[("Bet365", "euro_handicap", "draw", -1.0)] == 5.50
    assert keys[("Bet365", "exact_goals", "0", None)] == 19.0 and keys[("Bet365", "exact_goals", "7+", None)] == 13.0
    assert keys[("Bet365", "total_btts", "over_yes", 2.5)] == 1.73
    assert all(q.provider_event_id == "101" and q.home == "Feyenoord" and q.validation_status == "valid" for q in quotes)
    assert quotes[0].observed_at == datetime(2026, 10, 14, 12, 0, 57, tzinfo=timezone.utc)
    names = {o["marche"]: o for o in other["101"]}
    assert set(names) == {"Corners Over Under", "First Half Winner"} and "999" not in other
    corners = names["Corners Over Under"]
    assert corners["statut"] == "non analysé" and corners["bookmakers"] == 2
    assert corners["meilleures_cotes"] == [{"valeur": "Over 10.5", "cote": 1.95, "bookmaker": "Pinnacle"}]


@pytest.mark.parametrize("bet,value,expected", [
    ("Home/Away", "Away", ("dnb", "away", None)),
    ("Double Chance", "Draw/Away", ("double_chance", "X2", None)),
    ("Goals Over/Under", "Over 2.25", ("totals", "over", 2.25)),
    ("Total - Away", "Under 1", ("team_total_away", "under", 1.0)),
    ("Home Team Score a Goal", "Yes", ("team_total_home", "over", 0.5)),
    ("Result/Total Goals", "Draw/Under 2.5", ("result_total", "draw_under", 2.5)),
    ("Results/Both Teams Score", "Away/No", ("result_btts", "away_no", None)),
    ("Win To Nil", "Away", ("win_to_nil_away", "yes", None)),
    ("Exact Score", "2:1", ("exact_score", "2:1", None)),
    ("Number Of Goals In Match", "2 or 3 goals", ("goal_range", "2-3", None)),
    ("Odd/Even", "Odd", ("odd_even", "odd", None)),
    ("Asian Handicap", "Draw +1", None),
    ("Anytime Goal Scorer", "Someone", None),
])
def test_bet_names(bet, value, expected):
    assert map_value(bet, value) == expected


def test_complementary_selections_sum_to_one():
    for market, line in (("1x2", None), ("dnb", None), ("btts", None), ("totals", 2.5), ("totals", 2.0),
                         ("totals", 2.75), ("team_total_home", 1.5), ("team_total_away", 0.75),
                         ("asian_handicap", -0.5), ("asian_handicap", 0.0), ("asian_handicap", -1.25),
                         ("euro_handicap", -1.0), ("result_total", 2.5), ("result_btts", None),
                         ("total_btts", 3.5), ("clean_sheet_home", None), ("win_to_nil_away", None),
                         ("odd_even", None), ("odd_even_home", None), ("goal_range", None), ("scoring_draw", None)):
        group = market_group(MarketKey(market, "home" if market in ("1x2", "dnb", "asian_handicap", "euro_handicap")
                                       else "x", line))
        outs = [outcome(M, k) for k in group]
        wins = sum(o.win for o in outs)
        # two-way markets with a refund share the same push: win + win + push = 1
        assert wins + outs[0].push == pytest.approx(1.0), market
    assert sum(P("exact_score", f"{h}:{a}").win for h in range(11) for a in range(11)) == pytest.approx(1.0)
    assert sum(P("exact_goals", str(n)).win for n in range(6)) + P("exact_goals", "6+").win == pytest.approx(1.0)
    assert (sum(P("exact_goals_home", str(n)).win for n in range(3)) + P("exact_goals_home", "3+").win
            == pytest.approx(1.0))


def test_markets_agree_with_each_other():
    assert P("asian_handicap", "home", -0.5).win == pytest.approx(P("1x2", "home").win)
    ah0, dnb = P("asian_handicap", "away", 0.0), P("dnb", "away")
    assert (ah0.win, ah0.push) == pytest.approx((dnb.win, dnb.push))
    assert P("euro_handicap", "home", 0.0).win == pytest.approx(P("1x2", "home").win)
    assert P("euro_handicap", "draw", -1.0).win == pytest.approx(sum(M[k + 1, k] for k in range(10)))
    assert P("team_total_home", "over", 0.5).win == pytest.approx(1 - P("clean_sheet_away", "yes").win)
    assert P("win_to_nil_home", "yes").win == pytest.approx(M[1:, 0].sum())
    assert (P("result_total", "home_over", 2.5).win + P("result_total", "home_under", 2.5).win
            == pytest.approx(P("1x2", "home").win))
    assert P("total_btts", "over_yes", 2.5).win <= min(P("totals", "over", 2.5).win, P("btts", "yes").win)
    assert P("goal_range", "4+").win == pytest.approx(P("totals", "over", 3.5).win)
    assert P("scoring_draw", "yes").win == pytest.approx(P("1x2", "draw").win - M[0, 0])
    # quarter line = half the stake on each nearby line
    q, a, b = P("asian_handicap", "home", -0.75), P("asian_handicap", "home", -0.5), P("asian_handicap", "home", -1.0)
    assert q.win == pytest.approx((a.win + b.win) / 2) and q.push == pytest.approx((a.push + b.push) / 2)
    # vectorised over samples = one by one
    stack = np.stack([M, dc.score_matrix(1.0, 1.8, 0.0)])
    win, push = outcomes(stack, MarketKey("asian_handicap", "away", 0.0))
    assert win[1] == pytest.approx(outcome(stack[1], MarketKey("asian_handicap", "away", 0.0)).win)
    assert push[0] == pytest.approx(P("1x2", "draw").win)


def test_labels_settle_like_the_pricing():
    home, away = "Lyon", "Nice"
    keys = [MarketKey("asian_handicap", "home", -1.0), MarketKey("asian_handicap", "away", -0.5),
            MarketKey("euro_handicap", "draw", -1.0), MarketKey("team_total_away", "under", 1.5),
            MarketKey("result_total", "home_over", 2.5), MarketKey("result_btts", "draw_yes"),
            MarketKey("total_btts", "under_no", 2.5), MarketKey("clean_sheet_home", "no"),
            MarketKey("win_to_nil_away", "yes"), MarketKey("odd_even_home", "even"), MarketKey("exact_score", "2:1"),
            MarketKey("exact_goals", "3"), MarketKey("exact_goals_away", "2+"), MarketKey("goal_range", "0-1"),
            MarketKey("scoring_draw", "yes"), MarketKey("totals", "over", 3.0)]
    labels = {bet_label(k.market, k.selection, home, away, k.line) for k in keys}
    assert len(labels) == len(keys)
    assert bet_label("asian_handicap", "away", home, away, -0.5) == "Nice handicap asiatique +0,5"
    assert bet_label("euro_handicap", "draw", home, away, -1.0) == "Handicap Lyon -1 : nul"
    for hg, ag in ((0, 0), (1, 1), (2, 1), (3, 0), (0, 2), (4, 3)):
        for k in keys:
            got = settle(bet_label(k.market, k.selection, home, away, k.line), home, away, hg, ag)
            m = np.zeros((11, 11))
            m[hg, ag] = 1.0
            o = outcome(m, k)
            assert got == ("gagne" if o.win == 1 else "rembourse" if o.push == 1 else "perdu"), (k, hg, ag)
    assert settle_score(MarketKey("asian_handicap", "home", -0.75), 1, 0) == "demi_gagne"
    assert settle_score(MarketKey("totals", "over", 2.25), 1, 1) == "demi_perdu"


def _cand(market="asian_handicap", sel="home", line=-0.5, odds=2.0, p=0.58, mfp=0.55, push=0.0, spread=0.01):
    rng = np.random.default_rng(0)
    return Candidate(match_id="m1", match_label="A - B", market=market, selection=sel, line=line, odds=odds,
                     bookmaker="Pinnacle", odds_observed_at=NOW - timedelta(hours=3), odds_expires_at=None,
                     outcome=Outcome(win=p, push=push), win_prob_samples=rng.normal(p, spread, 400), push_prob=push,
                     market_fair_prob=mfp, data_quality=0.9, data_mode="real", max_odds_age=4 * 3600.0)


def test_unvalidated_markets_selected_on_trial_with_stricter_gates():
    cfg = SelectionConfig()
    ok = select([_cand()], cfg, NOW)  # value +16 %, 3-hour-old API-Football odds
    assert ok.selected and not ok.selected[0].validated and ok.selected[0].summary()["valide"] is False
    thin = select([_cand(p=0.52, mfp=0.50)], cfg, NOW)  # +4 %: enough for 1X2, not on trial
    assert not thin.selected and "market not validated" in " ".join(thin.rejected[0].reasons)
    assert select([_cand(p=0.52, mfp=0.50, market="1x2", sel="home", line=None)], cfg, NOW).selected
    assert not select([_cand(mfp=None)], cfg, NOW).selected  # nothing to compare with
    assert not select([_cand(line=-0.75)], cfg, NOW).selected  # quarter line
    old = _cand()
    old.max_odds_age = None  # a fast feed's limit: 15 minutes
    assert not select([old], cfg, NOW).selected
    ucl = SelectionConfig(enabled_markets=(), trial_markets=())  # Champions League: nothing on trial
    assert "disabled" in " ".join(select([_cand()], ucl, NOW).rejected[0].reasons)
    # a whole line compares the market with the chance of winning when not refunded
    whole = _cand(line=0.0, p=0.50, push=0.25, mfp=0.66, odds=2.2)
    assert select([whole], cfg, NOW).selected


def test_exact_score_consensus_over_the_whole_board():
    from sportai.services.prematch import _market_consensus

    obs = NOW - timedelta(minutes=5)
    board = {f"{h}:{a}": float(M[h, a]) for h in range(7) for a in range(7)}
    quotes = [OddsQuote("api-football", "Bet365", "101", "A", "B", KO, "exact_score", s, None,
                        round(0.93 / p, 2), obs, "real", "valid") for s, p in board.items()]
    cons = _market_consensus(quotes)
    assert cons[MarketKey("exact_score", "1:1")] == pytest.approx(M[1, 1], abs=0.01)
    assert not _market_consensus(quotes[:5])  # partial board: no market probability


def test_api_football_quotes_one_request_per_date(monkeypatch):
    from sportai.services.runs import api_football_quotes

    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        if "fixture" in req.url.params:
            return httpx.Response(200, json={"errors": [], "paging": {"current": 1, "total": 1},
                                             "response": [api_item(fid=202)]})
        return httpx.Response(200, json={"errors": [], "paging": {"current": 1, "total": 1}, "response": [api_item()]})

    api = ApiFootballProvider("k", transport=httpx.MockTransport(handler))
    fx = [Fixture("api-football", "101", "UCL", KO, "Feyenoord", "Como", "real"),
          Fixture("api-football", "202", "UCL", KO, "Roma", "Real Madrid", "real")]
    quotes, other = api_football_quotes(api, "UCL", fx)
    assert [p.get("date") or p.get("fixture") for p in seen] == ["2026-10-14", "202"]
    assert seen[0]["league"] == "2" and seen[0]["season"] == "2026"
    assert {q.provider_event_id for q in quotes} == {"101", "202"} and set(other) == {"101", "202"}


def _run_with_rows(db, rows, competition="FR1"):
    from sportai.db.models import AnalysisRun, CandidateRow

    run = AnalysisRun(kind="prematch", competition=competition, mode="real", status="completed", started_at=NOW,
                      config={"enabled_markets": ["1x2", "double_chance", "dnb"], "max_market_divergence": 0.10},
                      triggered_by="test",
                      fixtures=[{"match_id": mid, "match": f"{mid}h - {mid}a", "kickoff": KO.isoformat(),
                                 "skipped_reason": None} for mid in sorted({r[0] for r in rows})])
    db.add(run)
    db.flush()
    for mid, market, sel, line, odds, p, push, mfp in rows:
        db.add(CandidateRow(run_id=run.id, mode="real", match_id=mid, match_label=f"{mid}h - {mid}a", market=market,
                            selection=sel, line=line, odds=odds, bookmaker="Pinnacle", odds_observed_at=NOW,
                            p_win=p, p_push=push, p_low=p - 0.02, fair_odds=1 / p, ev=round(p * (odds - 1) - (1 - p - push), 4),
                            ev_low=0.0, ev_stress=0.0, market_fair_prob=mfp, data_quality=0.8, score=0.0,
                            accepted=False, reasons=[]))
    db.commit()
    return run


def test_legs_tickets_and_player_take_unvalidated_markets_with_care(tmp_path, monkeypatch):
    from sportai.services import pronostic

    monkeypatch.setattr(pronostic, "PER_MATCH_FROM", "2099-01-01")  # the combined tickets' own path
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.db.models import Base
    from sportai.services.bilan import apply_stakes
    from sportai.services.joueur import candidates
    from sportai.services.pronostic import TRIAL_PER_MATCH, build_pronostic, legs_from_db

    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    rows = [("m1", "1x2", "home", None, 1.60, 0.66, 0.0, 0.62),
            ("m2", "asian_handicap", "away", -0.5, 1.85, 0.60, 0.0, 0.57),  # +11 %: kept, non validé
            ("m2", "asian_handicap", "home", -0.75, 2.40, 0.50, 0.05, 0.45),  # quarter line: never a leg
            ("m2", "team_total_home", "over", 1.5, 1.70, 0.60, 0.0, 0.58),  # +2 %: a goal bet, kept (2026-10-24)
            ("m2", "team_total_away", "over", 1.5, 2.40, 0.38, 0.0, 0.37),  # goal bet, but under 40 %: never a leg
            ("m2", "exact_score", "1:0", None, 9.0, 0.12, 0.0, None),  # no market probability: never a leg
            ("m3", "result_btts", "home_yes", None, 3.0, 0.36, 0.0, 0.33)]
    rows += [("m5", "exact_score", "4:0", None, 48.0, 0.027, 0.0, 0.016)]
    rows += [("m4", "exact_goals", str(n), None, 4.0, 0.26, 0.0, 0.25) for n in range(8)]  # 8 on trial, 5 kept
    _run_with_rows(db, rows)
    legs = legs_from_db(db, NOW.date())
    by = {(l["match"], l["pari"]): l for l in legs}
    ah = by[("m2h - m2a", "m2a handicap asiatique +0,5")]
    assert ah["valide"] is False and ah["validation"] == "non validé" and ah["marche"] == "Handicap asiatique"
    assert by[("m1h - m1a", "Victoire m1h")]["valide"] is True
    assert not any("+0,75" in p or "-0,75" in p for _, p in by)
    assert not any(m == "m2h - m2a" and ("m2a : plus de" in p or "Score exact" in p) for m, p in by)
    # goal bets have their own places and a value above 0 is enough (owner, 2026-10-24); still "non validé"
    tt = by[("m2h - m2a", "m2h : plus de 1,5 buts")]
    assert tt["validation"] == "non validé" and tt["famille"] == "equipe_plus_1_5"
    # the team each bet needs to win or score (rotation risk of the next match, owner 2026-10-26)
    assert by[("m1h - m1a", "Victoire m1h")]["_exige"] == "home" and tt["_exige"] == "home"
    assert "_exige" not in ah  # m2a +0,5: no win needed
    assert sum(m == "m4h - m4a" for m, _ in by) == TRIAL_PER_MATCH
    assert not any(m == "m5h - m5a" for m, _ in by)  # long shot
    assert all("marche" in l and "valide" in l for l in legs)
    later = NOW.replace(hour=9)  # the 8 am pass: nothing fixed yet
    doc = build_pronostic(legs, {}, None, later, "matin")
    for t in doc["tickets"].values():
        # one unvalidated bet per ticket at most, goal bets apart
        assert sum(not l["valide"] and not l.get("famille", "").startswith(("equipe_", "btts", "plus", "moins"))
                   for l in t["jambes"]) <= 1
    trial_tickets = [k for k, t in doc["tickets"].items() if any(not l["valide"] for l in t["jambes"])]
    assert trial_tickets
    apply_stakes(doc, {"mises": {"safe": 0.04, "semi_safe": 0.02, "fun": 0.01}}, 1000.0)
    for k in trial_tickets:
        assert doc["tickets"][k]["mise_pct"] == {"safe": 0.02, "semi_safe": 0.01, "fun": 0.005}[k]
        assert "non validé" in doc["tickets"][k]["mise_reduite"]
    # The player sees the tickets holding them, the "non validé" label carried on each leg.
    cands = candidates(doc, legs)
    held = [l for c in cands for l in c["jambes"] if not l["valide"]]
    assert held and all(l["validation"] == "non validé" and l["marche"] for l in held)


def test_match_document_lists_every_market():
    from sportai.services.dashboard import all_markets_block

    class Row:
        def __init__(self, market, sel, line, ev, accepted=False):
            self.market, self.selection, self.line, self.ev, self.accepted = market, sel, line, ev, accepted
            self.odds, self.bookmaker, self.p_win, self.p_push, self.market_fair_prob = 2.0, "Bet365", 0.5, 0.0, 0.48

    f = {"non_analyses": [{"marche": "Corners Over Under", "statut": "non analysé", "bookmakers": 2,
                           "selections": 1, "meilleures_cotes": []}]}
    block = all_markets_block(f, [Row("1x2", "home", None, 0.01), Row("asian_handicap", "home", -1.0, 0.05)],
                              "A", "B", {"enabled_markets": ["1x2"]})
    first, second = block["analyses"]
    assert first["pari"] == "A handicap asiatique -1" and first["validation"] == "non validé"
    assert second["validation"] == "validé" and block["non_analyses"][0]["statut"] == "non analysé"
    assert block["n_marches"] == 2 and "non analysés" in block["note"]


def _stale_feed_run(tmp_path, monkeypatch, odds_api_down=False):
    """A real-mode pass 15 min before a match whose API-Football odds were last refreshed almost 5 hours earlier
    (2026-10-23: Stuttgart - Gladbach, Alavés - Málaga and Ipswich - Forest kept three 1X2 prices only)."""
    from dataclasses import replace as dc_replace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.config import get_settings
    from sportai.connectors.base import ProviderUnavailable
    from sportai.connectors.simulation import SimulationProvider
    from sportai.db.models import Base, CandidateRow
    from sportai.services import runs

    now = datetime(2026, 10, 23, 18, 45, tzinfo=timezone.utc)
    ko = now + timedelta(minutes=15)
    sim = SimulationProvider(now=now)
    history = [dc_replace(r, provider="api-football", mode="real") for r in sim.results()]
    home, away = sim.upcoming_fixtures()[0].home, sim.upcoming_fixtures()[0].away

    def bet(name, values):
        return {"name": name, "values": [{"value": v, "odd": o} for v, o in values]}

    item = {"fixture": {"id": 999, "date": ko.isoformat()}, "update": "2026-10-23T13:59:20+00:00", "bookmakers": [
        {"id": 8, "name": "Bet365", "bets": [
            bet("Match Winner", [("Home", "2.10"), ("Draw", "3.40"), ("Away", "3.50")]),
            bet("Both Teams Score", [("Yes", "1.57"), ("No", "2.30")]),
            bet("Goals Over/Under", [("Over 1.5", "1.28"), ("Under 1.5", "3.75"), ("Over 2.5", "1.90"),
                                     ("Under 2.5", "1.90")]),
            bet("Total - Home", [("Over 1.5", "2.05"), ("Under 1.5", "1.75")])]}]}

    class FakeApi:
        name = "api-football"

        def __init__(self, key):
            pass

        def results(self, competition, season):
            return history

        def upcoming_fixtures(self, competition):
            return [Fixture("api-football", "999", competition, ko, home, away, "real")]

        def odds_on(self, competition, day):
            return [item]

        def fixture_odds(self, fid):
            return []

    class FakeOddsApi:
        name = "the-odds-api"

        def __init__(self, *a, **k):
            self.last_quota, self.last_events = {}, []

        def odds(self, competition):
            if odds_api_down:
                raise ProviderUnavailable("HTTP 401: usage quota reached")
            return [OddsQuote("the-odds-api", "winamax_fr", "toa-1", home, away, ko, "1x2", s, None, o,
                              now - timedelta(minutes=1), "real", "valid", now + timedelta(minutes=30))
                    for s, o in (("home", 2.05), ("draw", 3.45), ("away", 3.60))]

    monkeypatch.setattr(runs, "ApiFootballProvider", FakeApi)
    monkeypatch.setattr(runs, "TheOddsApiProvider", FakeOddsApi)
    monkeypatch.setattr(runs, "OPENFOOTBALL_FILES", {})
    monkeypatch.setattr(runs, "load_book_safely", lambda now: None)
    monkeypatch.setattr(runs, "scorer_markets", lambda db, api, comp, raw, window, fixtures, now: fixtures)
    eng = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    run = runs.execute_prematch(db, get_settings(), "DE1", "test", mode="real", now=now)
    rows = list(db.query(CandidateRow).filter(CandidateRow.run_id == run.id))
    return run, rows


def test_goal_markets_priced_when_api_football_odds_are_old(tmp_path, monkeypatch):
    run, rows = _stale_feed_run(tmp_path, monkeypatch)
    assert run.status == "completed" and run.data_sources["odds"] == "the-odds-api+api-football"
    by = {(r.market, r.selection, r.line): r for r in rows}
    # the result markets from the current fallback prices only, never from the 5-hour-old feed
    assert {r.bookmaker for r in rows if r.market == "1x2"} == {"winamax_fr"}
    # the goal markets from API-Football's last prices: both teams score, totals, team totals
    assert by[("btts", "yes", None)].odds == 1.57 and by[("btts", "yes", None)].market_fair_prob is not None
    assert {("totals", "over", 1.5), ("totals", "under", 2.5), ("team_total_home", "over", 1.5)} <= by.keys()
    # analysed and offered, but the official selections keep their own 4-hour age limit
    assert any("old" in x for x in by[("btts", "yes", None)].reasons)


def test_goal_markets_priced_without_odds_api_credits(tmp_path, monkeypatch):
    run, rows = _stale_feed_run(tmp_path, monkeypatch, odds_api_down=True)
    assert run.status == "completed" and run.data_sources["odds"] == "api-football"
    assert {r.market for r in rows} >= {"btts", "totals", "team_total_home"}
    assert not any(r.market == "1x2" for r in rows)


def test_keep_stale_quotes_never_past_kickoff_nor_twelve_hours():
    from sportai.services.runs import keep_stale_quotes

    def q(market, observed, line=None):
        return OddsQuote("api-football", "Bet365", "1", "A", "B", KO, market, "over" if line else "yes", line, 1.8,
                         observed, "real", "valid", observed + timedelta(hours=4))

    old = [q("btts", NOW - timedelta(hours=5)), q("1x2", NOW - timedelta(hours=5)),
           q("totals", NOW - timedelta(hours=13), 2.5), q("totals", NOW - timedelta(hours=5), 1.5)]
    covered = [q("totals", NOW, 1.5)]
    kept = keep_stale_quotes(old, covered, NOW)
    assert [(k.market, k.line) for k in kept] == [("btts", None)] and kept[0].expires_at == KO
