import gzip
import json
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from sportai.connectors.simulation import SimulationProvider
from sportai.selection.engine import SelectionConfig
from sportai.services import goals_refresh as gr
from sportai.services.dashboard import goal_market_validated, goals_block
from sportai.services.prematch import GOALS_MODEL_DC, run_prematch

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def _fixture(fid, home, away, hg, ag, when, status="FT", rnd="Regular Season - 1"):
    return {"fixture": {"id": fid, "date": when.isoformat(), "status": {"short": status}},
            "league": {"round": rnd}, "teams": {"home": {"id": 1, "name": home}, "away": {"id": 2, "name": away}},
            "goals": {"home": hg, "away": ag}, "score": {"fulltime": {"home": hg, "away": ag}}}


def _with_stats(f):
    return {**f, "statistics": [
        {"team": {"id": 1}, "statistics": [{"type": "expected_goals", "value": "1.40"},
                                           {"type": "Shots on Goal", "value": 5}, {"type": "Total Shots", "value": 12}]},
        {"team": {"id": 2}, "statistics": [{"type": "expected_goals", "value": "0.70"},
                                           {"type": "Shots on Goal", "value": 2}, {"type": "Total Shots", "value": 7}]}]}


def _write(folder, name, data):
    with gzip.open(folder / name, "wt") as f:
        json.dump({"meta": {}, "data": data}, f)


def test_refresh_downloads_only_missing_stats_and_respects_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("SPORTAI_GOALS_CACHE", str(tmp_path))
    t = NOW - timedelta(days=3)
    old = _fixture(10, "Lyon", "Nice", 2, 1, t)
    new = _fixture(11, "Nice", "Lyon", 0, 0, t + timedelta(days=1))
    later = _fixture(12, "Lyon", "Nice", None, None, NOW + timedelta(days=2), status="NS")
    playoff = _fixture(13, "Lyon", "Metz", 1, 0, t, rnd="Relegation Round")
    _write(tmp_path, "fixtures_league61_season2026.json.gz", {"response": [old]})
    _write(tmp_path, "fixtures_ids_aaaa.json.gz", {"response": [_with_stats(old)]})
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        assert request.headers["x-apisports-key"] == "k"
        if "ids" in request.url.params:
            ids = request.url.params["ids"].split("-")
            body = {"errors": [], "response": [_with_stats(new)] if "11" in ids else []}
        else:
            body = {"errors": [], "response": [old, new, later, playoff]}
        return httpx.Response(200, json=body, headers={"x-ratelimit-requests-remaining": "7000"})

    api = gr.ApiFootballCache(tmp_path, "k", max_requests=2, transport=httpx.MockTransport(handler), pause=0)
    history, teams, report = gr.refresh_data(NOW, api, leagues=("FR1", "EN1"))
    # FR1 list + one stats chunk (fixture 11 only; 13 is finished too, so asked, then filtered as a play-off);
    # EN1 is cut by the budget and reported, never invented.
    assert report["requests"] == 2 and any("EN1" in e for e in report["errors"])
    assert calls[1]["ids"] == "11-13"
    assert [m.fid for m in history["FR1"]] == [10, 11]
    m = history["FR1"][1]
    assert (m.xg_h, m.sot_a, m.sh_h) == (1.4, 2.0, 12.0)
    assert teams["FR1"] == ["Lyon", "Nice"]  # play-off opponents are not league teams
    # without download, everything comes from the cache (the new snapshot list included)
    h2, _, rep2 = gr.refresh_data(NOW, None, leagues=("FR1",))
    assert [m.fid for m in h2["FR1"]] == [10, 11] and rep2["requests"] == 0 and len(calls) == 2


def _book(day="2026-10-09"):
    p = {"btts_yes": 0.55, "over_1.5": 0.78, "over_2.5": 0.52, "over_3.5": 0.27, "lam": 1.5, "mu": 1.1, "dc": {}}
    return gr.GoalsBook({"model": gr.MODEL_LABEL, "day": day, "leagues": {
        "FR1": {"teams": ["Paris Saint Germain", "Stade Rennais FC", "Lens"],
                "pairs": {"Paris Saint Germain|Stade Rennais FC": p, "Lens|Paris Saint Germain": {"lam": 1, "mu": 1}}}}})


def test_book_lookup_matches_names_across_providers_and_falls_back():
    book = _book()
    probs, note = book.lookup("FR1", "PSG", "Rennes")
    assert note is None
    assert probs["btts:no"] == pytest.approx(0.45) and probs["totals:under@3.5"] == pytest.approx(0.73)
    assert probs["totals:over@1.5"] == 0.78
    assert book.lookup("FR1", "PSG", "Nantes") == (None, "équipe non reconnue : Nantes")
    assert book.lookup("FR1", "Lens", "PSG")[1] == "historique insuffisant pour le modèle de buts"
    assert book.lookup("EN1", "Arsenal", "Chelsea")[0] is None


def test_book_is_only_used_on_the_day_it_was_built(tmp_path):
    path = tmp_path / "gm.json"
    gr.save_book(_book().data, path)
    assert gr.GoalsBook.load(NOW, path) is not None
    assert gr.GoalsBook.load(NOW + timedelta(days=1), path) is None
    assert gr.GoalsBook.load(NOW, tmp_path / "missing.json") is None


