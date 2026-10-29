from datetime import datetime, timedelta, timezone

import pytest

from sportai.services import pronostic
from sportai.services.pronostic import build_pronostic


@pytest.fixture(autouse=True)
def legacy_tickets(request, monkeypatch):
    """The combined tickets of before the one-bet-per-match rule, still tested on their own path."""
    if "top_pick" not in request.node.name:
        monkeypatch.setattr(pronostic, "PER_MATCH_FROM", "2099-01-01")

NOW = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)


def leg(match, pari, cote, proba, marche_p, hours=4):
    return {"match_id": f"2026-10-11-{match}", "match": match, "competition": "Ligue 1",
            "coup_envoi": (NOW + timedelta(hours=hours)).isoformat(), "pari": pari, "marche": "Résultat du match",
            "cote": cote, "bookmaker": "winamax_fr", "proba": proba, "proba_marche": marche_p,
            "valeur": round(proba * cote - 1, 4), "valide": True, "_divergence_max": 0.10}


LEGS = [leg("a-b", "Victoire a", 1.70, 0.66, 0.58), leg("c-d", "Victoire c", 1.60, 0.64, 0.56),
        leg("e-f", "Victoire f", 2.80, 0.42, 0.34), leg("g-h", "Victoire g", 3.0, 0.40, 0.31),
        leg("i-j", "Victoire i", 1.40, 0.70, 0.69),  # negative value: never proposed
        leg("k-l", "Victoire k", 2.5, 0.60, 0.38)]  # model and market too far apart: never proposed


def test_top3_and_tickets():
    doc = build_pronostic(LEGS, {}, None, NOW, "matin")
    assert [l["match"] for l in doc["top3"]] == ["g-h", "e-f", "a-b"]
    safe, semi, fun = (doc["tickets"][k] for k in ("safe", "semi_safe", "fun"))
    # Safe: always a single bet above 1.65 (owner, 2026-10-15): a-b at 1.70.
    assert [l["match"] for l in safe["jambes"]] == ["a-b"] and safe["cote_totale"] == 1.7
    assert safe["message"] is None
    # A bet goes in one ticket only (owner, 2026-10-15): without a-b, no semi-safe pair reaches 1.90 at 30 %.
    assert semi["jambes"] == [] and semi["message"].startswith("Pas de top idée semi safe")
    # Fun: c-d and e-f (4.48); a third bet would have to be a-b again, so it says it is short.
    assert {l["match"] for l in fun["jambes"]} == {"c-d", "e-f"} and fun["cote_totale"] == 4.48
    assert fun["message"].startswith("Pas de combiné de 3 paris") and "2 paris atteignent" in fun["message"]
    picked = [(l["match"], l["pari"]) for t in (safe, semi, fun) for l in t["jambes"]]
    assert len(picked) == len(set(picked))
    assert not any(k.startswith("_") for t in (safe, semi, fun) for l in t["jambes"] for k in l)
    assert "i-j" not in str(doc) and "k-l" not in str(doc)


def test_nothing_correct_says_so():
    doc = build_pronostic([LEGS[4], LEGS[5]], {}, None, NOW, "matin")
    assert doc["top3"] == [] and "Aucune cote correcte" in doc["message"]
    for t in doc["tickets"].values():
        assert t["jambes"] == [] and t["cote_totale"] is None and t["message"].startswith("Pas de top idée")


def test_ticket_frozen_after_first_kickoff_then_settled():
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    later = NOW + timedelta(hours=5)
    page = {"2026-10-11-a-b": {"statut": "termine", "score": "2 - 0", "domicile": "a", "exterieur": "b"},
            "2026-10-11-c-d": {"statut": "termine", "score": "1 - 1", "domicile": "c", "exterieur": "d"},
            "2026-10-11-e-f": {"statut": "termine", "score": "0 - 1", "domicile": "e", "exterieur": "f"}}
    doc = build_pronostic(LEGS, page, first, later, "resultats")
    safe = doc["tickets"]["fun"]
    assert [l["pari"] for l in safe["jambes"]] == [l["pari"] for l in first["tickets"]["fun"]["jambes"]]
    assert all(l["verrouille"] for l in safe["jambes"])
    assert {l["resultat"] for l in safe["jambes"]} == {"gagne", "perdu"} and safe["resultat"] == "perdu"


