"""Missing key players: factor, clipping, half strength, result markets only (docs/22-absences.md)."""

import importlib.util
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from sportai.connectors.simulation import SimulationProvider
from sportai.engine.football import dixon_coles as dc
from sportai.services.lineups import (ABSENCE_MAX, ABSENCE_MIN, SOURCE_INJURIES, SOURCE_LINEUPS, SideAbsence,
                                      absence_effect, absence_factor, absentees, absents_ready, side_from_absents,
                                      side_from_lineup, team_players_index)
from sportai.services.prematch import RESULT_MARKETS, run_prematch

_spec = importlib.util.spec_from_file_location("agent_pass", Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py")
agent_pass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_pass)

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


# --- the factor ---------------------------------------------------------------------------------------------------

def test_factor_formula_and_centring():
    # nobody missing on either side: the centred formula gives a small lift (the training means are not zero)
    assert absence_factor(0.0, 0.0) == pytest.approx(math.exp(0.395 * 0.128 - 0.046 * 0.423))
    # at the training means: exactly 1
    assert absence_factor(0.128, 0.423) == pytest.approx(1.0)
    # 41 % of the goals and assists missing (Man United, 2025-04-27 in the study)
    assert absence_factor(0.41, 0.0) == pytest.approx(math.exp(-0.395 * (0.41 - 0.128) - 0.046 * 0.423))
    assert absence_factor(0.41, 0.0) < 0.9
    # opponent without two regular defenders: a little more
    assert absence_factor(0.0, 2) > absence_factor(0.0, 0)


def test_factor_is_clipped():
    assert absence_factor(1.0, 0.0) == ABSENCE_MIN == 0.80
    assert absence_factor(0.0, 10) == ABSENCE_MAX == 1.10
    assert absence_factor(1.0, 0.0, strength=0.5) == pytest.approx(math.exp(0.5 * -0.395 * (1 - 0.128) - 0.5 * 0.046 * 0.423))


def test_half_strength_halves_the_exponent():
    full, half = absence_factor(0.30, 1), absence_factor(0.30, 1, strength=0.5)
    assert half == pytest.approx(math.sqrt(full))
    assert abs(half - 1) < abs(full - 1)


# --- squads -------------------------------------------------------------------------------------------------------

def _item(pid, name, pos, goals, assists, season, team=10, apps=10, starts=10, league=39):
    return {"player": {"id": pid, "name": name},
            "statistics": [{"team": {"id": team}, "league": {"id": league, "season": season},
                            "games": {"appearences": apps, "lineups": starts, "minutes": starts * 90, "position": pos},
                            "goals": {"total": goals, "assists": assists}}]}


CURRENT = [_item(1, "Star", "Attacker", 4, 2, 2026), _item(2, "Meneur", "Midfielder", 1, 3, 2026),
           _item(3, "Gardien", "Goalkeeper", 0, 0, 2026), _item(4, "Arrière", "Defender", 0, 0, 2026),
           _item(5, "Banc", "Attacker", 2, 0, 2026, starts=2),
           _item(6, "Prêté", "Attacker", 5, 0, 2026, team=99)]  # his goals for another club do not count
PREVIOUS = [_item(1, "Star", "Attacker", 10, 4, 2025), _item(7, "Parti", "Attacker", 9, 0, 2025)]


def test_team_index_shares_roster_and_defenders():
    tp = team_players_index(CURRENT, PREVIOUS, 10, 39)
    total = (4 + 2) + (1 + 3) + 2 + (10 + 4) + 9
    assert tp.shares[1] == pytest.approx(20 / total) and tp.shares[7] == pytest.approx(9 / total)
    assert 6 not in tp.shares
    assert tp.roster == {1, 2, 3, 4, 5}  # "Parti" left in the summer, "Prêté" plays elsewhere
    assert tp.defenders == {3, 4}  # regular goalkeeper and defender (Banc started 2 of 10)


def _xi(ids, bench=()):
    return {"startXI": [{"player": {"id": i}} for i in ids], "substitutes": [{"player": {"id": i}} for i in bench]}