def test_prematch_uses_enriched_goal_probabilities_with_fallback():
    sim = SimulationProvider(now=NOW)
    fixtures = sim.upcoming_fixtures()
    known = fixtures[0].home
    enriched = gr.enriched_labels({"btts_yes": 0.61, "over_1.5": 0.83, "over_2.5": 0.57, "over_3.5": 0.31})

    def lookup(home, away):
        return (enriched, None) if home == known else (None, "équipe non reconnue : " + home)

    cfg = SelectionConfig(enabled_markets=("1x2", "double_chance", "dnb", "totals", "btts"))
    r = run_prematch(sim.results(), fixtures, sim.odds(), cfg, now=NOW, goals_lookup=lookup,
                     goals_model_label=gr.MODEL_LABEL)
    analysed = [f for f in r.fixtures if f.expected_goals]
    hit = [f for f in analysed if f.fixture.home == known]
    assert hit, "the simulated fixture with the known team should be analysed"
    for f in analysed:
        if f.fixture.home == known:
            assert f.goals_model == gr.MODEL_LABEL and f.goals_model_note is None
            assert f.probabilities["btts:yes"] == 0.61 and f.probabilities["totals:under@2.5"] == 0.43
        else:
            assert f.goals_model == GOALS_MODEL_DC and f.goals_model_note.startswith("équipe non reconnue")
    ids = {f.fixture.provider_event_id for f in hit}
    goal_cands = [e for e in r.selection.selected + r.selection.rejected
                  if e.candidate.match_id in ids and e.candidate.market in ("btts", "totals")
                  and e.candidate.line in (None, 1.5, 2.5, 3.5)]
    assert goal_cands
    for e in goal_cands:
        label = f"{e.candidate.market}:{e.candidate.selection}" + (
            f"@{e.candidate.line:g}" if e.candidate.line is not None else "")
        assert e.candidate.outcome.win == pytest.approx(enriched[label])
        assert e.p_low < e.candidate.outcome.win  # pessimistic quantile below the central value
        assert np.all((e.candidate.win_prob_samples >= 0) & (e.candidate.win_prob_samples <= 1))


def test_goal_validity_rule_and_block():
    assert goal_market_validated("totals", 1.5, gr.MODEL_LABEL)
    assert goal_market_validated("totals", 3.5, gr.MODEL_LABEL)
    assert not goal_market_validated("totals", 2.5, gr.MODEL_LABEL)
    assert not goal_market_validated("btts", None, gr.MODEL_LABEL)
    assert not goal_market_validated("totals", 1.5, GOALS_MODEL_DC)
    assert not goal_market_validated("totals", 1.5, None)
    probs = {"btts:yes": 0.6, "totals:over@1.5": 0.8, "totals:over@2.5": 0.5, "totals:under@3.5": 0.7}
    f = {"expected_goals": [1.4, 1.1], "probabilities": probs, "goals_model": gr.MODEL_LABEL}
    block = goals_block(f, [], ("totals", "btts"))
    valid = {r["pari"]: r["valide"] for r in block["marches"]}
    assert valid == {"Les deux équipes marquent : oui": False, "Plus de 1,5 buts": True,
                     "Plus de 2,5 buts": False, "Moins de 3,5 buts": True}
    assert "fragile" in block["avertissement"] and "non validés" in block["avertissement"]
    dc_block = goals_block({**f, "goals_model": GOALS_MODEL_DC}, [], ("totals", "btts"))
    assert not any(r["valide"] for r in dc_block["marches"]) and "non validés" in dc_block["avertissement"]


def test_goal_legs_are_valid_only_for_enriched_15_and_35(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.db.models import AnalysisRun, Base, CandidateRow
    from sportai.services.pronostic import legs_from_db

    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    ko = NOW + timedelta(hours=6)
    for comp, match_id, model in (("FR1", "m1", gr.MODEL_LABEL), ("EN1", "m2", GOALS_MODEL_DC)):
        run = AnalysisRun(kind="prematch", competition=comp, mode="real", status="completed", started_at=NOW,
                          config={"enabled_markets": ["1x2"]}, triggered_by="test",
                          fixtures=[{"match_id": match_id, "match": f"A{match_id} - B{match_id}",
                                     "kickoff": ko.isoformat(), "skipped_reason": None, "goals_model": model}])
        db.add(run)
        db.flush()
        for market, sel, line in (("totals", "over", 1.5), ("totals", "under", 3.5), ("totals", "over", 2.5),
                                  ("btts", "yes", None), ("1x2", "home", None)):
            db.add(CandidateRow(run_id=run.id, mode="real", match_id=match_id, match_label=f"A{match_id} - B{match_id}",
                                market=market, selection=sel, line=line, odds=1.9, bookmaker="b",
                                odds_observed_at=NOW, p_win=0.55, p_push=0.0, p_low=0.5, fair_odds=1.8, ev=0.04,
                                ev_low=0.0, ev_stress=0.0, market_fair_prob=0.52, data_quality=0.8, score=0.0,
                                accepted=False, reasons=[]))
    db.commit()
    legs = legs_from_db(db, NOW.date())
    valid = {(l["match"], l["pari"]): l["valide"] for l in legs}
    assert valid[("Am1 - Bm1", "Plus de 1,5 buts")] and valid[("Am1 - Bm1", "Moins de 3,5 buts")]
    assert not valid[("Am1 - Bm1", "Plus de 2,5 buts")]
    assert not valid[("Am1 - Bm1", "Les deux équipes marquent : oui")]
    assert valid[("Am1 - Bm1", "Victoire Am1")]
    assert not any(v for (m, p), v in valid.items() if m == "Am2 - Bm2" and p != "Victoire Am2")