def test_bankroll_and_bilan():
    from sportai.services.bilan import apply_stakes, bankroll_settings, build_bilan, replay
    from sportai.services.dashboard import likely_score, settle_score

    settings = bankroll_settings(None)
    assert settings["depart"] == 100 and settings["objectif"] == 100000
    first = apply_stakes(build_pronostic(LEGS, {}, None, NOW, "matin"), settings, 100.0)
    assert first["tickets"]["safe"]["mise"] == 3.0 and first["tickets"]["fun"]["mise"] == 1.0
    page = {"2026-10-11-a-b": {"statut": "termine", "score": "2 - 0", "domicile": "a", "exterieur": "b", "jour": "2026-10-11",
                               "score_estime": {"score": "1 - 0", "domicile": 1, "exterieur": 0, "proba": 0.12}},
            "2026-10-11-c-d": {"statut": "termine", "score": "1 - 1", "domicile": "c", "exterieur": "d"},
            "2026-10-11-e-f": {"statut": "termine", "score": "0 - 1", "domicile": "e", "exterieur": "f"}}
    settle_score(page["2026-10-11-a-b"], 2, 0)
    # judged on the outcome: 1-0 estimated, 2-0 final is a home win called right, not the exact score
    assert page["2026-10-11-a-b"]["score_estime"]["resultat"] == "reussi"
    assert page["2026-10-11-a-b"]["score_estime"]["score_exact"] is False
    day = build_pronostic(LEGS, page, first, NOW + timedelta(hours=6), "resultats")
    assert day["tickets"]["safe"]["mise"] == 3.0  # frozen with its stake
    bank, history = replay({"2026-10-11": day}, settings)
    # safe a-b won at 1.70 (+2.10), no semi-safe ticket, fun c-d + e-f lost (-1.00)
    assert [h["gain"] for h in history] == [2.1, -1.0] and bank == 101.1
    bilan = build_bilan(page, {"2026-10-11": day}, settings, NOW)
    assert bilan["bankroll"]["actuelle"] == 101.1 and bilan["par_type"]["safe"]["taux"] == 1.0
    assert bilan["par_type"]["score"]["valides"] == 1 and bilan["global"] == {k: v for k, v in bilan["positions"].items() if k != "par_niveau"}
    assert likely_score({"expected_goals": [1.4, 0.9]})["score"] == "1 - 0"
    assert likely_score({"likely_score": [1, 1, 0.13]})["proba"] == 0.13
    # A run stored with the old modal 1-1 while its own probabilities favour the home side: the outcome decides.
    old_run = {"likely_score": [1, 1, 0.1079], "expected_goals": [1.9, 1.0],
               "probabilities": {"1x2:home": 0.5514, "1x2:draw": 0.2315, "1x2:away": 0.2171}}
    est = likely_score(old_run)
    assert est["domicile"] > est["exterieur"]
    agree = {**old_run, "probabilities": {"1x2:home": 0.30, "1x2:draw": 0.40, "1x2:away": 0.30}}
    assert likely_score(agree)["score"] == "1 - 1"


def test_tickets_fixed_45_min_before_then_bonus():
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    assert not any(t.get("fige") for t in first["tickets"].values())
    m45 = build_pronostic(LEGS, {}, first, NOW + timedelta(hours=3, minutes=15), "m45")
    assert all(t["fige"] for t in m45["tickets"].values() if t["jambes"]) and m45["tickets"]["safe"]["message"].startswith("Figée")
    # Better bets found later do not change the fixed tickets: they make bonus tickets, apart.
    later = [*LEGS, leg("m-n", "Victoire m", 1.95, 0.80, 0.75, hours=8), leg("o-p", "Victoire o", 1.60, 0.78, 0.74, hours=8),
             leg("q-r", "Victoire q", 1.90, 0.79, 0.74, hours=8), leg("s-t", "Victoire s", 1.62, 0.77, 0.73, hours=8)]
    doc = build_pronostic(later, {}, m45, NOW + timedelta(hours=3, minutes=30), "h3")
    assert all(doc["tickets"][k]["jambes"] == m45["tickets"][k]["jambes"] for k in doc["tickets"] if m45["tickets"][k]["jambes"])
    # the semi-safe ticket, still empty, is filled late; the bonus safe ticket takes other bets than every ticket
    assert {l["match"] for l in doc["tickets"]["semi_safe"]["jambes"]} == {"m-n", "o-p"}
    safe = doc["bonus"]["safe"]
    assert {l["match"] for l in safe[-1]["jambes"]} == {"q-r"} and not safe[-1]["fige"]
    assert safe[-1]["titre"] == "Idée safe" and all(b["fige"] for b in safe[:-1])


def test_match_odds_1x2_prefers_winamax():
    from types import SimpleNamespace as R
    from sportai.services.dashboard import match_odds_1x2
    rows = [R(event_label="m", bookmaker=b, selection=s, odds=o) for b, s, o in
            [("betclic_fr", "home", 2.25), ("betclic_fr", "draw", 3.55), ("betclic_fr", "away", 2.9),
             ("winamax_fr", "home", 2.2), ("winamax_fr", "draw", 3.6), ("winamax_fr", "away", 2.8),
             ("pmu_fr", "home", 2.2)]]
    assert match_odds_1x2(rows) == {"m": {"domicile": 2.2, "nul": 3.6, "exterieur": 2.8, "bookmaker": "Winamax"}}
    assert match_odds_1x2(rows[:3])["m"]["bookmaker"] == "Betclic"


def test_empty_safe_filled_by_the_15_min_pass_when_odds_rise():
    # 45 min before: Atalanta at 1.64, no safe ticket; 15 min before: 1.67, the safe ticket is made and fixed.
    low = [leg("x-y", "Victoire x", 1.64, 0.74, 0.66, hours=1)]
    m45 = build_pronostic(low, {}, None, NOW + timedelta(minutes=15), "m45")
    assert m45["tickets"]["safe"]["jambes"] == []
    high = [leg("x-y", "Victoire x", 1.67, 0.74, 0.66, hours=1)]
    m15 = build_pronostic(high, {}, m45, NOW + timedelta(minutes=45), "m15")
    safe = m15["tickets"]["safe"]
    assert [l["cote"] for l in safe["jambes"]] == [1.67] and safe["fige"]
    assert safe["message"].startswith("Ajoutée au dernier passage")


