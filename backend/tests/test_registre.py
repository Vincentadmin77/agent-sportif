"""The positions registry of the "agent mathématicien" (owner 2026-10-28)."""

from datetime import datetime, timezone

from sportai.services import registre as R

KO = "2026-10-29T19:00:00+00:00"


def pron(cote=1.80, fige=True, resultat=None, pari="Plus de 2,5 buts", recalcule=False):
    p = {"match_id": "m1", "match": "A - B", "competition": "Serie A", "coup_envoi": KO, "marche": "Nombre de buts",
         "pari": pari, "cote": cote, "proba": 0.60, "proba_marche": 0.55, "confiance": 0.575, "fige": fige,
         "niveau": "Confiance moyenne", "bookmaker": "X"}
    if resultat:
        p["resultat"] = resultat
    return {"jour": "2026-10-29", "passage": "m45", "recalcule": recalcule, "paris_retenus": {"m1": p}}


MATCH = {"m1": {"passages": {"m45": "2026-10-29T18:15:00+00:00", "m15": "2026-10-29T18:45:00+00:00"}, "tous_marches": {"analyses": [{"pari": "Plus de 2,5 buts", "cote": 1.70, "proba_marche": 0.57}]}}}


def at(h):
    return datetime(2026, 10, 29, h, tzinfo=timezone.utc)


def test_frozen_position_recorded_once_with_closing_odds_and_result():
    reg = R.update({}, pron(fige=False), MATCH, at(17), "abc")
    assert reg["fiches"] == {}  # not frozen yet: no decision
    reg = R.update(reg, pron(), MATCH, at(18), "abc")
    f = reg["fiches"]["2026-10-29|m1|Plus de 2,5 buts"]
    assert f["cote_prise"] == 1.80 and f["decision"] == at(18).isoformat() and "cote_cloture" not in f
    assert f["version_regles"] == R.rules_version() and f["recalcule"] is False
    reg = R.update(reg, pron(cote=1.95, resultat="gagne"), MATCH, at(22), "def")
    f = reg["fiches"]["2026-10-29|m1|Plus de 2,5 buts"]
    assert f["cote_prise"] == 1.80 and f["version_code"] == "abc" and f["decision"] == at(18).isoformat()
    assert f["modifications"][0]["champ"] == "cote_prise" and f["modifications"][0]["apres"] == 1.95
    assert f["cote_cloture"] == 1.70 and f["clv"] == round(1.80 / 1.70 - 1, 4)
    assert f["resultat"] == "gagne" and f["gain_unite"] == 0.8


def test_no_closing_line_without_a_later_pass_and_late_records_flagged():
    match = {"m1": {**MATCH["m1"], "passages": {"m45": "2026-10-29T17:45:00+00:00"}}}
    reg = R.update(R.update({}, pron(), match, at(18), "abc"), pron(), match, at(22), "abc")
    f = reg["fiches"]["2026-10-29|m1|Plus de 2,5 buts"]
    assert f["cote_cloture"] is None and "clv" not in f and "enregistre_apres_coup_envoi" not in f
    late = R.update({}, pron(resultat="perdu"), MATCH, at(22), "abc")["fiches"]["2026-10-29|m1|Plus de 2,5 buts"]
    assert late["enregistre_apres_coup_envoi"] is True


def test_later_change_of_bet_is_logged_not_replaced():
    reg = R.update({}, pron(), MATCH, at(18), "abc")
    reg = R.update(reg, pron(pari="Victoire A"), MATCH, at(18), "abc")
    assert list(reg["fiches"]) == ["2026-10-29|m1|Plus de 2,5 buts"]
    assert reg["fiches"]["2026-10-29|m1|Plus de 2,5 buts"]["modifications"][0]["apres"] == "Victoire A"


