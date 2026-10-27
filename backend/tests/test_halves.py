

from sportai.engine.football import halves
from sportai.services.mi_temps import price_halves
from sportai.services.pronostic import _settle_leg


def test_joint_law_and_full_time_agree():
    j = halves.joint(1.6, 1.1, 0.44)
    assert abs(j.sum() - 1) < 1e-9
    # the half results add up to one, and half-time / full-time sums to the half-time result
    assert abs(sum(halves.outcome(j, "ht_1x2", s).win for s in ("home", "draw", "away")) - 1) < 1e-9
    ht_home = halves.outcome(j, "ht_1x2", "home").win
    assert abs(sum(halves.outcome(j, "htft", f"home/{y}").win for y in ("home", "draw", "away")) - ht_home) < 1e-9
    # whole lines are refunded on the exact number
    o = halves.outcome(j, "ht_totals", "over", 1.0)
    assert o.push > 0 and o.win + o.push < 1


def test_settle_half_bets():
    assert halves.settle("ht_1x2", "home", None, 1, 0, 0, 2) == "gagne"
    assert halves.settle("htft", "home/away", None, 1, 0, 0, 2) == "gagne"
    assert halves.settle("2h_totals", "over", 1.5, 2, 2, 1, 0) == "perdu"
    assert halves.settle("ht_dnb", "home", None, 0, 0, 3, 0) == "rembourse"
    assert halves.settle("win_either_half_away", "yes", None, 0, 1, 2, 0) == "gagne"
    assert halves.settle("highest_half", "2nd", None, 0, 1, 2, 0) == "gagne"


def test_feed_values_are_mapped():
    assert halves.map_value("First Half Winner", "Home") == ("ht_1x2", "home", None)
    assert halves.map_value("Goals Over/Under - Second Half", "Over 1.5") == ("2h_totals", "over", 1.5)
    assert halves.map_value("Goal Line (1st Half)", "Under 2.25") is None  # quarter lines are not priced
    assert halves.map_value("HT/FT Double", "Draw/Away") == ("htft", "draw/away", None)
    assert halves.map_value("Win Both Halves", "Away") == ("win_both_halves_away", "yes", None)
    assert halves.label("ht_1x2", "home", None, "PSG", "OM") == "1re mi-temps : PSG gagne"


def test_price_halves_removes_the_margin_on_complete_groups():
    item = {"bookmakers": [{"name": "A", "bets": [
        {"name": "First Half Winner", "values": [{"value": "Home", "odd": "2.60"}, {"value": "Draw", "odd": "2.20"},
                                                 {"value": "Away", "odd": "4.33"}]},
        {"name": "To Win Either Half", "values": [{"value": "Home", "odd": "1.62"}]}]}]}
    rows = {(r["marche"], r["selection"]): r for r in price_halves(item, "PSG", "OM", (1.6, 1.1), "FR1")}
    s = 1 / 2.6 + 1 / 2.2 + 1 / 4.33
    assert abs(rows[("ht_1x2", "home")]["proba_marche"] - round((1 / 2.6) / s, 4)) < 1e-4
    assert rows[("ht_1x2", "home")]["valide"] and not rows[("ht_1x2", "draw")]["valide"]
    either = rows[("win_either_half_home", "yes")]
    assert either["proba_marche_brute"] and either["pari"] == "PSG gagne au moins une mi-temps"


def test_half_leg_settled_from_the_half_time_score():
    leg = {"match_id": "m", "pari": "1re mi-temps : PSG gagne", "mi_temps": {"marche": "ht_1x2", "selection": "home",
                                                                           "ligne": None}}
    page = {"m": {"statut": "termine", "score": "1 - 1", "score_mi_temps": "1 - 0", "domicile": "PSG",
                  "exterieur": "OM"}}
    assert _settle_leg(leg, page)["resultat"] == "gagne"
    assert "resultat" not in _settle_leg(leg, {"m": {**page["m"], "score_mi_temps": None}})
