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
def fake_simulation(*a, **k):
    sc = {s: {"mediane_echeance": 1, "mediane_1an": 1, "chance_objectif": 0, "risque_ruine": 0} for s in joueur.SCENARIOS}
    return {"depart": None, "objectif": None, "profils": {p: {"scenarios": sc} for p in joueur.PROFILS},
            "comparaison": {"scenarios": sc}}


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
    monkeypatch.setattr(joueur, "simulation", fake_simulation)
    settings = {"depart": 100.0, "objectif": 100000.0}
    day, etat = run(PRONO, None, {}, {}, settings, {"profil": "equilibre"}, None, NOW)
    played = [d for d in day["decisions"] if d["joue"]]
    assert day["journal"][0]["texte"].startswith("Mon profil aujourd'hui : prudent")
    assert day["journal"][-1]["texte"].startswith("Je joue")
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
    assert etat2["plan"]["phrases"][0].startswith("Échéance : 31 mai 2027")
    assert etat2["echeance"]["jours_restants"] == 232


def test_brake_after_a_big_drop():
    days = {"2026-10-09": {"decisions": [{"joue": True, "gain": 100.0}]},
            "2026-10-10": {"decisions": [{"joue": True, "gain": -90.0}]}}
    bank, pic, _ = replay(days, 100.0)
    assert (bank, pic) == (110.0, 200.0)
    profil, why = joueur.effective_profile("agressif", bank, pic)
    assert profil == "equilibre" and why.startswith("Frein")


def test_simulation_bigger_stakes_more_ruin():
    out = {k: joueur.simulate(100, 100000, p, "aucun", paths=300, days=365) for k, p in joueur.PROFILS.items()}
    assert out["prudent"]["risque_ruine"] <= out["agressif"]["risque_ruine"]
    assert out["agressif"]["mediane_fin"] < out["prudent"]["mediane_fin"]