def test_rebuilt_days_stay_apart():
    p = {"jour": "2026-10-20", "recalcule": True, "positions": [
        {"match_id": "m", "pari": "Victoire A", "cote": 1.6, "proba": 0.6, "proba_marche": 0.6, "resultat": "gagne"}]}
    assert R.update({}, p, {}, at(18), "abc")["fiches"] == {}
    reg = R.import_rebuilt(p)
    (f,) = reg["fiches"].values()
    assert f["recalcule"] and f["decision"] is None and f["gain_unite"] == 0.6
    rep = R.report([reg], min_group=1)
    assert rep["reel"]["global"]["reglees"] == 0 and rep["recalcule"]["global"]["validees"] == 1


def test_measures():
    rows = [{"resultat": "gagne", "cote_prise": 2.0, "p_marche": 0.5, "p_modele": 0.6, "gain_unite": 1.0, "clv": 0.05},
            {"resultat": "perdu", "cote_prise": 2.0, "p_marche": 0.5, "p_modele": 0.6, "gain_unite": -1.0, "clv": -0.02},
            {"resultat": "perdu", "cote_prise": 2.0, "p_marche": 0.5, "p_modele": 0.6, "gain_unite": -1.0},
            {"resultat": "rembourse", "cote_prise": 2.0, "gain_unite": 0.0}]
    m = R.measures(rows)
    assert m["reglees"] == 3 and m["remboursees"] == 1 and m["reussite"] == round(1 / 3, 4)
    assert m["rendement_mise_egale"] == -0.25 and m["pire_serie"] == 2 and m["creux_max_unites"] == -2.0
    assert m["brier"]["p_marche"] == 0.25 and m["clv_positive"] == 0.5
    lo, hi = R.wilson(23, 30)
    assert 0.58 < lo < 0.60 and 0.87 < hi < 0.89


def live_match(score="0 - 0", statut="en_direct"):
    return {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "competition": "Serie A", "coup_envoi": KO,
            "statut": statut, "score": score,
            "tous_marches": {"analyses": [{"pari": "Plus de 1,5 buts", "cote": 1.25, "proba": 0.80, "proba_marche": 0.76},
                                          {"pari": "Victoire A", "cote": 1.90, "proba": 0.5, "proba_marche": 0.5}]},
            "buteurs": {"cotes": [{"joueur": "Joe Nine", "joueur_id": 9, "cote": 1.9, "proba": 0.45, "proba_marche": 0.5}]}}


def test_targets_give_the_odds_from_which_a_short_bet_is_worth_it():
    t = R.targets(live_match())
    assert t["Plus de 1,5 buts"]["cote_valable"] == 1.50 and "Victoire A" not in t  # never under the 1.50 floor
    assert t["Joe Nine buteur"]["cote_valable"] == round(1.03 / 0.475, 2)


def test_live_note_only_when_worth_it_and_in_his_match():
    m = live_match()
    rows = [{"pari": "Plus de 1,5 buts", "cote": 2.40, "proba": 0.50, "statut": "opportunite"},  # above 2.30
            {"pari": "Joe Nine buteur", "cote": 2.20, "proba": 0.50, "statut": "opportunite",
             "buteur": {"id": 9, "nom": "Joe Nine", "forme": {"note": 5.5, "tirs": 0}}}]  # red rating: left
    assert R.capture_live(m, rows, 46, at(20)) == []
    rows[0]["cote"] = 2.25
    rows[1]["buteur"]["forme"] = {"note": 7.1, "tirs": 2}
    m["live_a_blanc"] = R.capture_live(m, rows, 46, at(20))
    assert [n["pari"] for n in m["live_a_blanc"]] == ["Plus de 1,5 buts", "Joe Nine buteur"]
    assert R.capture_live(m, [{**rows[0], "cote": 2.28}], 50, at(20)) == m["live_a_blanc"]  # noted once
    assert R.capture_live({**live_match()}, rows, 85, at(20)) == []  # too late