def test_champions_league_bets_combine_half_stake():
    from sportai.services.bilan import apply_stakes

    u1 = {**leg("ucl1", "Victoire u", 1.60, 0.74, 0.70), "ucl": True}
    u2 = {**leg("ucl2", "Victoire w", 1.55, 0.76, 0.72), "ucl": True}
    draw = {**leg("ucl3", "Match nul", 3.4, 0.33, 0.29), "ucl": True, "valide": False}
    doc = build_pronostic([u1, u2, draw], {}, None, NOW, "h3")
    assert all(l["pari"] != "Match nul" for t in doc["tickets"].values() for l in t["jambes"])
    # several Champions League matches in one ticket since 2026-10-15 (owner: Liverpool with Aston Villa)
    assert {l["match"] for l in doc["tickets"]["semi_safe"]["jambes"]} == {"ucl1", "ucl2"}
    assert doc["tickets"]["safe"]["jambes"] == [] and "pari simple" in doc["tickets"]["safe"]["message"]
    settings = {"mises": {"safe": 0.04, "semi_safe": 0.02, "fun": 0.01}}
    apply_stakes(doc, settings, 1000.0)
    assert doc["tickets"]["semi_safe"]["mise"] == 10.0


def test_safe_ticket_on_confidence_2026_10_14():
    """City at 55 % for the model but 48 % for the market made the safe ticket of 2026-10-14 (lost 2-4); Liverpool
    and Aston Villa, favourites for both but rated over 10 points higher by the model, were left out."""
    ucl = lambda *a: {**leg(*a), "ucl": True, "competition": "Ligue des champions"}
    city = ucl("city-psg", "Victoire Manchester City", 2.21, 0.553, 0.4773)
    villa = ucl("villa-fen", "Victoire Aston Villa", 1.74, 0.7177, 0.5749)
    pool = ucl("lask-liv", "Victoire Liverpool", 1.38, 0.8414, 0.6925)
    over = {**ucl("villa-fen", "Plus de 1,5 buts", 1.20, 0.8463, 0.8228), "marche": "Nombre de buts",
            "valide": False, "validation": "non validé"}
    over["valeur"] = 0.0156  # below the 3 % asked of a bet on trial: not taken
    over2 = {**over, "cote": 1.25, "valeur": round(0.8463 * 1.25 - 1, 4)}
    doc = build_pronostic([city, villa, pool, over2], {}, None, NOW, "h3")
    safe = doc["tickets"]["safe"]
    # a single above 1.65 since the same day: Aston Villa, the favourite of both (won 6-0)
    assert {l["pari"] for l in safe["jambes"]} == {"Victoire Aston Villa"}
    assert safe["cote_totale"] == 1.74 and safe["confiance"] >= 0.55
    # any market may make the safe single with positive value and a strong chance (owner, 2026-10-15)
    goals = {**over, "pari": "Plus de 2,5 buts", "cote": 1.80, "proba": 0.70, "proba_marche": 0.62, "valeur": 0.26}
    assert build_pronostic([goals], {}, None, NOW, "h3")["tickets"]["safe"]["jambes"] != []
    hcap = {**goals, "marche": "Handicap asiatique", "pari": "k handicap asiatique -1"}
    assert build_pronostic([hcap], {}, None, NOW, "h3")["tickets"]["safe"]["jambes"] != []
    weak = {**hcap, "proba": 0.54, "proba_marche": 0.52, "valeur": round(0.54 * 1.80 - 1, 4)}
    assert build_pronostic([weak], {}, None, NOW, "h3")["tickets"]["safe"]["jambes"] == []
    assert "Victoire Manchester City" not in {l["pari"] for l in safe["jambes"]}
    # Aston Villa is in the safe ticket, so the semi-safe one may not take it again (owner, 2026-10-15)
    assert "Victoire Aston Villa" not in {l["pari"] for l in doc["tickets"]["semi_safe"]["jambes"]}
    # a coin flip the model rates far above the market is still left out
    far = leg("k-l", "Victoire k", 2.5, 0.60, 0.38)
    assert "k-l" not in str(build_pronostic([far], {}, None, NOW, "h3"))


def test_one_bankroll_per_ticket_kind():
    """Owner 2026-10-15: one gauge per kind of ticket, so the fun tickets do not drag the safe ones."""
    from sportai.services.bilan import apply_stakes, build_bilan, replay_by_ticket

    settings = {"depart": 1000.0, "objectif": 100000.0, "mises": {"safe": 0.03, "semi_safe": 0.02, "fun": 0.01}}
    won = lambda cote, mise: {"titre": "t", "jambes": [{"cote": cote, "resultat": "gagne", "pari": "p", "match": "m"}], "resultat": "gagne",
                              "cote_totale": cote, "mise": mise}
    lost = lambda mise: {"titre": "t", "jambes": [{"cote": 3.0, "resultat": "perdu", "pari": "p", "match": "m"}], "resultat": "perdu",
                         "cote_totale": 3.0, "mise": mise}
    days = {"2026-10-11": {"tickets": {"safe": won(1.5, 30.0), "fun": lost(10.0)}},
            "2026-10-12": {"tickets": {"safe": won(1.6, 30.45), "semi_safe": lost(20.0), "fun": lost(10.0)}}}
    banks = {k: b for k, (b, _) in replay_by_ticket(days, settings).items()}
    # the start split in three (owner: "divise par trois"), stakes re-sized on each kind's own bankroll
    assert banks == {"safe": 344.42, "semi_safe": 326.66, "fun": 326.7}
    doc = apply_stakes({"tickets": {"safe": {"jambes": [{"cote": 1.5}]}, "fun": {"jambes": [{"cote": 3.0}]}}},
                       settings, banks)
    assert doc["tickets"]["safe"]["mise"] == 10.33 and doc["tickets"]["fun"]["mise"] == 3.27
    per = build_bilan({}, days, settings, NOW)["bankroll"]["par_ticket"]
    assert per["safe"]["actuelle"] == 344.42 and per["safe"]["depart"] == 333.33


