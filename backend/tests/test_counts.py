from datetime import datetime, timedelta, timezone

import pytest

from sportai.engine.football import counts
from sportai.services.comptages import price_counts, settle_count, stat_value
from sportai.services.live_page import stats_by_side


def test_feed_values_map_to_count_bets():
    assert counts.map_value("Corners Over Under", "Over 9.5") == ("corners", "total", "over", 9.5)
    assert counts.map_value("Away Team Total Cards", "Under 2.5") == ("cartons", "away", "under", 2.5)
    assert counts.map_value("Shots.1x2", "Draw") == ("tirs", "1x2", "draw", None)
    assert counts.map_value("Corners Over Under", "Over 10") is None  # whole line: refunds, left out
    assert counts.map_value("Corners Race To", "Draw 9") is None
    assert counts.map_value("Fouls. Total", "Under 24.5") == ("fautes", "total", "under", 24.5)
    assert counts.label("fautes", "away", "over", 11.5, "Lens", "Metz") == "Fautes Metz : plus de 11,5"
    assert counts.validated("fautes", "1x2", "away", None) and "fautes" in counts.REFEREE_STATS


def test_labels_settlement_and_validation():
    assert counts.label("corners", "total", "over", 9.5, "Lens", "Metz") == "Corners : plus de 9,5 dans le match"
    assert counts.label("cartons", "home", "under", 1.5, "Lens", "Metz") == "Cartons Lens : moins de 1,5"
    assert counts.label("tirs_cadres", "1x2", "away", None, "Lens", "Metz") == "Plus de tirs cadrés : Metz"
    assert counts.settle("total", "over", 9.5, 6, 4) == "gagne"
    assert counts.settle("away", "under", 4.5, 6, 5) == "perdu"
    assert counts.settle("1x2", "home", None, 5, 5) == "perdu"
    assert counts.validated("corners", "total", "over", 10.5) and not counts.validated("corners", "total", "over", 9.5)
    assert not counts.validated("corners", "total", "over", 13.5)  # never tested


def test_probabilities_add_up():
    p = [counts.win_prob("1x2", s, None, 5.2, 4.4, 10) for s in ("home", "draw", "away")]
    assert sum(p) == pytest.approx(1, abs=1e-6) and p[0] > p[2]
    over = counts.win_prob("total", "over", 9.5, 5.2, 4.4, 10, 60, 9.8)
    assert over + counts.win_prob("total", "under", 9.5, 5.2, 4.4, 10, 60, 9.8) == pytest.approx(1, abs=1e-6)


def test_cards_count_zero_when_the_feed_gives_null():
    raw = [{"team": {"id": 1}, "statistics": [{"type": "Yellow Cards", "value": None}, {"type": "Red Cards", "value": None},
                                              {"type": "Corner Kicks", "value": 7}]},
           {"team": {"id": 2}, "statistics": [{"type": "Yellow Cards", "value": 3}, {"type": "Red Cards", "value": 1},
                                              {"type": "Corner Kicks", "value": 2}]}]
    st = stats_by_side(raw, 1)
    assert st["jaunes"] == [0, 3] and st["rouges"] == [0, 1] and st["corners"] == [7, 2]
    doc = {"statut": "termine", "direct": {"stats": st}}
    leg = {"comptage": {"stat": "cartons", "type": "total", "selection": "over", "ligne": 3.5}}
    assert settle_count(leg, doc) == "gagne"  # 0 + 3 yellow + 1 red
    assert settle_count(leg, {"statut": "en_direct", "direct": {"stats": st}}) is None
    assert stat_value("cartons", {"corners": 3}) is None  # no card count known: not settled


def test_pricing_from_the_cache(tmp_path, monkeypatch):
    from sportai.services import comptages

    teams = ["A", "B", "C", "D"]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ms = []
    for i in range(400):
        h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
        if h == a:
            continue
        stats = {"home": {"corners": 6 if h == "A" else 4}, "away": {"corners": 3}}
        ms.append({"kickoff": start + timedelta(days=i), "home": h, "away": a, "stats": stats})
    monkeypatch.setattr(comptages, "history", lambda folder, day: {"FR1": ms})
    comptages.model.cache_clear()
    item = {"bookmakers": [{"name": "X", "bets": [
        {"name": "Corners Over Under", "values": [{"value": "Over 8.5", "odd": "1.80"}, {"value": "Under 8.5", "odd": "2.00"}]},
        {"name": "Home Corners Over/Under", "values": [{"value": "Over 4.5", "odd": "1.70"}]}]}]}
    rows = price_counts(item, "A", "B", "FR1", start + timedelta(days=401), str(tmp_path))
    comptages.model.cache_clear()
    by = {r["pari"]: r for r in rows}
    assert by["Corners A : plus de 4,5"]["proba"] > 0.6  # A always takes 6 corners at home
    assert by["Corners : plus de 8,5 dans le match"]["proba_marche_brute"] is False
    assert all(r["marche"] == "Corners" for r in rows)
    # statistics not refreshed for months: no price
    assert price_counts(item, "A", "B", "FR1", start + timedelta(days=600), str(tmp_path)) == []


def test_a_strict_referee_raises_the_cards_chances(tmp_path, monkeypatch):
    from sportai.services import comptages

    teams = ["A", "B", "C", "D"]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ms = []
    for i in range(400):
        h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
        if h == a:
            continue
        strict = i % 2 == 0
        stats = {"home": {"jaunes": 3 if strict else 1}, "away": {"jaunes": 3 if strict else 1}}
        ms.append({"kickoff": start + timedelta(days=i), "home": h, "away": a, "stats": stats,
                   "referee": "Paul Strict, France" if strict else "Luc Doux, France"})
    monkeypatch.setattr(comptages, "history", lambda folder, day: {"FR1": ms})
    comptages.model.cache_clear()
    item = {"bookmakers": [{"name": "X", "bets": [
        {"name": "Cards Over/Under", "values": [{"value": "Over 4.5", "odd": "1.90"}, {"value": "Under 4.5", "odd": "1.90"}]}]}]}
    now = start + timedelta(days=401)
    p = {ref: {r["pari"]: r for r in price_counts(item, "A", "B", "FR1", now, str(tmp_path), ref)}
         for ref in (None, "P. Strict", "Luc Doux, France", "Inconnu Total")}
    comptages.model.cache_clear()
    over = "Cartons : plus de 4,5 dans le match"
    assert p["P. Strict"][over]["proba"] > p[None][over]["proba"] > p["Luc Doux, France"][over]["proba"]
    assert p["P. Strict"][over]["arbitre"]["facteur"] > 1 and "arbitre" not in p[None][over]
    assert p["Inconnu Total"][over]["arbitre"]["facteur"] == 1.0
