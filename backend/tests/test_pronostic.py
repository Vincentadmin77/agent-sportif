from datetime import datetime, timedelta, timezone

from sportai.services.pronostic import build_pronostic

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
    assert bilan["par_type"]["score"]["valides"] == 1 and bilan["global"]["valides"] >= 2
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