def test_bonus_tickets_have_their_own_bankroll():
    """Owner 2026-10-17: the bonus tickets get a gauge apart, with the ones won and lost, and never touch the others."""
    from sportai.services.bilan import build_bilan

    settings = {"depart": 1000.0, "objectif": 100000.0, "mises": {"safe": 0.03, "semi_safe": 0.02, "fun": 0.01}}
    ticket = lambda res, cote, **kw: {"titre": "t", "jambes": [{"cote": cote, "resultat": res, "pari": "p", "match": "m",
                                                                **kw}], "resultat": res, "cote_totale": cote, "mise": 10.0}
    bonus = lambda res, cote, fige=True, **kw: {**ticket(res, cote, **kw), "titre": "Ticket bonus x", "fige": fige}
    days = {"2026-10-11": {"tickets": {"safe": ticket("gagne", 1.5)},
                           "bonus": {"safe": [bonus("gagne", 2.0)], "fun": [bonus("perdu", 4.0)]}},
            "2026-10-12": {"tickets": {"safe": ticket("perdu", 1.5)},
                           "bonus": {"semi_safe": [bonus("gagne", 2.0, valide=False), bonus("perdu", 2.0, fige=False)],
                                     "fun": [{**bonus("en_cours", 3.0), "resultat": "en_cours"}]}}}
    without = build_bilan({}, {d: {k: v for k, v in doc.items() if k != "bonus"} for d, doc in days.items()}, settings,
                          NOW)
    out = build_bilan({}, days, settings, NOW)
    bonus_bank = out["bankroll"].pop("bonus")
    # safe +30 and fun -10 on 1000; then semi safe on trial at half its 2 % of 1020, won at 2.0: +10.2
    assert bonus_bank["actuelle"] == 1030.2 and bonus_bank["depart"] == 1000.0 and bonus_bank["titre"] == "La boîte à idées"
    assert [(h["jour"], h["type"], h["mise"], h["resultat"]) for h in bonus_bank["historique"]] == [
        ("2026-10-11", "safe", 30.0, "gagne"), ("2026-10-11", "fun", 10.0, "perdu"),
        ("2026-10-12", "semi_safe", 10.2, "gagne")]
    # the official bankrolls and the totals are the same with or without the bonus tickets
    without["bankroll"].pop("bonus")
    # "La boîte à idées" (owner 2026-10-17): 2 ideas won, 1 lost; the open and unfixed ones are not counted
    ideas = out.pop("boite_a_idees")
    assert (ideas["gagnees"], ideas["perdues"], ideas["taux_reussite"]) == (2, 1, 0.6667)
    assert ideas["par_type"]["fun"]["perdues"] == 1 and ideas["par_type"]["safe"]["taux_reussite"] == 1.0
    assert without.pop("boite_a_idees")["taux_reussite"] is None
    assert {k: v for k, v in out.items() if k != "maj"} == {k: v for k, v in without.items() if k != "maj"}


def test_two_both_teams_score_make_a_ticket():
    """Owner, 2026-10-15: two "both teams score" were enough for a good ticket on 2026-10-14; goal bets are not
    limited to one per ticket like the other markets on trial."""
    btts = lambda m, cote, p, pm: {**leg(m, "Les deux équipes marquent : oui", cote, p, pm), "valide": False,
                                   "marche": "Les deux équipes marquent", "validation": "non validé"}
    doc = build_pronostic([btts("roma-real", 1.52, 0.70, 0.65), btts("betis-porto", 1.62, 0.65, 0.60)], {}, None, NOW,
                          "h3")
    assert {l["match"] for l in doc["tickets"]["semi_safe"]["jambes"]} == {"roma-real", "betis-porto"}


def test_a_ticket_fixed_earlier_keeps_its_bets_out_of_the_others():
    # 2026-10-16: the fun ticket, fixed first, and the semi safe built after it shared a bet.
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    prev = {**first, "tickets": {**first["tickets"], "safe": {"jambes": []},
                                 "fun": {**first["tickets"]["fun"], "fige": True, "jambes": [LEGS[0], LEGS[1]]}}}
    doc = build_pronostic(LEGS, {}, prev, NOW, "h3")
    assert [l["match"] for l in doc["tickets"]["fun"]["jambes"]] == ["a-b", "c-d"]
    picked = [(l["match"], l["pari"]) for t in doc["tickets"].values() for l in t["jambes"]]
    assert len(picked) == len(set(picked))


