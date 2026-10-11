import math
from datetime import datetime, timedelta, timezone

from sportai.services import joueur
from sportai.services.joueur import candidates, decide, replay, run

NOW = datetime(2026, 10, 11, 7, tzinfo=timezone.utc)


def leg(match, pari, cote, proba, hours=4, valide=True):
    home, _, away = match.partition("-")
    return {"match_id": f"2026-10-11-{match}", "match": f"{home} - {away}", "coup_envoi": (NOW + timedelta(hours=hours)).isoformat(),
            "pari": pari, "cote": cote, "proba": proba, "valeur": round(proba * cote - 1, 4), "valide": valide}


def ticket(title, legs):
    cote = round(math.prod(l["cote"] for l in legs), 2)
    proba = round(math.prod(l["proba"] for l in legs), 4)
    return {"titre": title, "jambes": legs, "cote_totale": cote, "proba_totale": proba}


A, C, E, G = (leg("a-b", "Victoire a", 1.80, 0.62), leg("c-d", "Victoire c", 1.60, 0.64),
              leg("e-f", "Victoire f", 2.80, 0.37), leg("g-h", "Victoire g", 2.0, 0.60, valide=False))
PRONO = {"jour": "2026-10-11", "top3": [E, A],
         "tickets": {"safe": ticket("Ticket safe", [A]), "semi_safe": ticket("Ticket semi safe", [A, C]),
                     "fun": ticket("Ticket fun", [G, E])}}


def test_candidates_value_and_kelly():
    c = {x["id"]: x for x in candidates(PRONO)}
    assert c["ticket:safe"]["valeur"] == round(0.62 * 1.8 - 1, 4)
    assert c["ticket:safe"]["kelly"] == round(c["ticket:safe"]["valeur"] / 0.8, 4)
    assert "top3:2026-10-11-e-f:Victoire f" in c


def test_decisions_follow_the_profile():
    ds = {d["id"]: d for d in decide(PRONO, None, {}, 100.0, "equilibre", NOW)}
    # The goal leg is not validated: never played.
    assert not ds["ticket:fun"]["joue"] and "non validé" in ds["ticket:fun"]["raison"]
    played = [d for d in ds.values() if d["joue"]]
    assert played and all(d["mise"] <= 5.0 for d in played)  # 5 % per bet at most
    assert sum(d["mise"] for d in played) <= 10.0  # 10 % per day at most
    # One position per match: a-b is played once only.
    assert sum("2026-10-11-a-b" in {l["match_id"] for l in d["jambes"]} for d in played) == 1
    # Prudent wants 3 % value and 40 % chance: the 2.80 single (37 %) is passed.
    pr = {d["id"]: d for d in decide(PRONO, None, {}, 100.0, "prudent", NOW)}
    assert not pr["top3:2026-10-11-e-f:Victoire f"]["joue"]
    assert all(d["mise"] <= 2.0 for d in pr.values())


def test_nothing_worth_it_passes():
    flat = leg("x-y", "Victoire x", 1.9, 0.53)  # value +0.7 %
    doc = {"jour": "2026-10-11", "top3": [flat], "tickets": {}}
    ds = decide(doc, None, {}, 100.0, "prudent", NOW)
    assert not any(d["joue"] for d in ds)
    assert joueur._summary(ds).startswith("Rien ne vaut le coup")


def test_frozen_then_settled_and_bankroll(monkeypatch):
    monkeypatch.setattr(joueur, "simulation", lambda d, o: {"depart": d, "objectif": o, "profils": {
        k: {"scenarios": {s: {"mediane_1an": 1, "chance_objectif": 0, "risque_ruine": 0} for s in joueur.SCENARIOS}}
        for k in joueur.PROFILS}})
    settings = {"depart": 100.0, "objectif": 100000.0}
    day, etat = run(PRONO, None, {}, {}, settings, {"profil": "equilibre"}, None, NOW)
    played = [d for d in day["decisions"] if d["joue"]]
    assert day["journal"] and day["journal"][0]["texte"].startswith("Je joue")
    # After the matches: the analysis no longer proposes them, the decisions stay and are settled.
    later = NOW + timedelta(hours=8)
    page = {"2026-10-11-a-b": {"statut": "termine", "score": "2 - 0", "domicile": "a", "exterieur": "b"},
            "2026-10-11-c-d": {"statut": "termine", "score": "0 - 1", "domicile": "c", "exterieur": "d"},
            "2026-10-11-e-f": {"statut": "termine", "score": "0 - 1", "domicile": "e", "exterieur": "f"}}
    day2, etat2 = run({"jour": "2026-10-11", "top3": [], "tickets": {}}, day, {}, page, settings,
                      {"profil": "equilibre"}, etat, later)
    assert {d["id"] for d in day2["decisions"] if d["joue"]} == {d["id"] for d in played}
    assert all(d["fige"] and d["gain"] is not None for d in day2["decisions"] if d["joue"])
    assert etat2["bankroll"]["actuelle"] == round(100 + sum(d["gain"] for d in day2["decisions"] if d["joue"]), 2)
    assert any("gagné" in e["texte"] or "perdu" in e["texte"] for e in day2["journal"])
    assert etat2["plan"]["phrases"][0].startswith("Il faut multiplier")


