from datetime import datetime, timedelta, timezone

from sportai.services.pronostic import build_pronostic

NOW = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)


def leg(match, pari, cote, proba, marche_p, hours=4):
    return {"match_id": f"2026-10-11-{match}", "match": match, "competition": "Ligue 1",
            "coup_envoi": (NOW + timedelta(hours=hours)).isoformat(), "pari": pari, "marche": "Résultat du match",
            "cote": cote, "bookmaker": "winamax_fr", "proba": proba, "proba_marche": marche_p,
            "valeur": round(proba * cote - 1, 4), "valide": True, "_divergence_max": 0.10}


LEGS = [leg("a-b", "Victoire a", 1.70, 0.66, 0.58), leg("c-d", "Victoire c", 1.60, 0.64, 0.60),
        leg("e-f", "Victoire f", 2.80, 0.42, 0.34), leg("g-h", "Victoire g", 3.0, 0.40, 0.31),
        leg("i-j", "Victoire i", 1.40, 0.70, 0.69),  # negative value: never proposed
        leg("k-l", "Victoire k", 2.5, 0.60, 0.38)]  # model and market too far apart: never proposed


def test_top3_and_tickets():
    doc = build_pronostic(LEGS, {}, None, NOW, "matin")
    assert [l["match"] for l in doc["top3"]] == ["g-h", "e-f", "a-b"]
    safe, semi, fun = (doc["tickets"][k] for k in ("safe", "semi_safe", "fun"))
    # Safe: a combined bet above 1.50 at 50 % or more; none here, so the best single bet, a-b, and it says so.
    assert [l["match"] for l in safe["jambes"]] == ["a-b"] and safe["cote_totale"] == 1.7
    assert safe["message"].startswith("Pas de combiné de 2 paris")
    # Semi-safe: at most 2 bets above 1.90, not the safe ticket again: a-b with c-d.
    assert {l["match"] for l in semi["jambes"]} == {"a-b", "c-d"} and semi["cote_totale"] == 2.72
    assert semi["proba_totale"] == 0.4224
    assert semi["message"] is None
    # Fun: a 3-bet combined bet above 2.50: a-b, c-d and e-f (7.62, 18 %).
    assert {l["match"] for l in fun["jambes"]} == {"a-b", "c-d", "e-f"} and fun["cote_totale"] == 7.62
    assert not any(k.startswith("_") for t in (safe, semi, fun) for l in t["jambes"] for k in l)
    assert "i-j" not in str(doc) and "k-l" not in str(doc)


def test_nothing_correct_says_so():
    doc = build_pronostic([LEGS[4], LEGS[5]], {}, None, NOW, "matin")
    assert doc["top3"] == [] and "Aucune cote correcte" in doc["message"]
    for t in doc["tickets"].values():
        assert t["jambes"] == [] and t["cote_totale"] is None and t["message"].startswith("Pas de ticket")


def test_ticket_frozen_after_first_kickoff_then_settled():
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    later = NOW + timedelta(hours=5)
    page = {"2026-10-11-a-b": {"statut": "termine", "score": "2 - 0", "domicile": "a", "exterieur": "b"},
            "2026-10-11-c-d": {"statut": "termine", "score": "1 - 1", "domicile": "c", "exterieur": "d"}}
    doc = build_pronostic(LEGS, page, first, later, "resultats")
    safe = doc["tickets"]["semi_safe"]
    assert [l["pari"] for l in safe["jambes"]] == [l["pari"] for l in first["tickets"]["semi_safe"]["jambes"]]
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
    # safe a-b won at 1.70 (+2.10), semi a-b + c-d lost (-2.00), fun a-b + c-d + e-f lost (-1.00)
    assert [h["gain"] for h in history] == [2.1, -2.0, -1.0] and bank == 99.1
    bilan = build_bilan(page, {"2026-10-11": day}, settings, NOW)
    assert bilan["bankroll"]["actuelle"] == 99.1 and bilan["par_type"]["safe"]["taux"] == 1.0
    assert bilan["par_type"]["score"]["valides"] == 1 and bilan["global"]["valides"] >= 2
    assert likely_score({"expected_goals": [1.4, 0.9]})["score"] == "1 - 0"
    assert likely_score({"likely_score": [1, 1, 0.13]})["proba"] == 0.13


def test_tickets_fixed_45_min_before_then_bonus():
    first = build_pronostic(LEGS, {}, None, NOW, "matin")
    assert not any(t.get("fige") for t in first["tickets"].values())
    m45 = build_pronostic(LEGS, {}, first, NOW + timedelta(hours=3, minutes=15), "m45")
    assert all(t["fige"] for t in m45["tickets"].values()) and m45["tickets"]["safe"]["message"].startswith("Ticket figé")
    # Better bets found later do not change the fixed tickets: they make bonus tickets, apart.
    later = [*LEGS, leg("m-n", "Victoire m", 1.95, 0.80, 0.75, hours=8), leg("o-p", "Victoire o", 1.60, 0.78, 0.74, hours=8)]
    doc = build_pronostic(later, {}, m45, NOW + timedelta(hours=3, minutes=30), "h3")
    assert all(doc["tickets"][k]["jambes"] == m45["tickets"][k]["jambes"] for k in doc["tickets"])
    safe = doc["bonus"]["safe"]
    assert {l["match"] for l in safe[-1]["jambes"]} == {"m-n", "o-p"} and not safe[-1]["fige"]
    assert safe[-1]["titre"] == "Ticket bonus safe" and all(b["fige"] for b in safe[:-1])
