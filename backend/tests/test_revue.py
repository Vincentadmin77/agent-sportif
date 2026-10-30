"""The daily review of the errors and of the winning bets nobody chose (owner, 2026-10-24)."""

from datetime import date, datetime, timedelta, timezone

from sportai.services import joueur
from sportai.services.revue import BORNES, MIN_MATCHS, build_revue, correct_leg, corrige, famille

TODAY = date(2026, 10, 24)


def match_doc(day, home, away, score, btts_proba=0.57, btts_cote=1.57, extra=()):
    rows = [{"pari": "Les deux équipes marquent : oui", "proba": btts_proba, "cote": btts_cote, "proba_marche": 0.61,
             "valeur": round(btts_proba * btts_cote - 1, 4)},
            {"pari": "Les deux équipes marquent : non", "proba": round(1 - btts_proba, 4), "cote": 2.30,
             "proba_marche": 0.39, "valeur": round((1 - btts_proba) * 2.3 - 1, 4)},
            {"pari": "Plus de 2,5 buts", "proba": 0.55}]  # no odds: calibration only, never listed as missed
    return {"jour": day, "domicile": home, "exterieur": away, "statut": "termine", "score": score,
            "tous_marches": {"analyses": [{"pari": f"Victoire {home}", "marche": "Résultat du match", "cote": 1.55,
                                           "proba": 0.69, "proba_marche": 0.60, "valeur": 0.07},
                                          {"pari": "Gustavo buteur", "marche": "Buteur", "cote": 3.2, "proba": 0.3},
                                          *extra]},
            "buts": {"marches": rows}}


def test_families():
    assert famille("btts", "yes", None) == "btts_oui" and famille("totals", "under", 2.5) == "moins_2_5"
    assert famille("totals", "over", 4.5) == "plus_autres" and famille("team_total_away", "over", 1.5) == "equipe_plus_1_5"
    assert famille("1x2", "draw", None) == "nul" and famille("asian_handicap", "home", -0.5) == "handicap_asiatique"


def test_review_lists_winners_nobody_chose_and_chosen_losses():
    page = {"2026-10-23-brest-nice": match_doc("2026-10-23", "Brest", "Nice", "1 - 2"),
            "2026-10-23-alaves-malaga": match_doc("2026-10-23", "Alaves", "Malaga", "1 - 1"),
            "2026-10-24-a-b": {**match_doc("2026-10-24", "A", "B", "0 - 0")}}  # today: not reviewed
    leg = {"match_id": "2026-10-23-alaves-malaga", "match": "Alaves - Malaga", "pari": "Victoire Alaves",
           "cote": 1.55, "proba": 0.69, "proba_marche": 0.60, "resultat": "perdu"}
    pron = {"2026-10-23": {"tickets": {"semi_safe": {"jambes": [leg]}}, "top3": [], "bonus": {}}}
    jou = {"2026-10-23": {"decisions": [{"joue": True, "jambes": [
        {"match_id": "2026-10-23-brest-nice", "pari": "Les deux équipes marquent : non", "resultat": "perdu",
         "match": "Brest - Nice", "cote": 2.3, "proba": 0.43}]}]}}
    r = build_revue(page, pron, jou, TODAY)
    assert r["dernier_jour"] == "2026-10-23"
    missed = {(x["match"], x["pari"]) for x in r["rates"]}
    # both teams scored at Brest: a winner at 1.57 nobody took; Alavés - Málaga 1-1 too
    assert ("Brest - Nice", "Les deux équipes marquent : oui") in missed
    assert ("Alaves - Malaga", "Les deux équipes marquent : oui") in missed
    assert not any("buteur" in p or p == "Plus de 2,5 buts" for _, p in missed)  # scorers and unpriced rows apart
    assert {l["pari"] for l in r["perdus"]} == {"Victoire Alaves", "Les deux équipes marquent : non"}
    f = r["familles"]["btts_oui"]
    assert f["matchs"] == 2 and f["frequence"] == 1.0 and f["rates"] == 2 and f["rates_sous_estimes"] == 2
    assert f["correction"] == 1.0 and r["corrections"] == {}  # two matches: far too few to correct anything
    assert r["familles"]["plus_2_5"]["matchs"] == 2  # the model's chance is judged even without odds
    text = " ".join(r["phrases"])
    assert text.startswith("Revue du 23/10") and "Les deux équipes marquent : oui à 1,57" in text
    assert "€" not in text and "mise" not in text.lower()