def test_live_notes_go_to_the_registry_apart_and_settle():
    m = {**live_match(score="1 - 1", statut="termine"), "feuille_de_match": {"buts": []}}
    m["live_a_blanc"] = [{"pari": "Plus de 1,5 buts", "p_avant": 0.78, "cote_avant": 1.25, "cote_valable": 1.50,
                          "heure": at(20).isoformat(), "minute": 46, "score": "0 - 0", "cote": 2.25, "p_direct": 0.5}]
    reg = R.update({}, {"jour": "2026-10-29", "paris_retenus": {}}, {"m1": m}, at(23), "abc")
    (f,) = [x for x in reg["fiches"].values() if x.get("live_a_blanc")]
    assert f["live_a_blanc"] and f["resultat"] == "gagne" and f["gain_unite"] == 1.25
    rep = R.report([reg], min_group=1)
    assert rep["reel"]["global"]["positions"] == 0 and rep["direct_a_blanc"]["global"]["validees"] == 1


def test_the_m_chooses_once_from_the_45_min_pass_and_keeps_it_apart():
    m = {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "coup_envoi": KO, "statut": "a_venir",
         "tous_marches": {"analyses": [
             {"pari": "Victoire A", "cote": 1.55, "proba": 0.6, "proba_marche": 0.62},
             {"pari": "Plus de 2,5 buts", "cote": 1.60, "proba": 0.6, "proba_marche": 0.61},
             {"pari": "Victoire B", "cote": 6.0, "proba": 0.15, "proba_marche": 0.14}]}}
    day = {"jour": "2026-10-29", "paris_retenus": {}}
    early = R.update({}, day, {"m1": m}, at(17), "abc")["fiches"]["2026-10-29|m1|M"]
    assert early["pari"] == "Victoire A" and not early["fige"]  # a choice already, still open
    m["passages"] = {"m45": at(18).isoformat()}
    reg = R.update({}, day, {"m1": m}, at(18), "abc")
    assert reg["fiches"]["2026-10-29|m1|M"]["fige"]
    f = reg["fiches"]["2026-10-29|m1|M"]
    assert f["pari"] == "Victoire A" and f["choix_m"] and f["p_marche"] == 0.62
    assert reg["cibles"] == {"m1": []}  # nothing very likely priced under 1.50 here
    m["tous_marches"]["analyses"][1]["proba_marche"] = 0.70  # later odds never change the choice
    reg = R.update(reg, day, {"m1": {**m, "statut": "termine", "score": "2 - 0"}}, at(22), "abc")
    f = reg["fiches"]["2026-10-29|m1|M"]
    assert f["pari"] == "Victoire A" and f["resultat"] == "gagne" and f["gain_unite"] == 0.55
    rep = R.report([reg], min_group=1)
    assert rep["reel"]["global"]["positions"] == 0 and rep["le_m_a_blanc"]["global"]["validees"] == 1


def test_visa_lights():
    assert R.visa({"pari": "x", "proba": 0.62, "proba_marche": 0.63})["feu"] == "vert"
    assert R.visa({"pari": "x", "proba": 0.55, "proba_marche": 0.54})["feu"] == "orange"
    assert R.visa({"pari": "x", "proba": 0.50, "proba_marche": 0.49})["feu"] == "rouge"
    assert R.visa({"pari": "x", "proba": 0.60, "proba_marche": 0.52})["feu"] == "rouge"  # model far above
    assert R.visa({"pari": "x", "proba": 0.39, "proba_marche": 0.50})["feu"] == "rouge"  # model far below
    assert R.visa({"pari": None}) is None


def test_opinions_side_by_side_then_who_was_right():
    m = {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "coup_envoi": KO, "statut": "a_venir",
         "passages": {"m45": at(18).isoformat()},
         "contexte": {"absences_effet": {"phrases": ["A sans son buteur."]}},
         "tous_marches": {"analyses": [{"pari": "Victoire A", "cote": 1.55, "proba": 0.6, "proba_marche": 0.62}]}}
    day = pron(cote=1.84)
    day["paris_retenus"]["m1"].update(proba=0.55, proba_marche=0.54)
    reg = R.update({}, day, {"m1": m}, at(18), "abc")
    a = reg["avis"]["m1"]
    assert a["officielle"]["pari"] == "Plus de 2,5 buts" and a["visa_m"]["feu"] == "orange"
    assert a["choix_m"]["pari"] == "Victoire A" and a["enqueteur"] == ["A sans son buteur."]
    day["paris_retenus"]["m1"]["resultat"] = "perdu"
    reg = R.update(reg, day, {"m1": {**m, "statut": "termine", "score": "1 - 0"}}, at(22), "abc")
    a = reg["avis"]["m1"]
    assert a["visa_m"]["feu"] == "orange" and "non validée" in a["bilan"] and "Choix du M validé" in a["bilan"]


