from datetime import datetime, timedelta, timezone

from sportai.services import chasseur
from sportai.services.chasseur import eligible, live, replay, run, stopped

NOW = datetime(2026, 10, 15, 6, tzinfo=timezone.utc)


def leg(match, pari, cote, proba, pm, hours=6, valide=True):
    home, _, away = match.partition("-")
    return {"match_id": f"2026-10-15-{match}", "match": f"{home} - {away}",
            "coup_envoi": (NOW + timedelta(hours=hours)).isoformat(), "pari": pari, "marche": "Résultat",
            "cote": cote, "proba": proba, "proba_marche": pm, "valeur": round(proba * cote - 1, 4), "valide": valide,
            "_divergence_max": 0.10, "_p_cmp": proba}


A = leg("a-b", "Victoire a", 1.60, 0.66, 0.60)
B = leg("c-d", "Victoire c", 1.75, 0.62, 0.55)


def pari(cote, res):
    return {"pari": {"cote": cote, "fige": True, "resultat": res, "confiance": 0.6}}


def test_eligible_rules():
    assert eligible(A) is None
    assert eligible(leg("x-y", "Victoire x", 1.40, 0.75, 0.70)) == "cote hors de 1,50 à 2,00"
    assert eligible(leg("x-y", "Victoire x", 1.90, 0.60, 0.49)) == "les bookmakers n'en font pas le favori"
    trial = {**A, "valide": False, "marche": "Les deux équipes marquent"}
    assert eligible(trial) is None  # every market, like the joueur pro
    assert eligible({**trial, "proba": 0.63, "proba_marche": 0.62}) == "pas assez de valeur"  # 3 % asked on trial
    assert "je le note" in eligible(trial, arretes={"Les deux équipes marquent"})
    assert "chances" in eligible(leg("x-y", "Victoire x", 1.80, 0.57, 0.53))


def test_climb_refuge_and_fall():
    days = {f"2026-10-{d:02d}": pari(1.6, "gagne") for d in range(1, 5)}  # 100 -> 655.36: refuge
    s = replay(days)
    assert s["coffre"] == 555.36 and s["montante"] == 100 and len(s["refuges"]) == 1
    s = replay({**days, "2026-10-05": pari(1.6, "perdu")})  # fall: 100 taken from the vault
    assert s["coffre"] == 455.36 and s["montante"] == 100 and s["apports"] == 100
    s = replay({"2026-10-01": pari(1.6, "perdu")})  # empty vault: a new 100 counted as a contribution
    assert s["apports"] == 200 and s["montante"] == 100


def test_day_pick_freeze_and_wait():
    doc, etat, _ = run([A, B], {}, None, {}, NOW, "2026-10-15")
    assert doc["statut"] == "vise" and doc["pari"]["pari"] == "Victoire a" and doc["pari"]["mise"] == 100
    late = NOW + timedelta(hours=5, minutes=35)
    doc2, _, _ = run([A, B], {}, doc, {"2026-10-15": doc}, late, "2026-10-15")
    assert doc2["statut"] == "joue" and doc2["pari"]["fige"]
    page = {A["match_id"]: {"statut": "termine", "score": "2 - 0", "domicile": "a", "exterieur": "b"}}
    tomorrow = leg("c-d", "Victoire c", 1.60, 0.66, 0.60, hours=30)
    nxt, etat, changed = run([tomorrow], page, None, {"2026-10-15": doc2}, NOW + timedelta(days=1), "2026-10-16")
    assert changed["2026-10-15"]["pari"]["resultat"] == "gagne"
    assert etat["montante"] == 160 and nxt["pari"]["mise"] == 160
    pending, _, _ = run([tomorrow], {}, None, {"2026-10-15": doc2}, NOW + timedelta(days=1), "2026-10-16")
    assert pending["statut"] == "en_attente" and pending["pari"] is None


def test_stays_at_the_chalet():
    doc, etat, _ = run([leg("x-y", "Victoire x", 2.4, 0.45, 0.40)], {}, None, {}, NOW, "2026-10-15")
    assert doc["statut"] == "au_chalet" and "chalet" in doc["raison"] and etat["jauge"] == chasseur.DEPART


def test_trial_market_back_to_paper():
    bad = {"pari": {"cote": 1.6, "fige": True, "resultat": "perdu", "confiance": 0.6, "valide": False,
                    "marche": "Handicap"}}
    days = {f"2026-10-{d:02d}": bad for d in range(1, 9)}
    assert stopped(replay(days)) == {"Handicap"}


def live_match(score="1 - 0", minute=30, statut="en_direct", rows=()):
    return {"jour": "2026-10-15", "statut": statut, "domicile": "a", "exterieur": "b", "score": score,
            "minute": minute, "coup_envoi": NOW.isoformat(),
            "direct": {"maj": NOW.isoformat(), "proba": {"domicile": 0.7, "nul": 0.2, "exterieur": 0.1},
                       "cotes_direct": list(rows)}}


def test_frozen_30_min_before_then_followed_never_bets_live():
    day = {"jour": "2026-10-15", "statut": "au_chalet", "pari": None, "raison": "rien", "seuil": 0.58, "journal": []}
    row = {"pari": "Victoire a", "cote": 1.55, "proba": 0.70, "proba_marche": 0.62, "valeur": 0.085,
           "statut": "opportunite"}
    assert live(day, {"2026-10-15-a-b": live_match(rows=[row])}, NOW, {})["pari"] is None  # no live bet
    doc, _, _ = run([A], {}, None, {}, NOW, "2026-10-15")
    assert not doc["pari"]["fige"]
    at40 = NOW + timedelta(hours=5, minutes=20)
    assert not run([A], {}, doc, {"2026-10-15": doc}, at40, "2026-10-15")[0]["pari"]["fige"]
    upcoming = {A["match_id"]: {**live_match(), "statut": "a_venir", "score": None}}
    frozen = live(doc, upcoming, NOW + timedelta(hours=5, minutes=31), {})
    assert frozen["pari"]["fige"] and frozen["statut"] == "joue"
    missed = run([], {}, doc, {"2026-10-15": doc}, NOW + timedelta(hours=7), "2026-10-15")[0]
    assert missed["pari"]["fige"] and missed["pari"]["cote"] == 1.60  # no pass in time: last price seen
    d = live(frozen, {A["match_id"]: live_match()}, NOW + timedelta(hours=6, minutes=30), {})
    assert d["pari"]["direct"]["chance"] == 0.7 and d["en_direct"]["resume"].startswith("En direct : Victoire a")
    d2 = live(d, {A["match_id"]: live_match("2 - 0", 90, "termine")}, NOW + timedelta(hours=8), {})
    assert d2["pari"]["resultat"] == "gagne" and replay({"2026-10-15": d2})["montante"] == 160