def test_a_bonus_fixed_earlier_keeps_its_bets_out_of_the_official_tickets():
    # 2026-10-17: the semi safe, still open, took the bet of a fixed bonus fun ticket.
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    taken = [{**l} for t in first["tickets"].values() for l in t["jambes"]]
    prev = {**first, "tickets": {k: {"jambes": []} for k in first["tickets"]},
            "bonus": {"fun": [{"titre": "Ticket bonus fun", "fige": True, "jambes": taken}]}}
    doc = build_pronostic(LEGS, {}, prev, NOW, "h3")
    official = {(l["match"], l["pari"]) for t in doc["tickets"].values() for l in t["jambes"]}
    assert not official & {(l["match"], l["pari"]) for l in taken}


def test_a_new_bonus_shares_no_bet_with_a_bonus_of_another_kind_fixed_earlier():
    # 2026-10-17: a new bonus semi safe took the bet of a bonus fun ticket fixed earlier.
    other = leg("x-y", "Victoire x", 1.75, 0.66, 0.58)
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    prev = {**first, "tickets": {**first["tickets"], "safe": {**first["tickets"]["safe"], "fige": True,
                                                             "jambes": [other]}},
            "bonus": {"fun": [{"titre": "Ticket bonus fun", "fige": True, "jambes": [LEGS[0]]}]}}
    doc = build_pronostic(LEGS, {}, prev, NOW, "h3")
    assert all(("a-b", "Victoire a") not in {(l["match"], l["pari"]) for l in t["jambes"]}
               for t in doc["bonus"].get("safe", []))


def test_no_ticket_leg_under_the_odds_floor():
    # Owner, 2026-10-22: no proposal under 1.50 anywhere ("Moins de 4,5 buts" at 1.28 and 1.17 in the fun ticket).
    doc = build_pronostic([leg("a-b", "Moins de 4,5 buts", 1.28, 0.85, 0.80), *LEGS], {}, None, NOW, "h3")
    assert all(l["cote"] >= 1.50 for t in doc["tickets"].values() for l in t["jambes"])


def test_needs_effort_markets():
    from sportai.services.pronostic import needs_effort

    assert needs_effort("1x2", "home", None) == "home" and needs_effort("1x2", "draw", None) is None
    assert needs_effort("dnb", "away", None) == "away"
    assert needs_effort("double_chance", "1X", None) is None
    assert needs_effort("asian_handicap", "home", -1.5) == "home" and needs_effort("asian_handicap", "home", 0.5) is None
    assert needs_effort("asian_handicap", "away", 1.0) == "away"  # the line is the home team's: away -1
    assert needs_effort("asian_handicap", "away", -1.0) is None  # away +1
    assert needs_effort("euro_handicap", "home", -1.0) == "home" and needs_effort("euro_handicap", "home", 1.0) is None
    assert needs_effort("euro_handicap", "away", 0.0) == "away" and needs_effort("euro_handicap", "away", -1.0) is None
    assert needs_effort("team_total_home", "over", 1.5) == "home" and needs_effort("team_total_home", "over", 0.5) is None
    assert needs_effort("team_total_away", "under", 1.5) is None
    assert needs_effort("result_total", "away_over", 2.5) == "away" and needs_effort("result_btts", "draw_yes", None) is None
    assert needs_effort("win_to_nil_home", "yes", None) == "home" and needs_effort("win_to_nil_home", "no", None) is None
    assert needs_effort("exact_goals_away", "2+", None) == "away" and needs_effort("exact_goals_away", "1", None) is None
    assert needs_effort("exact_score", "2:1", None) == "home" and needs_effort("exact_score", "1:1", None) is None
    assert needs_effort("totals", "over", 2.5) is None and needs_effort("btts", "yes", None) is None


def test_high_rotation_risk_keeps_win_bets_out_of_the_tickets_and_ideas():
    # Owner, 2026-10-26: a team that plays a bigger match soon may hold back tonight.
    win_a = {**leg("a-b", "Victoire a", 1.70, 0.66, 0.58), "_exige": "home"}
    dc_a = leg("a-b", "a ou nul", 1.55, 0.72, 0.66)  # no win needed: kept
    legs = [win_a, dc_a, *LEGS[1:]]
    nxt = {"equipe": "a", "risque_rotation": "élevé",
           "phrase": "a joue z 3 jours après (Ligue des champions, match plus important) : risque de rotation élevé."}
    page = {"2026-10-11-a-b": {"contexte": {"prochain_match": {"domicile": nxt}}}}
    doc = build_pronostic(legs, page, None, NOW, "matin")
    picked = [(l["match"], l["pari"]) for t in doc["tickets"].values() for l in t["jambes"]]
    assert ("a-b", "Victoire a") not in picked and ("a-b", "a ou nul") in picked
    assert nxt["phrase"] in doc["message"] and "écartés des idées" in doc["message"]
    assert all(l["cote"] >= 1.50 for t in doc["tickets"].values() for l in t["jambes"])
    # a moderate risk, or the other team's risk, changes nothing
    for other in ({"domicile": {**nxt, "risque_rotation": "modéré"}}, {"exterieur": nxt}):
        page = {"2026-10-11-a-b": {"contexte": {"prochain_match": other}}}
        doc = build_pronostic(legs, page, None, NOW, "matin")
        assert ("a-b", "Victoire a") in [(l["match"], l["pari"]) for t in doc["tickets"].values() for l in t["jambes"]]
        assert "écartés" not in doc["message"]