def test_basket_opinions():
    b = {"paris_retenus": {"7": {"match": "C - D", "competition": "NBA", "coup_envoi": "2026-10-29T23:00:00Z",
                                 "pari": "Moins de 230,5 points dans le match", "cote": 1.8, "proba": 0.60,
                                 "proba_marche": 0.524}}}
    reg = R.basket_avis({"jour": "2026-10-29", "fiches": {}}, b, at(18))
    assert reg["avis"]["basket-7"]["visa_m"]["feu"] == "rouge" and reg["avis"]["basket-7"]["sport"] == "basket"


def test_the_m_says_why_it_passes_with_its_best_option():
    m = {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "coup_envoi": KO, "statut": "a_venir",
         "tous_marches": {"analyses": [{"pari": "Victoire A", "cote": 1.84, "proba": 0.55, "proba_marche": 0.54}]}}
    f = R.update({}, {"jour": "2026-10-29", "paris_retenus": {}}, {"m1": m}, at(17), "abc")["fiches"]["2026-10-29|m1|M"]
    assert f["pari"] is None and f["meilleure_option"]["pari"] == "Victoire A"
    assert "Victoire A à 1,84" in f["raison"] and "54 %" in f["raison"]


def test_the_m_chooses_on_every_nba_game_then_settles_it():
    bm = {"jour": "2026-10-29", "domicile": "C", "exterieur": "D", "coup_envoi": "2026-10-29T23:00:00Z",
          "competition": "NBA", "statut": "a_venir", "score": None,
          "cotes_cloture": {"lignes": [
              {"marche": "winner", "selection": "away", "ligne": None, "cote": 1.33, "proba": 0.62, "proba_marche": 0.66},
              {"marche": "handicap", "selection": "home", "ligne": 7.5, "cote": 1.71, "proba": 0.59, "proba_marche": 0.557},
              {"marche": "total", "selection": "over", "ligne": 234.5, "cote": 1.72, "proba": 0.41, "proba_marche": 0.53}]}}
    reg = R.basket_avis({"jour": "2026-10-29", "fiches": {}}, {}, at(18), {"7": bm})
    a = reg["avis"]["basket-7"]
    assert a["choix_m"]["pari"] is None and "C +7,5 points (handicap) à 1,71" in a["choix_m"]["raison"]
    assert a["choix_m"]["feu"] == "orange" and a["choix_m"]["choix_55"]["pari"] == "C +7,5 points (handicap)"
    assert a["sans_position"]
    bm["cotes_cloture"]["lignes"][1]["proba_marche"] = 0.61
    reg = R.basket_avis(reg, {}, at(20), {"7": bm})
    assert reg["avis"]["basket-7"]["choix_m"]["pari"] == "C +7,5 points (handicap)"
    assert reg["avis"]["basket-7"]["choix_m"]["feu"] == "vert"
    reg = R.basket_avis(reg, {}, datetime(2026, 10, 30, 3, tzinfo=timezone.utc),
                        {"7": {**bm, "statut": "termine", "score": "110-115"}})
    assert reg["avis"]["basket-7"]["choix_m"]["resultat"] == "gagne" and "Choix du M validé" in reg["avis"]["basket-7"]["bilan"]


def test_a_match_added_after_kick_off_still_gets_the_opinions_out_of_the_ratios():
    m = {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "coup_envoi": KO, "statut": "en_cours",
         "tous_marches": {"analyses": [{"pari": "Victoire A", "cote": 1.55, "proba": 0.6, "proba_marche": 0.62}]}}
    day = pron(cote=1.84)
    reg = R.update({}, day, {"m1": m}, at(20), "abc")
    a, f = reg["avis"]["m1"], reg["fiches"]["2026-10-29|m1|M"]
    assert a["tardif"] and a["visa_m"]["feu"] and a["choix_m"]["pari"] == "Victoire A"
    assert f["enregistre_apres_coup_envoi"] and R.report([reg])["le_m_a_blanc"]["global"]["positions"] == 0