def test_lineup_side_counts_only_players_not_in_the_squad():
    tp = team_players_index(CURRENT, PREVIOUS, 10, 39)
    full = side_from_lineup(_xi([1, 2, 3, 4], bench=[5]), tp)
    assert full.ga_out == 0 and full.def_out == 0  # the departed player is not "missing"
    benched = side_from_lineup(_xi([2, 3, 4], bench=[1, 5]), tp)
    assert benched.ga_out == 0  # a star on the bench is not missing
    out = side_from_lineup(_xi([2, 4], bench=[5]), tp)
    assert out.ga_out == pytest.approx(tp.shares[1]) and out.missing[0][0] == "Star"
    assert out.def_out == 1 and out.def_missing == ["Gardien"]


def test_injuries_entries_carry_what_the_factor_needs():
    fx = {"fixture": {"id": 5}, "teams": {"home": {"id": 10, "name": "A"}, "away": {"id": 20, "name": "B"}}}
    tp = team_players_index(CURRENT, PREVIOUS, 10, 39)
    injuries = [{"team": {"id": 10}, "player": {"id": 1, "name": "Star", "type": "Missing Fixture", "reason": "Injury"}},
                {"team": {"id": 10}, "player": {"id": 4, "name": "Arrière", "type": "Missing Fixture", "reason": "Suspended"}},
                {"team": {"id": 10}, "player": {"id": 2, "name": "Meneur", "type": "Questionable", "reason": "Knock"}}]
    out = absentees(injuries, fx, {10: {1, 4}}, {10: tp})
    star = out["domicile"][0]
    assert star["id"] == 1 and star["part_bp"] == pytest.approx(tp.shares[1], abs=1e-4) and not star["defense_habituel"]
    side = side_from_absents(out["domicile"])
    assert side.ga_out == pytest.approx(tp.shares[1], abs=1e-4)  # the doubtful one does not count
    assert side.def_out == 1
    assert absents_ready({"jour": "2026-10-09", **out}, "2026-10-09")
    assert not absents_ready({"jour": "2026-10-08", **out}, "2026-10-09")
    assert not absents_ready({"jour": "2026-10-09", "domicile": [{"joueur": "X", "raison": "blessure"}]}, "2026-10-09")


def test_effect_strength_by_source_and_plain_sentence():
    big = SideAbsence(0.41, [("Amad", 0.21), ("L. Martínez", 0.12), ("Dalot", 0.08)], 2, ["Dalot", "de Ligt"])
    none = SideAbsence(0.0, [], 0, [])
    full = absence_effect(none, big, "Bournemouth", "Man United", SOURCE_LINEUPS)
    half = absence_effect(none, big, "Bournemouth", "Man United", SOURCE_INJURIES)
    assert full["exterieur"] == pytest.approx(absence_factor(0.41, 0), abs=1e-4)
    assert half["exterieur"] == pytest.approx(absence_factor(0.41, 0, 0.5), abs=1e-4)
    assert full["domicile"] == pytest.approx(absence_factor(0.0, 2), abs=1e-4)
    assert full["source"] == "compositions" and half["source"] == "blessés"
    assert ("Sans Amad, L. Martínez et Dalot (41 % des buts et passes décisives), l'attaque de Man United est revue "
            "à la baisse.") in full["phrases"]
    assert any(p.startswith("Sans Dalot et de Ligt en défense chez Man United") for p in full["phrases"])
    assert all("pari" not in p.lower() for p in full["phrases"] + half["phrases"])
    quiet = absence_effect(none, none, "A", "B", SOURCE_INJURIES)  # +1.6 %: no sentence
    assert quiet["phrases"] == [] and quiet["domicile"] > 1


# --- the model ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sim():
    return SimulationProvider(now=NOW)


def test_dixon_coles_factors_scale_the_rates_only(sim):
    hist = [dc.MatchResult(r.played_on, r.home, r.away, r.home_goals, r.away_goals) for r in sim.results()]
    model = dc.fit(hist, reference_date=NOW.date())
    h, a = model.teams[0], model.teams[1]
    lam, mu = model.expected_goals(h, a)
    assert model.expected_goals(h, a, (0.9, 1.05)) == pytest.approx((lam * 0.9, mu * 1.05))
    assert np.array_equal(model.score_matrix(h, a), model.score_matrix(h, a, None))
    assert np.allclose(model.score_matrix(h, a, (0.9, 1.05)), dc.score_matrix(lam * 0.9, mu * 1.05, model.rho))
    s0 = model.sample_score_matrices(h, a, n=20, seed=3)
    s1 = model.sample_score_matrices(h, a, n=20, seed=3, factors=(1.0, 1.0))
    assert all(np.allclose(x, y) for x, y in zip(s0, s1))