def test_one_bet_per_match_and_top_pick_from_2026_10_28(monkeypatch):
    # the top pick and the combos, switched off by the owner on 2026-10-28, stay tested with the rules they had
    monkeypatch.setattr(pronostic, "SELECTIONS_PER_DAY", 1)
    monkeypatch.setattr(pronostic, "COMBOS", True)
    monkeypatch.setattr(pronostic, "PICK_ODDS", (1.50, None))
    monkeypatch.setattr(pronostic, "FORCE_CHOICE", False)
    from sportai.services.bilan import bankroll_settings, build_bilan

    now = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)

    def l(match, pari, cote, proba, pm, hours=4):
        return {**leg(match, pari, cote, proba, pm, hours), "match_id": f"2026-10-28-{match}",
                "coup_envoi": (now + timedelta(hours=hours)).isoformat(), "_p_cmp": proba}

    legs = [l("a-b", "Victoire a", 1.70, 0.64, 0.60), l("a-b", "Plus de 2,5 buts", 1.95, 0.56, 0.52),
            l("c-d", "Victoire c", 1.60, 0.68, 0.63), l("e-f", "Victoire f", 2.10, 0.52, 0.48),
            l("g-h", "Victoire g", 2.60, 0.45, 0.40),  # odds above 2.20: no bet on g-h
            l("i-j", "Victoire i", 1.80, 0.66, 0.54),  # model 12 points above the bookmakers: no bet
            l("k-l", "Victoire k", 1.45, 0.70, 0.68), l("m-n", "Victoire m", 1.65, 0.66, 0.62),
            {**l("o-p", "p : moins de 1,5 buts", 1.70, 0.66, 0.62)},  # a team's under: paused
            l("q-r", "Victoire q", 2.80, 0.53, 0.50)]  # no upper limit when the chance is there
    doc = build_pronostic(legs, {}, None, now, "matin")
    assert "tickets" not in doc and "top3" not in doc
    picks = doc["paris_retenus"]
    assert picks["2026-10-28-a-b"]["pari"] == "Victoire a"  # the most likely of the match
    assert picks["2026-10-28-a-b"]["niveau"] == "Confiance élevée"
    assert picks["2026-10-28-e-f"]["niveau"] == "Confiance moyenne"
    assert picks["2026-10-28-q-r"]["pari"] == "Victoire q"
    for mid in ("g-h", "i-j", "o-p", "k-l"):  # k-l: odds of 1.45, under 1.50
        assert picks[f"2026-10-28-{mid}"]["pari"] is None and picks[f"2026-10-28-{mid}"]["raison"]
    # the day's top pick: the most likely bet
    assert [(s["match"], s["titre"]) for s in doc["selections"]] == [("c-d", "Top confiance du jour")]
    assert not any(k.startswith("_") for p in picks.values() for k in p)
    # the day's combo: the two most likely bets of two other matches, never the Top confiance's match
    combos = doc["combines"]
    assert all(len(c["jambes"]) == 2 and len({l["match_id"] for l in c["jambes"]}) == 2 for c in combos)
    in_combos = [l["match"] for c in combos for l in c["jambes"]]
    assert "c-d" not in in_combos and len(in_combos) == len(set(in_combos))
    # every pair is kept (owner 2026-10-27 15:27), the most likely one is the day's selected combo
    assert [{l["match"] for l in c["jambes"]} for c in combos] == [{"m-n", "a-b"}, {"e-f", "q-r"}]
    assert [c["retenu"] for c in combos] == [True, False]  # e-f + q-r: Confiance moyenne, never the selected one
    assert all(l["niveau"] == "Confiance élevée" for l in combos[0]["jambes"])
    assert all("resultat" not in c for c in combos)

    # 45 min before kick-off the bet is fixed; once the match is over it is settled and counted in the bilan
    later = now + timedelta(hours=6)
    page = {"2026-10-28-c-d": {"statut": "termine", "score": "2 - 0", "domicile": "c", "exterieur": "d",
                               "jour": "2026-10-28"}}
    moved = [{**x, "proba": 0.40, "_p_cmp": 0.40} if x["match"] == "c-d" else x for x in legs]
    doc2 = build_pronostic(moved, page, doc, later, "resultats")
    cd = doc2["paris_retenus"]["2026-10-28-c-d"]
    assert cd["pari"] == "Victoire c" and cd["fige"] and cd["resultat"] == "gagne"
    assert any(s["match"] == "c-d" and s["resultat"] == "gagne" for s in doc2["selections"])
    assert doc2["combines"][0]["fige"] and [l["pari"] for l in doc2["combines"][0]["jambes"]] == [
        l["pari"] for l in doc["combines"][0]["jambes"]]
    bilan = build_bilan(page, {"2026-10-28": doc2}, bankroll_settings(None), later)
    assert bilan["par_type"]["pari_retenu"]["valides"] >= 1 and bilan["par_type"]["selection_jour"]["valides"] == 1