def test_review_corrects_a_family_under_rated_by_the_model_with_a_prior():
    page = {}
    for k in range(40):  # both teams scored in 30 of 40 matches the model gave 55 %
        day = f"2026-10-{1 + k % 20:02d}"
        page[f"{day}-h{k}-a{k}"] = match_doc(day, f"H{k}", f"A{k}", "1 - 1" if k % 4 else "1 - 0", btts_proba=0.55)
    r = build_revue(page, {}, {}, TODAY)
    c = r["corrections"]["btts_oui"]["modele"]
    assert 1.0 < c <= BORNES[1]
    # pulled towards no correction: 30 wins seen for 22 announced is 1.36, the prior keeps it below
    assert c < 30 / 22
    assert r["corrections"]["btts_non"]["modele"] < 1.0
    assert any(p.startswith("Les deux équipes marquent : oui : sur 40 matchs") and "remonte" in p for p in r["phrases"])
    assert len([d for d in page]) >= MIN_MATCHS


def test_correction_applied_to_a_leg():
    corr = {"btts_oui": {"modele": 1.2, "marche": 1.05}}
    fix = correct_leg("btts", "yes", None, 0.57, 0.0, 1.80, 0.56, corr)
    assert fix["proba"] == corrige(0.57, 1.2) > 0.57 and fix["valeur"] == round(fix["proba"] * 1.8 - 1, 4)
    assert fix["proba_marche"] == corrige(0.56, 1.05)
    same = correct_leg("1x2", "home", None, 0.5, 0.0, 2.0, 0.5, corr)
    assert same["proba"] == 0.5 and same["ratio"] == 1.0


def test_goal_leg_from_the_review_enters_the_ideas(tmp_path):
    """Both teams score at 1.80, model 54 % (value -2.8 %): left out; once the review saw the model under-rate that
    family (x1.2 on its odds), 58.5 % and +5.3 %: a goal idea, still "non validé"."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.db.models import Base
    from sportai.services.pronostic import legs_from_db
    from tests.test_all_markets import NOW, _run_with_rows

    eng = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    _run_with_rows(db, [("m1", "btts", "yes", None, 1.80, 0.54, 0.0, 0.53), ("m1", "btts", "no", None, 2.05, 0.46, 0.0, 0.47)])
    assert not [l for l in legs_from_db(db, NOW.date()) if l["pari"].endswith("oui") and not l.get("_secours")]
    legs = legs_from_db(db, NOW.date(), corrections={"btts_oui": {"modele": 1.2, "marche": 1.0}})
    (leg,) = [l for l in legs if l["pari"].endswith("oui")]
    assert leg["proba_brute"] == 0.54 and leg["proba"] > 0.58 and leg["valeur"] > 0 and leg["correction"] == 1.2
    assert leg["validation"] == "non validé" and leg["famille"] == "btts_oui"


def test_player_takes_a_goal_bet_the_bookmakers_give_at_47_percent():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)

    def leg(pari, famille, pm):
        return {"match_id": f"2026-10-24-{pari[:3]}", "match": "x - y", "pari": pari, "cote": 2.30, "proba": 0.50,
                "proba_marche": pm, "valeur": round(0.5 * 2.3 - 1, 4), "valide": False, "validation": "non validé",
                "coup_envoi": (now + timedelta(hours=3)).isoformat(), "_divergence_max": 0.10,
                **({"famille": famille} if famille else {}), "marche": "m"}

    goal, scorer = leg("x : plus de 1,5 buts", "equipe_plus_1_5", 0.47), leg("Joueur buteur", None, 0.47)
    ds = {d["id"]: d for d in joueur.decide({"jour": "2026-10-24", "top3": [goal, scorer], "tickets": {}}, None, {},
                                            100.0, "prudent", now)}
    assert ds[f"top3:{goal['match_id']}:{goal['pari']}"]["joue"]  # a team's over 1.5 at 47 %: played on trial
    assert ds[f"top3:{scorer['match_id']}:{scorer['pari']}"]["motif"] == "proba"
    assert "50 %" in ds[f"top3:{scorer['match_id']}:{scorer['pari']}"]["raison"]


def test_player_journal_shows_the_review_once():
    from tests.test_joueur import fake_simulation

    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(joueur, "simulation", fake_simulation)
    try:
        now = datetime(2026, 10, 24, 7, tzinfo=timezone.utc)
        prono = {"jour": "2026-10-24", "top3": [], "tickets": {},
                 "revue": {"phrases": ["Revue du 23/10 : 6 match(s) terminé(s).", "Choisis mais perdus : x."]}}
        doc, _ = joueur.run(prono, None, {}, {}, {"depart": 100, "objectif": 1000}, None, None, now)
        texts = [x["texte"] for x in doc["journal"]]
        assert "Revue du 23/10 : 6 match(s) terminé(s)." in texts and doc["revue"][1] == "Choisis mais perdus : x."
        again, _ = joueur.run(prono, doc, {}, {}, {"depart": 100, "objectif": 1000}, None, None, now)
        assert sum(t["texte"].startswith("Revue du ") for t in again["journal"]) == 1
    finally:
        mp.undo()