def test_write_reads_the_page_copy(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(joueur, "simulation", fake_simulation)
    (tmp_path / "page" / "reglages").mkdir(parents=True)
    (tmp_path / "page" / "reglages" / "joueur.json").write_text(json.dumps({"profil": "agressif"}))
    doc = joueur.write(str(tmp_path / "page"), str(tmp_path / "out"), PRONO, {}, {"depart": 100.0, "objectif": 1e5}, NOW)
    # Autonomous: the page's profile is only a ceiling; with no record the player starts prudent.
    assert doc["profil"] == "prudent"
    etat = json.loads((tmp_path / "out" / "joueur" / "etat.json").read_text())
    assert etat["apprentissage"]["plafond"] == "agressif" and etat["autonome"]
    prono = joueur.ticket_stakes(json.loads(json.dumps(PRONO)), doc)
    assert prono["tickets"]["fun"]["mise_joueur"] == 0.0 and prono["tickets"]["fun"]["joue_joueur"] is False
    assert "mise" not in prono["tickets"]["fun"]  # the tickets' own stake is left to bilan.apply_stakes


def test_live_follow_up_and_live_odds_seen():
    day, _ = run(PRONO, None, {}, {}, {"depart": 100.0, "objectif": 1e5}, {"profil": "equilibre"},
                 {"simulation": fake_simulation()}, NOW)
    page = {"2026-10-11-a-b": {"jour": "2026-10-11", "statut": "en_direct", "score": "1 - 0", "minute": 52,
                               "domicile": "a", "exterieur": "b",
                               "direct": {"proba": {"domicile": 0.85, "nul": 0.11, "exterieur": 0.04},
                                          "cotes_direct": [{"pari": "Plus de 2,5 buts", "cote": 2.1, "proba": 0.55,
                                                            "valeur": 0.155, "statut": "opportunite"}]}}}
    later = NOW + timedelta(hours=5)
    live = joueur.live(day, page, later)
    safe = next(d for d in live["decisions"] if d["id"] == "ticket:safe")
    assert safe["joue"] and safe["direct"]["chance"] == 0.85 and safe["direct"]["chance_avant"] == 0.62
    assert "en bonne voie" in live["journal"][-1]["texte"]
    assert not any("Vu en direct" in e["texte"] for e in live["journal"])
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


def test_deadline_pace_raises_stakes_up_to_the_profile_ceiling():
    from datetime import date
    on_track = joueur.pace(100.0, 100.0, 1e5, date(2026, 10, 11))
    assert on_track["ecart"] == 0 and on_track["jours_restants"] == 232 and on_track["statut"] == "dans_les_temps"
    assert 0.029 < on_track["rythme_jour_necessaire"] < 0.031
    late = joueur.pace(100.0, 100.0, 1e5, date(2026, 11, 11))
    assert late["statut"] == "en_retard" and late["ecart"] < -0.5
    p = joueur.PROFILS["equilibre"]
    assert joueur.kelly_fraction(p, 0.0, False) == 0.25
    assert joueur.kelly_fraction(p, late["ecart"], False) == 0.5  # never above the profile's ceiling
    assert joueur.kelly_fraction(p, late["ecart"], True) == 0.25  # the safety brake wins
    ds = {d["id"]: d for d in decide(PRONO, None, {}, 100.0, "equilibre", NOW, 0.5)}
    assert "relevées pour l'échéance" in ds["ticket:safe"]["raison"] and ds["ticket:safe"]["mise"] <= 5.0



def _settled(i, kind, proba, cote, won, joue=True, motif=None):
    leg = {"match_id": f"m{i}", "pari": "x", "cote": cote, "resultat": "gagne" if won else "perdu"}
    return {"id": f"{kind}:{i}", "type": kind, "quoi": f"p{i}", "jambes": [leg], "cote": cote, "proba": proba,
            "valeur": round(proba * cote - 1, 4), "joue": joue, "mise": 1.0 if joue else 0.0, "motif": motif,
            "resultat": "gagne" if won else "perdu", "gain": (cote - 1 if won else -1.0) if joue else None}


def test_learning_profile_needs_evidence_and_respects_the_ceiling():
    assert joueur.learn({})["profil_auto"] == "prudent"
    # 40 winning-ish bets: wins 24 of 40 at 2.0 (model said 50 %): positive return, not yet proven.
    days = {f"2026-10-{i:02d}": {"decisions": [_settled(i, "top3", 0.5, 2.0, i % 5 < 3)]} for i in range(1, 41)}
    lrn = joueur.learn(days)
    assert lrn["propositions"]["n"] == 40 and lrn["profil_auto"] == "equilibre"
    assert lrn["correction"]["top3"] > 1.0  # it wins more often than the model said
    assert joueur.learn(days, plafond="prudent")["profil_auto"] == "prudent"


def test_learning_corrects_an_overconfident_model_and_thin_value_threshold():
    # Model said 60 % but only 1 in 4 won: chances corrected down, value threshold raised.
    days = {f"2026-09-{i:02d}": {"decisions": [_settled(i, "ticket", 0.6, 1.8, i % 4 == 0)]} for i in range(1, 25)}
    lrn = joueur.learn(days)
    assert lrn["correction"]["ticket"] < 0.8 and lrn["valeur_min"] == 0.04 and lrn["profil_auto"] == "prudent"
    ds = {d["id"]: d for d in decide(PRONO, None, {}, 100.0, "prudent", NOW, None,
                                     {"ratio": lrn["correction"], "valeur_min": lrn["valeur_min"]})}
    assert not ds["ticket:safe"]["joue"] and ds["ticket:safe"]["proba"] < ds["ticket:safe"]["proba_modele"]
    # Passed for thin value but they would have paid: threshold lowered.
    passed = {f"2026-08-{i:02d}": {"decisions": [_settled(i, "ticket", 0.55, 1.85, i % 3 != 0, joue=False,
                                                         motif="valeur")]} for i in range(1, 25)}
    assert joueur.learn(passed)["valeur_min"] == 0.02
    assert joueur.learn(passed)["examens"][0]["texte"].startswith("0 pari(s) joué(s)")


def test_singles_beyond_the_tickets_and_odds_watch():
    # Atalanta case (2026-10-12): not in any ticket nor the top 3, but a validated single with value.
    ata = {**leg("at-ve", "Victoire at", 1.45, 0.736), "proba_marche": 0.68, "_divergence_max": 0.10}
    far = {**leg("x-y", "Victoire x", 2.0, 0.60), "proba_marche": 0.45, "_divergence_max": 0.10}  # gap too big
    goals = {**leg("g-h", "Plus de 2,5 buts", 1.9, 0.6), "valide": False}
    empty = {"jour": "2026-10-11", "top3": [], "tickets": {}}
    first = decide(empty, None, {}, 1000.0, "prudent", NOW, legs=[ata, far, goals])
    assert [d["id"] for d in first] == ["simple:2026-10-11-at-ve:Victoire at"]
    assert first[0]["joue"] and first[0]["type"] == "simple" and first[0]["cotes"][0]["cote"] == 1.45
    # The odds drift down to 1.38: value 1.6 %, under the prudent 3 %: the player withdraws, and says why.
    day, _ = run(empty, None, {}, {}, {"depart": 1000.0, "objectif": 1e5}, None,
                 {"simulation": fake_simulation()}, NOW, [ata])
    later = NOW + timedelta(hours=1)
    day2, _ = run(empty, day, {}, {}, {"depart": 1000.0, "objectif": 1e5}, None,
                  {"simulation": fake_simulation()}, later, [{**ata, "cote": 1.38, "valeur": round(0.736 * 1.38 - 1, 4)}])
    d = day2["decisions"][0]
    assert not d["joue"] and [c["cote"] for c in d["cotes"]] == [1.45, 1.38]
    assert any(e["texte"].startswith("La cote de Victoire at") and "Je retire" in e["texte"] for e in day2["journal"])


def test_champions_league_singles_staked_at_half_then_back_to_paper_when_losing():
    ucl = {**leg("psg-bay", "Victoire psg", 2.2, 0.52), "valide": True, "ucl": True, "proba_marche": 0.46,
           "_divergence_max": 0.10}
    nat = leg("lyo-nan", "Victoire lyo", 2.2, 0.52)
    empty = {"jour": "2026-10-11", "top3": [], "tickets": {}}
    d_ucl, d_nat = (decide(empty, None, {}, 1000.0, "agressif", NOW, legs=[x])[0] for x in (ucl, nat))
    assert d_ucl["joue"] and d_nat["joue"] and d_ucl["mise"] == round(d_nat["mise"] / 2, 2)
    assert "Ligue des champions" in d_ucl["raison"]
    # A ticket holding a Champions League leg is played too, at half the stake.
    t = {"jour": "2026-10-11", "top3": [], "tickets": {"safe": {"titre": "Safe", "jambes": [ucl], "cote_totale": 2.2,
                                                               "proba_totale": 0.52}}}
    d_t = decide(t, None, {}, 1000.0, "agressif", NOW)[0]
    assert d_t["joue"] and d_t["mise"] == d_ucl["mise"]
    # One more point of value asked: 2.2 x 0.465 = +2.3 %, enough elsewhere (prudent 3 % ... equilibre 2 %), not here.
    thin = {**ucl, "proba": 0.465, "valeur": round(0.465 * 2.2 - 1, 4)}
    d = decide(empty, None, {}, 1000.0, "equilibre", NOW, legs=[thin])[0]
    assert not d["joue"] and d["motif"] == "valeur"
    # Its record is kept apart from the profile's, and 20 settled losses send it back to paper.
    lost = {**d_ucl, "resultat": "perdu", "jambes": [{**d_ucl["jambes"][0], "resultat": "perdu"}]}
    lrn = joueur.learn({f"2026-09-{i + 1:02d}": {"decisions": [lost]} for i in range(20)})
    assert lrn["propositions"]["n"] == 0 and lrn["ucl_sur_papier"]["n"] == 20 and lrn["ucl_arret"]
    assert "ucl" in lrn["correction"]
    d = decide(empty, None, {}, 1000.0, "agressif", NOW, ajust={"ucl_arret": True}, legs=[ucl])[0]
    assert not d["joue"] and d["raison"].startswith("Ligue des champions")