def test_top_pick_only_among_high_confidence_bets():
    # owner 2026-10-27 14:56: the best single is picked only among "Confiance élevée" bets, none otherwise
    now = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)

    def l(match, pari, cote, proba, pm):
        return {**leg(match, pari, cote, proba, pm, 4), "match_id": f"2026-10-28-{match}",
                "coup_envoi": (now + timedelta(hours=4)).isoformat(), "_p_cmp": proba}

    doc = build_pronostic([l("e-f", "Victoire f", 2.10, 0.52, 0.48), l("q-r", "Victoire q", 1.80, 0.58, 0.56)],
                          {}, None, now, "matin")
    assert {p["niveau"] for p in doc["paris_retenus"].values() if p["pari"]} == {"Confiance moyenne"}
    assert doc["selections"] == []


def test_top_pick_selected_combo_needs_two_high_confidence_bets(monkeypatch):
    # the top pick and the combos, switched off by the owner on 2026-10-28, stay tested with the rules they had
    monkeypatch.setattr(pronostic, "SELECTIONS_PER_DAY", 1)
    monkeypatch.setattr(pronostic, "COMBOS", True)
    monkeypatch.setattr(pronostic, "PICK_ODDS", (1.50, None))
    now = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)

    def l(match, pari, cote, proba, pm):
        return {**leg(match, pari, cote, proba, pm, 4), "match_id": f"2026-10-28-{match}",
                "coup_envoi": (now + timedelta(hours=4)).isoformat(), "_p_cmp": proba}

    doc = build_pronostic([l("c-d", "Victoire c", 1.60, 0.68, 0.63), l("a-b", "Victoire a", 1.70, 0.64, 0.60),
                           l("e-f", "Victoire f", 2.10, 0.52, 0.48)], {}, None, now, "matin")
    assert [s["match"] for s in doc["selections"]] == ["c-d"]
    assert [c["retenu"] for c in doc["combines"]] == [False]  # a-b (élevée) + e-f (moyenne): kept in the total only


def test_no_both_teams_no_and_no_under_half_a_goal():
    from sportai.services.pronostic import pick_ok

    base = {"cote": 1.80, "proba": 0.62, "proba_marche": 0.60}
    assert pick_ok({**base, "pari": "Plus de 2,5 buts"})
    assert not pick_ok({**base, "pari": "Les deux équipes marquent : non"})
    assert not pick_ok({**base, "pari": "Moins de 2,5 buts et les deux équipes marquent : non"})
    half = {"marche": "ht_team_total_away", "selection": "under", "ligne": 0.5}
    sure = {"cote": 1.62, "proba": 0.72, "proba_marche": 0.71}  # half-time bets need 70 % (owner 2026-10-28)
    assert not pick_ok({**sure, "pari": "1re mi-temps : Lazio moins de 0,5 buts", "mi_temps": half})
    assert pick_ok({**sure, "pari": "1re mi-temps : Lazio plus de 0,5 buts", "mi_temps": {**half, "selection": "over"}})
    assert pick_ok({**sure, "pari": "1re mi-temps : Lazio moins de 1,5 buts", "mi_temps": {**half, "ligne": 1.5}})
    assert not pick_ok({**base, "pari": "1re mi-temps : Lazio plus de 0,5 buts", "mi_temps": {**half, "selection": "over"}})
    assert not pick_ok({**base, "pari": "Moins de 0,5 buts"})


def test_result_and_goals_before_counts():
    from sportai.services.pronostic import _best, top_ok

    goal = {"pari": "Plus de 2,5 buts", "cote": 1.80, "proba": 0.58, "proba_marche": 0.56}
    cards = {"pari": "Cartons : moins de 4,5", "cote": 1.62, "proba": 0.66, "proba_marche": 0.65,
             "comptage": {"stat": "cartons"}}
    assert _best([goal, cards]) is goal  # 65.5 % is not 10 points surer than 57 %
    sure = {**cards, "proba": 0.70, "proba_marche": 0.69}
    assert _best([goal, sure]) is sure
    assert _best([cards]) is cards and _best([{**cards, "proba": 0.58, "proba_marche": 0.58}]) is None
    assert not top_ok(cards) and top_ok(goal)


def test_match_bets_from_1_50_but_top_pick_from_1_60(monkeypatch):
    monkeypatch.setattr(pronostic, "SELECTIONS_PER_DAY", 1)
    now = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
    lg = {**leg("k-l", "Moins de 3,5 buts", 1.55, 0.72, 0.68, 4), "match_id": "2026-10-28-k-l",
          "coup_envoi": (now + timedelta(hours=4)).isoformat(), "_p_cmp": 0.72}
    doc = build_pronostic([lg], {}, None, now, "matin")
    assert doc["paris_retenus"]["2026-10-28-k-l"]["pari"] == "Moins de 3,5 buts"  # owner 2026-10-28: from 1.50
    assert doc["selections"] == []  # the Top confiance stays at 1.60 or more


