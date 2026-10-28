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