def test_the_nba_55_choice_is_settled_and_counted_apart():
    bm = {"jour": "2026-10-29", "domicile": "C", "exterieur": "D", "coup_envoi": "2026-10-29T23:00:00Z",
          "passages": {"m45": "2026-10-29T22:15:00+00:00"}, "statut": "a_venir",
          "cotes_cloture": {"lignes": [{"marche": "total", "selection": "under", "ligne": 230.5, "cote": 1.8,
                                        "proba": 0.55, "proba_marche": 0.56}]}}
    reg = R.basket_avis({"jour": "2026-10-29", "fiches": {}}, {}, at(22), {"7": bm})
    reg = R.basket_avis(reg, {}, datetime(2026, 10, 30, 3, tzinfo=timezone.utc),
                        {"7": {**bm, "statut": "termine", "score": "110-115"}})
    assert reg["avis"]["basket-7"]["choix_m"]["choix_55"]["resultat"] == "gagne"
    rep = R.report([reg], min_group=1)
    assert rep["le_m_nba_55"]["global"]["validees"] == 1 and rep["le_m_a_blanc"]["global"]["positions"] == 0


def test_a_stale_register_takes_the_position_that_froze_before_tip_off():
    early = {"paris_retenus": {"7": {"match": "C - D", "competition": "NBA", "coup_envoi": "2026-10-29T23:00:00Z",
                                     "pari": "D +1,5 points (handicap)", "cote": 1.67, "proba": 0.51,
                                     "proba_marche": 0.61}}}
    reg = R.basket_avis({"jour": "2026-10-29", "fiches": {}}, early, at(18))
    frozen = {"paris_retenus": {"7": {**early["paris_retenus"]["7"], "pari": "Victoire C", "cote": 1.82,
                                      "proba_marche": 0.50, "fige": True, "resultat": "perdu"}}}
    reg = R.basket_avis(reg, frozen, datetime(2026, 10, 30, 3, tzinfo=timezone.utc))
    a = reg["avis"]["basket-7"]
    assert a["officielle"]["pari"] == "Victoire C" and a["officielle"]["resultat"] == "perdu" and a["corrige"]


def test_a_day_rewritten_on_request_keeps_the_replaced_records_apart():
    m = {"jour": "2026-10-29", "domicile": "A", "exterieur": "B", "coup_envoi": KO, "statut": "termine", "score": "0 - 0"}
    reg = R.update({}, pron(), {"m1": {**m, "statut": "a_venir"}}, at(18), "abc")
    new = pron(pari="Moins de 2,5 buts", resultat="gagne", recalcule="Recalculé")
    out = R.realign(reg, new, {"m1": m}, at(23), "abc", "décision du propriétaire")
    old, fresh = out["fiches"]["2026-10-29|m1|Plus de 2,5 buts"], out["fiches"]["2026-10-29|m1|Moins de 2,5 buts"]
    assert old["remplacee"] and fresh["reecrit"] and fresh["enregistre_apres_coup_envoi"]
    assert out["avis"]["m1"]["officielle"]["pari"] == "Moins de 2,5 buts" and out["avis"]["m1"]["reecrit"]
    rep = R.report([out], min_group=1)
    assert rep["reel"]["global"]["positions"] == 1 and rep["reel"]["global"]["validees"] == 1


def test_a_pick_the_model_does_not_retain_stays_out_of_its_ratio():
    day = pron(resultat="gagne")
    day["paris_retenus"]["m1"]["non_retenu"] = True
    reg = R.update({}, day, MATCH, at(18), "abc")
    rep = R.report([reg], min_group=1)
    assert rep["reel"]["global"]["positions"] == 0 and rep["non_retenu"]["global"]["validees"] == 1