def _probs(report):
    out = {}
    for e in report.selection.selected + report.selection.rejected:
        c = e.candidate
        out[(c.match_id, c.market, c.selection, c.line)] = c.outcome.win
    return out


def test_run_prematch_moves_result_markets_only(sim):
    fixtures, quotes = sim.upcoming_fixtures(), sim.odds()
    ah = [replace(q, market="asian_handicap", line=-0.5) for q in quotes if q.market == "1x2" and q.selection == "home"]
    quotes = quotes + ah
    target = fixtures[0]
    lookup = lambda fx: (0.85, 1.05) if fx.provider_event_id == target.provider_event_id else None  # noqa: E731
    base = run_prematch(sim.results(), fixtures, quotes, now=NOW)
    corr = run_prematch(sim.results(), fixtures, quotes, now=NOW, absence_lookup=lookup)
    pb, pc = _probs(base), _probs(corr)
    assert pb.keys() == pc.keys()
    tid = target.provider_event_id
    moved = {k for k in pb if pb[k] != pytest.approx(pc[k], abs=1e-12)}
    assert moved and all(k[0] == tid and k[1] in RESULT_MARKETS for k in moved)
    assert pc[(tid, "1x2", "home", None)] < pb[(tid, "1x2", "home", None)] - 0.02
    assert pc[(tid, "1x2", "away", None)] > pb[(tid, "1x2", "away", None)]
    assert pc[(tid, "asian_handicap", "home", -0.5)] < pb[(tid, "asian_handicap", "home", -0.5)]
    for k in pb:  # totals and both teams to score: untouched
        if k[1] in ("totals", "btts"):
            assert pc[k] == pb[k]
    fb = {f.fixture.provider_event_id: f for f in base.fixtures}
    fc = {f.fixture.provider_event_id: f for f in corr.fixtures}
    assert fc[tid].expected_goals == fb[tid].expected_goals  # stored expected goals stay uncorrected
    assert fc[tid].absence_factors == (0.85, 1.05) and fb[tid].absence_factors is None
    for key in ("totals:over@2.5", "btts:yes", "totals:under@1.5"):
        assert fc[tid].probabilities[key] == fb[tid].probabilities[key]


def test_no_information_changes_nothing(sim):
    fixtures, quotes = sim.upcoming_fixtures(), sim.odds()
    base = run_prematch(sim.results(), fixtures, quotes, now=NOW)
    for lookup in (lambda fx: None, lambda fx: (1.0, 1.0)):
        same = run_prematch(sim.results(), fixtures, quotes, now=NOW, absence_lookup=lookup)
        assert _probs(same) == _probs(base)
        assert [f.likely_score for f in same.fixtures] == [f.likely_score for f in base.fixtures]


# --- the pass -----------------------------------------------------------------------------------------------------

class FakeApi:
    def __init__(self, lineups=(), injuries=()):
        self._lineups, self._injuries, self.calls = list(lineups), list(injuries), []

    def lineups(self, fixture_id, ttl=300):
        self.calls.append("lineups")
        return self._lineups

    def injuries(self, fixture_id):
        self.calls.append("injuries")
        return self._injuries

    def team_players(self, team_id, season):
        self.calls.append(f"players {team_id} {season}")
        if team_id == 10:
            return CURRENT if season == 2026 else PREVIOUS
        return [_item(200 + i, f"B{i}", "Defender" if i < 4 else "Attacker", 1, 0, season, team=20) for i in range(8)]


PASS_NOW = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)


def _fx(minutes):
    return {"fixture": {"id": 5, "timestamp": int((PASS_NOW + timedelta(minutes=minutes)).timestamp()),
                        "status": {"short": "NS"}},
            "league": {"id": 39, "season": 2026},
            "teams": {"home": {"id": 10, "name": "Alpha"}, "away": {"id": 20, "name": "Beta"}}}