def test_brake_after_a_big_drop():
    days = {"2026-10-09": {"decisions": [{"joue": True, "gain": 100.0}]},
            "2026-10-10": {"decisions": [{"joue": True, "gain": -90.0}]}}
    bank, pic, _ = replay(days, 100.0)
    assert (bank, pic) == (110.0, 200.0)
    profil, why = joueur.effective_profile("agressif", bank, pic)
    assert profil == "equilibre" and why.startswith("Frein")


def test_simulation_bigger_stakes_more_ruin():
    out = {k: joueur.simulate(100, 100000, p, "aucun", paths=300, years=1) for k, p in joueur.PROFILS.items()}
    assert out["prudent"]["risque_ruine"] <= out["agressif"]["risque_ruine"]
    assert out["agressif"]["mediane_fin"] < out["prudent"]["mediane_fin"]


def test_write_reads_the_page_copy(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(joueur, "simulation", lambda d, o: {"depart": d, "objectif": o, "profils": {
        k: {"scenarios": {s: {"mediane_1an": 1, "chance_objectif": 0, "risque_ruine": 0} for s in joueur.SCENARIOS}}
        for k in joueur.PROFILS}})
    (tmp_path / "page" / "reglages").mkdir(parents=True)
    (tmp_path / "page" / "reglages" / "joueur.json").write_text(json.dumps({"profil": "agressif"}))
    doc = joueur.write(str(tmp_path / "page"), str(tmp_path / "out"), PRONO, {}, {"depart": 100.0, "objectif": 1e5}, NOW)
    assert doc["profil"] == "agressif"
    assert json.loads((tmp_path / "out" / "joueur" / "etat.json").read_text())["profil"] == "agressif"
    prono = joueur.ticket_stakes(json.loads(json.dumps(PRONO)), doc)
    assert prono["tickets"]["fun"]["mise"] == 0.0 and prono["tickets"]["fun"]["joue"] is False


def test_live_follow_up_and_live_odds_seen():
    day, _ = run(PRONO, None, {}, {}, {"depart": 100.0, "objectif": 1e5}, {"profil": "equilibre"},
                 {"simulation": {"depart": 100.0, "objectif": 1e5, "profils": {k: {"scenarios": {s: {
                     "mediane_1an": 1, "chance_objectif": 0, "risque_ruine": 0} for s in joueur.SCENARIOS}}
                     for k in joueur.PROFILS}}}, NOW)
    page = {"2026-10-11-a-b": {"jour": "2026-10-11", "statut": "en_direct", "score": "1 - 0", "minute": 52,
                               "domicile": "a", "exterieur": "b",
                               "direct": {"proba": {"domicile": 0.85, "nul": 0.11, "exterieur": 0.04},
                                          "cotes_direct": [{"pari": "Plus de 2,5 buts", "cote": 2.1, "proba": 0.55,
                                                            "valeur": 0.155, "statut": "opportunite"}]}}}
    later = NOW + timedelta(hours=5)
    live = joueur.live(day, page, later)
    safe = next(d for d in live["decisions"] if d["id"] == "ticket:safe")
    assert safe["joue"] and safe["direct"]["chance"] == 0.85 and safe["direct"]["chance_avant"] == 0.62
    assert "en bonne voie" in live["journal"][-2]["texte"]
    assert live["direct_vus"][0]["pari"] == "Plus de 2,5 buts" and not live["direct_vus"][0]["joue"]
    # Same minute again: nothing new in the journal.
    again = joueur.live(live, page, later)
    assert len(again["journal"]) == len(live["journal"])
    # A pre-match pass afterwards keeps what the live loop wrote.
    day2, _ = run(PRONO, again, {}, page, {"depart": 100.0, "objectif": 1e5}, {"profil": "equilibre"}, None, later)
    assert day2["direct_vus"] and next(d for d in day2["decisions"] if d["id"] == "ticket:safe")["direct"]


def test_leg_live_chance_goals_and_finished():
    m = {"statut": "en_direct", "score": "2 - 1", "domicile": "a", "exterieur": "b", "direct": {}}
    assert joueur.leg_live_chance({"pari": "Plus de 2,5 buts"}, m) == 1.0
    assert joueur.leg_live_chance({"pari": "Les deux équipes marquent : non"}, m) == 0.0
    assert joueur.leg_live_chance({"pari": "Victoire a"}, m) is None
    assert joueur.leg_live_chance({"pari": "Victoire a"}, {**m, "statut": "termine"}) == 1.0