def test_singles_from_1_50_to_2_30_without_top_pick_nor_combos():
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)

    def l(match, pari, cote, proba, pm):
        return {**leg(match, pari, cote, proba, pm, 4), "match_id": f"2026-10-28-{match}",
                "coup_envoi": (now + timedelta(hours=4)).isoformat(), "_p_cmp": proba}

    doc = build_pronostic([l("a-b", "Victoire a", 1.70, 0.70, 0.64), l("c-d", "Victoire c", 1.65, 0.68, 0.63),
                           l("e-f", "Victoire e", 2.45, 0.50, 0.46)], {}, None, now, "matin")
    picks = doc["paris_retenus"]
    assert picks["2026-10-28-a-b"]["pari"] and picks["2026-10-28-c-d"]["pari"]
    assert picks["2026-10-28-e-f"]["pari"] is None  # above 2.30 (owner 2026-10-28)
    assert doc["selections"] == [] and doc["combines"] == []


def test_positions_ratio_takes_every_odds_once_within_1_50_and_2_30():
    from sportai.services.bilan import bankroll_settings, build_bilan

    def p(mid, pari, cote, res):
        return {"match_id": mid, "match": mid, "pari": pari, "cote": cote, "resultat": res}

    days = {"2026-10-20": {"positions": [p("a", "Victoire a", 1.50, "gagne"), p("a", "Victoire a", 1.50, "gagne"),
                                         p("b", "Plus de 2,5 buts", 1.70, "perdu"), p("c", "Victoire c", 3.10, "gagne"),
                                         p("d", "Victoire d", 1.40, "gagne")]},
            "2026-10-28": {"paris_retenus": {"e": p("e", "Victoire e", 2.30, "gagne"), "f": {"pari": None}}}}
    b = build_bilan({}, days, bankroll_settings(None), datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert (b["positions"]["valides"], b["positions"]["non_valides"]) == (2, 1)


def test_estimated_scores_count_as_positions_once_within_1_50_and_2_30():
    from sportai.services.bilan import bankroll_settings, build_bilan

    def m(home, away, h, a, res, cotes):
        return {"statut": "termine", "jour": "2026-10-20", "domicile": home, "exterieur": away,
                "score_estime": {"domicile": h, "exterieur": a, "score": f"{h} - {a}", "resultat": res},
                "cotes_1x2": dict(zip(("domicile", "nul", "exterieur"), cotes)),
                "proba_1x2": {"domicile": 0.5, "nul": 0.25, "exterieur": 0.25}}

    matchs = {"a": m("A", "B", 2, 1, "reussi", (1.80, 3.5, 4.5)),   # counted, won
              "b": m("C", "D", 1, 1, "rate", (2.5, 2.20, 3.0)),     # draw at 2.20, lost
              "c": m("E", "F", 2, 0, "reussi", (1.30, 5.0, 9.0)),   # under 1.50: out
              "d": m("G", "H", 0, 1, "reussi", (2.0, 3.4, 3.6)),    # away at 3.60: out
              "e": m("I", "J", 1, 0, "reussi", (1.90, 3.4, 4.0))}   # already a position on that outcome
    days = {"2026-10-20": {"positions": [{"match_id": "e", "match": "I - J", "pari": "Victoire I", "cote": 1.90,
                                          "resultat": "gagne"}]}}
    b = build_bilan(matchs, days, bankroll_settings(None), datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert (b["positions"]["valides"], b["positions"]["non_valides"]) == (2, 1)
    pos = [i for i in b["valides"] + b["non_valides"] if i["categorie"] == "position"]
    assert sorted(i["libelle"] for i in pos if i.get("origine") == "score estimé") == ["Match nul (C - D)", "Victoire A (A - B)"]


def test_top_pick_path_every_match_gets_a_choice_from_2026_10_28_evening():
    # owner 2026-10-28 16:00: "je veux un choix sur tous les matchs de ce soir"
    now = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)

    def l(match, pari, cote, proba, pm, marche="Nombre de buts"):
        return {**leg(match, pari, cote, proba, pm, 4), "match_id": f"2026-10-28-{match}", "marche": marche,
                "coup_envoi": (now + timedelta(hours=4)).isoformat(), "_p_cmp": proba}

    legs = [l("i-j", "Victoire i", 1.80, 0.66, 0.50, "Résultat du match"),  # 16 points apart: not a normal pick
            l("i-j", "Plus de 2,5 buts", 1.72, 0.50, 0.55),
            l("i-j", "Les deux équipes marquent : non", 1.70, 0.60, 0.58, "Les deux équipes marquent"),
            l("k-l", "Victoire k", 2.60, 0.40, 0.38, "Résultat du match")]  # nothing within 1.50-2.30
    picks = build_pronostic(legs, {}, None, now, "matin")["paris_retenus"]
    assert picks["2026-10-28-i-j"]["pari"] == "Victoire i" and picks["2026-10-28-i-j"]["non_retenu"]
    assert picks["2026-10-28-k-l"]["pari"] is None


def test_a_position_the_model_does_not_retain_never_counts_in_the_ratio():
    # owner 2026-10-29: shown with "Le modèle ne le retient pas", outside the ratio
    from sportai.services.bilan import day_positions
    doc = {"paris_retenus": {"a": {"match_id": "a", "pari": "Victoire a", "cote": 1.8},
                             "b": {"match_id": "b", "pari": "Victoire b", "cote": 1.8, "non_retenu": True},
                             "c": {"match_id": "c", "pari": "Victoire c", "cote": 1.8, "choix_impose": True}}}
    assert [p["match_id"] for p in day_positions(doc)] == ["a"]