def test_pass_uses_lineups_at_full_strength_near_kickoff():
    xi = [{"team": {"id": 10}, **_xi([2, 3, 4], bench=[5])},  # Star not in the squad
          {"team": {"id": 20}, **_xi([200 + i for i in range(8)])}]
    api = FakeApi(lineups=xi)
    e = agent_pass._absence_of(api, _fx(45), {}, PASS_NOW + timedelta(minutes=45), PASS_NOW)
    tp = team_players_index(CURRENT, PREVIOUS, 10, 39)
    assert e["effet"]["source"] == "compositions" and e["absents"] is None
    assert e["effet"]["domicile"] == pytest.approx(absence_factor(tp.shares[1], 0), abs=1e-4)
    assert "injuries" not in api.calls


def test_pass_uses_injuries_at_half_strength_and_keeps_them_for_the_day():
    inj = [{"team": {"id": 10}, "player": {"id": 1, "name": "Star", "type": "Missing Fixture", "reason": "Injury"}}]
    api = FakeApi(injuries=inj)
    e = agent_pass._absence_of(api, _fx(180), {}, PASS_NOW + timedelta(hours=3), PASS_NOW)
    tp = team_players_index(CURRENT, PREVIOUS, 10, 39)
    assert e["effet"]["source"] == "blessés"
    assert e["effet"]["domicile"] == pytest.approx(absence_factor(tp.shares[1], 0, 0.5), abs=1e-4)
    assert api.calls.count("injuries") == 1 and "lineups" not in api.calls
    assert not any(c.startswith("players 20") for c in api.calls)  # nobody listed for Beta: no request
    # a later pass of the day reads the stored list: no request at all
    api2 = FakeApi(injuries=inj)
    again = agent_pass._absence_of(api2, _fx(170), {"absents": e["absents"]}, PASS_NOW + timedelta(hours=3), PASS_NOW)
    assert again["effet"] == e["effet"] and api2.calls == [] and again["absents"] is None
    # an empty injuries list may mean no coverage: factor 1 (nothing applied)
    empty = agent_pass._absence_of(FakeApi(), _fx(180), {}, PASS_NOW + timedelta(hours=3), PASS_NOW)
    assert empty["effet"] is None and empty["absents"]["domicile"] == []
    assert agent_pass._absence_lookup({("Alpha", "Beta"): empty})(type("F", (), {"home": "Alpha", "away": "Beta"})) is None


def test_apply_writes_the_effect_and_its_sentence_once():
    effet = {"domicile": 0.88, "exterieur": 1.02, "source": "compositions",
             "phrases": ["Sans Star (40 % des buts et passes décisives), l'attaque de Alpha est revue à la baisse."]}
    docs = {"k": {"jour": "2026-10-11", "domicile": "Alpha", "exterieur": "Beta",
                  "contexte": {"phrases": ["Alpha reste sur 3 victoires."], "note": "Contexte affiché."}}}
    fixtures = [{"match": "Alpha - Beta", "absence_factors": [0.88, 1.02], "skipped_reason": None}]
    effects = {("Alpha", "Beta"): {"effet": effet, "absents": None}}
    agent_pass._apply_absences(effects, docs, fixtures, PASS_NOW)
    agent_pass._apply_absences(effects, docs, fixtures, PASS_NOW)
    ctx = docs["k"]["contexte"]
    assert ctx["absences_effet"]["domicile"] == 0.88 and ctx["absences_effet"]["source"] == "compositions"
    assert ctx["phrases"] == ["Alpha reste sur 3 victoires."] + effet["phrases"]
    assert ctx["note"].count("joueurs clés absents") == 1
    # priced again without information: the effect and its sentence are taken back
    agent_pass._apply_absences({}, docs, [{"match": "Alpha - Beta", "skipped_reason": None}], PASS_NOW)
    ctx = docs["k"]["contexte"]
    assert "absences_effet" not in ctx and ctx["phrases"] == ["Alpha reste sur 3 victoires."]
    assert ctx["note"] == "Contexte affiché."
