"""Anytime goalscorer market (owner, 2026-10-15): model, name matching, settlement, live staking by the player."""

import math
from datetime import datetime, timedelta, timezone

from sportai.engine.football import scorers as sc
from sportai.services import buteurs, joueur

NOW = datetime(2026, 10, 14, 19, 30, tzinfo=timezone.utc)


def item(pid, name, first, last, pos, goals, minutes, apps, starts, team=40, subs_in=0, league=2):
    return {"player": {"id": pid, "name": name, "firstname": first, "lastname": last},
            "statistics": [{"team": {"id": team}, "league": {"id": league, "season": 2026},
                            "games": {"appearences": apps, "lineups": starts, "minutes": minutes, "position": pos},
                            "substitutes": {"in": subs_in}, "goals": {"total": goals}, "penalty": {"scored": 0}}]}


# Liverpool and LASK as API-Football lists them (names shortened like the feed: "R. Araújo"), 8 matches each.
LIVERPOOL = [item(1, "A. Isak", "Alexander", "Isak", "Attacker", 6, 650, 8, 8),
             item(2, "R. Araújo", "Ronald Federico", "Araújo da Silva", "Defender", 1, 720, 8, 8),
             item(3, "J. Newboy", "Jack", "Newboy", "Attacker", 1, 180, 2, 2),  # 1 goal in 2 games: not a 50 % striker
             item(4, "Alisson Becker", "Alisson Ramsés", "Becker", "Goalkeeper", 0, 720, 8, 8),
             item(5, "M. Salah", "Mohamed", "Salah Hamed Ghaly", "Attacker", 8, 700, 8, 8),
             item(6, "D. Szoboszlai", "Dominik", "Szoboszlai", "Midfielder", 2, 600, 8, 7, subs_in=1),
             # a player of another club in the same season: not counted for Liverpool
             {"player": {"id": 7, "name": "X. Loan", "firstname": "Xavier", "lastname": "Loan"},
              "statistics": [{"team": {"id": 99}, "league": {"id": 39, "season": 2026},
                              "games": {"appearences": 5, "lineups": 5, "minutes": 450, "position": "Attacker"},
                              "goals": {"total": 5}}]}]
LASK = [item(11, "F. Flecker", "Florian", "Flecker", "Midfielder", 2, 500, 8, 6, team=7),
        item(12, "S. Adeniran", "Samuel", "Adeniran", "Attacker", 4, 650, 8, 8, team=7)]


def squads():
    return {"domicile": sc.squad_from_api(LASK, 7, 2026), "exterieur": sc.squad_from_api(LIVERPOOL, 40, 2026)}


def test_squad_and_share_shrunk_towards_the_position():
    liv = squads()["exterieur"]
    assert liv.matches == 8 and liv.goals == 18  # the loanee's goals for another club are out
    isak, newboy = liv.players[1], liv.players[3]
    # 1 goal in 2 games is 0.5 per 90, more than Isak's 0.83 per 90 shrunk... but its share stays well below Isak's
    assert sc.share(newboy, liv) < sc.share(isak, liv)
    assert sc.share(liv.players[4], liv) < 0.01  # a goalkeeper
    p = sc.anytime_proba(2.0, sc.share(isak, liv), 90)
    lam = 2.0 * (1 - sc.OWN_GOALS) * sc.share(isak, liv)
    assert math.isclose(p, 1 - math.exp(-lam)) and 0.3 < p < 0.6
    assert sc.anytime_proba(2.0, 0.3, 0) == 0.0 and sc.anytime_proba(2.0, 0.3, 45) < sc.anytime_proba(2.0, 0.3, 90)
    # minutes: starter his usual minutes per start, substitute about 20, absent 0, unknown his minutes per appearance
    szob = liv.players[6]
    assert sc.expected_minutes(szob, "titulaire") == szob.minutes_per_start() == (600 - 20) / 7
    assert sc.expected_minutes(szob, "remplacant") == sc.BENCH_MINUTES
    assert sc.expected_minutes(szob, "absent") == 0.0
    assert sc.expected_minutes(szob, None) == 75.0


def test_names_match_accents_initials_and_drop_other_clubs():
    liv = squads()["exterieur"]
    p = liv.players
    assert sc.name_score("Ronald Araujo", p[2]) > 0 and sc.name_score("R. Araújo", p[2]) > 0
    assert sc.name_score("Alexander Isak", p[1]) > 0 and sc.name_score("Isak", p[1]) > 0
    assert sc.name_score("Mohamed Salah", p[5]) > 0 and sc.name_score("Alisson", p[4]) > 0
    assert sc.name_score("Kevin Isak", p[1]) == 0  # another first name
    odds = {"Alexander Isak": {"cote": 2.1, "bookmaker": "Bet365"}, "Florian Flecker": {"cote": 6.0, "bookmaker": "B"},
            # the 2026-10-14 feed listed Bradley Barcola under LASK - Liverpool: not in these squads, dropped
            "Bradley Barcola": {"cote": 3.4, "bookmaker": "Bet365"}}
    rows, dropped = sc.price_scorers(odds, squads(), {"domicile": 1.0, "exterieur": 2.4})
    assert {r["joueur"] for r in rows} == {"Alexander Isak", "Florian Flecker"}
    assert dropped == ["Bradley Barcola : pas un joueur de l'une des deux équipes (ou nom ambigu)"]
    isak = next(r for r in rows if r["joueur"] == "Alexander Isak")
    assert isak["cote_equipe"] == "exterieur" and isak["proba_marche"] == round(1 / 2.1, 4) and not isak["valide"]
    assert isak["valeur"] == round(isak["proba"] * 2.1 - 1, 4)
    # Two players answering to one name: cannot tell which, dropped.
    twins = sc.squad_from_api([item(1, "J. Silva", "João", "Silva", "Attacker", 1, 90, 1, 1),
                               item(2, "J. Silva", "Jorge", "Silva", "Defender", 0, 90, 1, 1)], 40, 2026)
    assert sc.match_player("J. Silva", [twins]) is None


def test_lineup_known_absent_dropped_and_bench_short():
    sheet = {"domicile": {11: "titulaire", 12: "remplacant"}, "exterieur": {5: "titulaire"}}
    odds = {n: {"cote": 3.0, "bookmaker": "b"} for n in ("Mohamed Salah", "Alexander Isak", "Samuel Adeniran")}
    rows, dropped = sc.price_scorers(odds, squads(), {"domicile": 1.0, "exterieur": 2.4}, sheet)
    by = {r["joueur"]: r for r in rows}
    assert by["Samuel Adeniran"]["minutes"] == sc.BENCH_MINUTES and by["Samuel Adeniran"]["composition"] == "remplacant"
    assert "Alexander Isak : absent de la feuille de match" in dropped


def test_best_odds_across_bookmakers_and_market_names():
    books = [{"name": "Bet365", "bets": [{"name": "Anytime Goal Scorer", "values": [
                 {"value": "Alexander Isak", "odd": "2.10"}, {"value": "No Goalscorer", "odd": "12"}]}]},
             {"name": "Betano", "bets": [{"name": "Away Anytime Goal Scorer", "values": [
                 {"value": "Alexander Isak", "odd": "2.25"}]},
                 {"name": "First Goal Scorer", "values": [{"value": "Alexander Isak", "odd": "5.0"}]}]}]
    assert sc.best_scorer_odds(books) == {"Alexander Isak": {"cote": 2.25, "bookmaker": "Betano", "bookmakers": 2}}


def test_price_fixture_with_cached_squads_and_lineup(tmp_path, monkeypatch):
    from sportai.connectors.base import Fixture

    monkeypatch.setenv("SPORTAI_PLAYERS_CACHE", str(tmp_path))
    calls = []

    class Api:
        def team_players(self, team, season):
            calls.append(team)
            return {7: LASK, 40: LIVERPOOL}[team]

        def lineups(self, fid):
            return [{"team": {"id": 7}, "startXI": [{"player": {"id": 12}}], "substitutes": []},
                    {"team": {"id": 40}, "startXI": [{"player": {"id": 1}}], "substitutes": []}]

    fx = Fixture("api-football", "1635674", "UCL", NOW + timedelta(minutes=45), "LASK Linz", "Liverpool", "real",
                 home_id=7, away_id=40)
    raw = {"bookmakers": [{"name": "Bet365", "bets": [{"name": "Anytime Goal Scorer", "values": [
        {"value": "Alexander Isak", "odd": "1.90"}, {"value": "Mohamed Salah", "odd": "1.80"},
        {"value": "Bradley Barcola", "odd": "3.40"}]}]}]}
    out = buteurs.price_fixture(Api(), raw, fx, [1.0, 2.6], NOW, 2026)
    assert out["composition"] and [r["joueur"] for r in out["rows"]] == ["Alexander Isak"]
    assert out["rows"][0]["equipe"] == "Liverpool" and out["rows"][0]["composition"] == "titulaire"
    assert len(out["ecartes"]) == 2  # Barcola (no player of either side) and Salah (not on the team sheet)
    buteurs.price_fixture(Api(), raw, fx, [1.0, 2.6], NOW, 2026)
    assert sorted(calls) == [7, 40]  # the second pass of the day reads the cache


# LASK - Liverpool, 2026-10-14 (3-6), events as API-Football gave them (trimmed): in a substitution "player" leaves.
EVENTS = [
    {"time": {"elapsed": 22}, "team": {"id": 40}, "player": {"id": 101814, "name": "Ronald Araujo"}, "type": "Goal",
     "detail": "Normal Goal", "assist": {"id": 1600}},
    {"time": {"elapsed": 24}, "team": {"id": 40}, "player": {"id": 161904, "name": "Bradley Barcola"}, "type": "Goal",
     "detail": "Normal Goal", "assist": {}},
    {"time": {"elapsed": 30}, "team": {"id": 7}, "player": {"id": 1, "name": "Alexander Isak"}, "type": "Goal",
     "detail": "Own Goal", "assist": {}},
    {"time": {"elapsed": 40}, "team": {"id": 40}, "player": {"id": 5, "name": "Mohamed Salah"}, "type": "Goal",
     "detail": "Missed Penalty", "assist": {}},
    {"time": {"elapsed": 62}, "team": {"id": 40}, "player": {"id": 101814, "name": "Ronald Araujo"}, "type": "subst",
     "detail": "Substitution 2", "assist": {"id": 284, "name": "Joe Gomez"}},
]
LINEUPS = [{"team": {"id": 7}, "startXI": [{"player": {"id": 12, "name": "Samuel Adeniran"}}]},
           {"team": {"id": 40}, "startXI": [{"player": {"id": i, "name": n}} for i, n in (
               (101814, "Ronald Araujo"), (161904, "Bradley Barcola"), (1, "Alexander Isak"), (5, "Mohamed Salah"))]}]


def finished_doc():
    sheet = buteurs.match_sheet(EVENTS, 7, LINEUPS)
    return {"statut": "termine", "score": "3 - 6", "domicile": "LASK Linz", "exterieur": "Liverpool",
            "feuille_de_match": sheet, "buteurs": {"marques": sheet["buts"]}}


def scorer_leg(pid, name, side="exterieur", **extra):
    return {"match_id": "m", "pari": f"{name} buteur", "cote": 2.0, "buteur": {"id": pid, "nom": name, "cote_equipe": side,
                                                                               **extra}}


def test_settlement_won_lost_void_and_own_goal():
    from sportai.services.pronostic import _settle_leg

    doc = finished_doc()
    assert [g["joueur"] for g in doc["buteurs"]["marques"]] == ["Ronald Araujo", "Bradley Barcola"]
    assert doc["feuille_de_match"]["entres"][0]["joueur"] == "Joe Gomez"
    assert buteurs.settle_scorer(scorer_leg(161904, "Bradley Barcola"), doc) == "gagne"
    assert buteurs.settle_scorer(scorer_leg(1, "Alexander Isak"), doc) == "perdu"  # his own goal is not his
    assert buteurs.settle_scorer(scorer_leg(5, "Mohamed Salah"), doc) == "perdu"  # missed penalty
    assert buteurs.settle_scorer(scorer_leg(284, "Joe Gomez"), doc) == "perdu"  # came on, did not score
    assert buteurs.settle_scorer(scorer_leg(999, "Federico Chiesa"), doc) == "rembourse"  # never played
    # Taken live at the 30th minute: Araujo's 22nd-minute goal does not count any more.
    assert buteurs.settle_scorer(scorer_leg(101814, "Ronald Araujo", apres_minute=30), doc) == "perdu"
    # Not over, or no match sheet: never settled on a guess.
    assert buteurs.settle_scorer(scorer_leg(161904, "Bradley Barcola"), {**doc, "statut": "en_direct"}) is None
    assert buteurs.settle_scorer(scorer_leg(161904, "B"), {"statut": "termine", "score": "3 - 6"}) is None
    # Through the tickets' settlement, score-based legs unchanged.
    page = {"m": doc}
    assert _settle_leg(scorer_leg(999, "Federico Chiesa"), page)["resultat"] == "rembourse"
    assert _settle_leg({"match_id": "m", "pari": "Victoire Liverpool"}, page)["resultat"] == "gagne"
    # By name when the bet has no id (line-up published before the match, names only).
    by_name = {**doc, "feuille_de_match": {k: v for k, v in doc["feuille_de_match"].items() if k != "titulaires"},
               "compositions": {"exterieur": {"titulaires": ["Bradley Barcola", "Mohamed Salah"]}}}
    assert buteurs.settle_scorer({"buteur": {"nom": "Mohamed Salah", "cote_equipe": "exterieur"}}, by_name) == "perdu"


def test_live_doc_records_the_match_sheet_and_scorer_odds():
    from sportai.connectors.base import LiveSnapshot
    from sportai.services.live_page import live_doc

    doc = {"jour": "2026-10-14", "domicile": "LASK Linz", "exterieur": "Liverpool", "statut": "en_direct",
           "buteurs": {"cotes": [{"joueur": "Alexander Isak", "joueur_id": 1, "nom_api": "A. Isak",
                                  "cote_equipe": "exterieur", "part": 0.35, "minutes_par_titularisation": 85.0},
                                 {"joueur": "Ronald Araujo", "joueur_id": 101814, "nom_api": "R. Araújo",
                                  "cote_equipe": "exterieur", "part": 0.05, "minutes_par_titularisation": 90.0},
                                 {"joueur": "Mohamed Salah", "joueur_id": 5, "nom_api": "M. Salah",
                                  "cote_equipe": "exterieur", "part": 0.3, "minutes_par_titularisation": 88.0}]}}
    fx = {"fixture": {"id": 1635674, "status": {"short": "2H", "elapsed": 63}}, "goals": {"home": 0, "away": 2},
          "teams": {"home": {"id": 7, "name": "LASK Linz"}, "away": {"id": 40, "name": "Liverpool"}}}
    snap = LiveSnapshot("api-football", "1635674", 63, 0, 2, NOW, "real", events=EVENTS)
    odds = [{"name": "Anytime Goal Scorer", "values": [{"value": "Alexander Isak", "odd": "6.0"},
                                                       {"value": "Ronald Araujo", "odd": "4.0"},
                                                       {"value": "Mohamed Salah", "odd": "2.60"}]}]
    out = live_doc(doc, fx, snap, {}, ((1.0, 2.6), {"1x2:home": 0.15, "1x2:draw": 0.2, "1x2:away": 0.65}), NOW,
                   odds, LINEUPS)
    assert [g["joueur"] for g in out["buteurs"]["marques"]] == ["Ronald Araujo", "Bradley Barcola"]
    assert out["feuille_de_match"]["titulaires"]["exterieur"][0]["id"] == 101814
    lam, mu = out["direct"]["buts_restants"]
    rows = {r["pari"]: r for r in out["direct"]["cotes_direct"] if r.get("marche") == "Buteur"}
    # Araujo already scored and left the pitch: no row. Isak and Salah on the pitch.
    assert "Ronald Araujo buteur" not in rows
    isak = rows["Alexander Isak buteur"]
    stay = min(max((85 - 63) / (95 - 63), 0.3), 1.0)
    assert isak["proba"] == round(1 - math.exp(-mu * 0.35 * stay), 4) and isak["proba_marche"] == round(1 / 6.0, 4)
    assert isak["statut"] == "opportunite" and "Mohamed Salah buteur" not in rows  # no value at 2.60
    assert isak["buteur"]["apres_minute"] == 63 and isak["buteur"]["id"] == 1


def live_page(rows, minute=63, score="1 - 0", maj=NOW):
    return {"2026-10-14-man-city-real": {
        "jour": "2026-10-14", "statut": "en_direct", "minute": minute, "score": score, "domicile": "Man City",
        "exterieur": "Real Madrid", "competition": "Ligue des champions", "coup_envoi": (NOW - timedelta(hours=1)).isoformat(),
        "feuille_de_match": {"buts": [], "titulaires": {"domicile": [{"id": 1100, "joueur": "Erling Haaland"}]}},
        "buteurs": {"marques": []},
        "direct": {"maj": maj.isoformat(), "proba": {"domicile": 0.7, "nul": 0.2, "exterieur": 0.1},
                   "buts_restants": [0.9, 0.5], "cotes_direct": rows}}}


HAALAND = {"pari": "Erling Haaland buteur", "marche": "Buteur", "cote": 2.10, "proba": 0.55, "proba_marche": 0.4762,
           "valeur": round(0.55 * 2.1 - 1, 4), "statut": "opportunite",
           "buteur": {"id": 1100, "nom": "Erling Haaland", "cote_equipe": "domicile", "part": 0.45,
                      "minutes_par_titularisation": 88.0, "apres_minute": 63}}
OVER = {"pari": "Plus de 2,5 buts", "cote": 2.4, "proba": 0.5, "proba_marche": 0.41, "valeur": 0.2,
        "statut": "opportunite"}


def player_day(decisions=()):
    return {"jour": "2026-10-14", "profil": "prudent", "bankroll_du_jour": 100.0, "kelly_du_jour": 0.125,
            "decisions": list(decisions), "journal": [{"heure": "08:00", "texte": "Mon profil aujourd'hui : prudent."}]}


def test_joueur_stakes_live_once_per_match_half_stake_idempotent():
    later = NOW + timedelta(seconds=30)
    page = live_page([HAALAND, OVER])
    day = joueur.live(player_day(), page, later, {})
    live = [d for d in day["decisions"] if d["type"] == "direct"]
    assert len(live) == 1 and live[0]["joue"]  # one live bet per match: the best value only
    d = live[0]
    assert d["jambes"][0]["pari"] == "Plus de 2,5 buts" and d["jambes"][0]["en_direct"]["minute"] == 63
    k = round(d["valeur"] / (d["cote"] - 1), 4)
    # half the stake (live on trial), within the prudent profile's 2 % per bet, halved too
    assert d["mise"] == round(min(0.125 * 0.5 * k, 0.02 * 0.5) * 100, 2)
    assert joueur._essais(d) == {joueur.DIRECT}
    assert any(e["texte"] == joueur.LIVE_RULE for e in day["journal"])
    assert day["journal"][-1]["texte"].startswith("En direct 63e, 1 - 0 : je prends Plus de 2,5 buts")
    left = {v["pari"]: v for v in day["direct_vus"]}
    assert left["Erling Haaland buteur"]["raison"] == joueur.LIVE_ONE_PER_MATCH
    # Next 2-minute cycle, odds moved: the bet stays taken with its odds, nothing decided again.
    moved = live_page([{**OVER, "cote": 3.0, "valeur": 0.5}, HAALAND], minute=65)
    again = joueur.live(day, moved, later + timedelta(minutes=2), {})
    live2 = [d for d in again["decisions"] if d["type"] == "direct"]
    assert len(live2) == 1 and live2[0]["cote"] == 2.4 and live2[0]["mise"] == d["mise"]
    assert sum(e["texte"].startswith("En direct 6") for e in again["journal"]) == 1


def test_live_scorer_bet_followed_and_settled_and_counted_in_budget(monkeypatch):
    later = NOW + timedelta(seconds=30)
    day = joueur.live(player_day(), live_page([HAALAND]), later, {})
    d = next(d for d in day["decisions"] if d["type"] == "direct")
    assert d["joue"] and d["jambes"][0]["buteur"]["apres_minute"] == 63
    assert "je prends Erling Haaland buteur (Man City - Real Madrid) à 2,10 (modèle 55 %" in day["journal"][-1]["texte"]
    # followed: on the pitch -> the in-play formula; substituted -> 0; scored -> 1
    page = live_page([HAALAND], minute=70)
    page["2026-10-14-man-city-real"]["statut"] = "en_direct"
    later2 = later + timedelta(minutes=7)
    on = joueur.live(day, page, later2, {})
    chance = next(x for x in on["decisions"] if x["type"] == "direct")["direct"]["chance"]
    stay = min(max((88 - 70) / (95 - 70), 0.3), 1.0)
    assert chance == round(1 - math.exp(-0.9 * 0.45 * stay), 4)
    off = live_page([], minute=75)
    off["2026-10-14-man-city-real"]["feuille_de_match"]["sortis"] = [{"id": 1100, "joueur": "Erling Haaland"}]
    assert joueur.leg_live_chance(d["jambes"][0], off["2026-10-14-man-city-real"]) == 0.0
    scored = live_page([], minute=75)
    scored["2026-10-14-man-city-real"]["buteurs"]["marques"] = [{"id": 1100, "joueur": "Erling Haaland", "minute": 71}]
    assert joueur.leg_live_chance(d["jambes"][0], scored["2026-10-14-man-city-real"]) == 1.0
    # After the match, the pre-match pass keeps the live bet, counts it in the day's budget and settles it.
    final = scored["2026-10-14-man-city-real"]
    final.update(statut="termine", score="2 - 0")
    monkeypatch.setattr(joueur, "simulation", lambda *a, **k: {
        "depart": None, "profils": {p: {"scenarios": {s: {"mediane_echeance": 1, "chance_objectif": 0, "risque_ruine": 0}
                                                     for s in joueur.SCENARIOS}} for p in joueur.PROFILS},
        "comparaison": {"scenarios": {s: {"chance_objectif": 0, "risque_ruine": 0} for s in joueur.SCENARIOS}}})
    doc, etat = joueur.run({"jour": "2026-10-14", "top3": [], "tickets": {}}, day, {}, scored,
                           {"depart": 100.0, "objectif": 1e5}, {"profil": "prudent"}, None, NOW + timedelta(hours=3))
    settled = next(x for x in doc["decisions"] if x["type"] == "direct")
    assert settled["resultat"] == "gagne" and settled["gain"] == round(d["mise"] * 1.1, 2)
    assert etat["bankroll"]["actuelle"] == round(100 + settled["gain"], 2)


def test_live_budget_paper_and_stale_data():
    later = NOW + timedelta(seconds=30)
    # The day's budget (prudent: 5 % of 100) already spent before kick-off: no live bet.
    spent = player_day([{"id": "top3:x", "type": "top3", "joue": True, "mise": 5.0, "jambes": []}])
    day = joueur.live(spent, live_page([OVER]), later, {})
    assert not any(d["type"] == "direct" for d in day["decisions"])
    assert day["direct_vus"][0]["raison"].startswith("Budget du jour atteint")
    # Stale live data: never bet on.
    stale = joueur.live(player_day(), live_page([OVER], maj=NOW - timedelta(minutes=10)), later, {})
    assert not any(d["type"] == "direct" for d in stale["decisions"])
    # The live record loses: back to paper, the opportunity is noted (not staked), once per match.
    lost = {"2026-10-1%d" % i: {"decisions": [{
        "id": f"direct:{i}:{j}", "type": "direct", "joue": True, "mise": 1.0, "cote": 2.0, "proba": 0.5,
        "resultat": "perdu", "gain": -1.0, "jambes": [{"match_id": f"{i}{j}", "pari": "x", "cote": 2.0, "valide": False,
                                                      "en_direct": {"minute": 50}, "resultat": "perdu"}]}
        for j in range(5)]} for i in range(0, 4)}
    paper = joueur.live(player_day(), live_page([OVER]), later, lost)
    d = next(d for d in paper["decisions"] if d["type"] == "direct")
    assert not d["joue"] and d["motif"] == "non_valide" and d["mise"] == 0.0
    assert "noté sur papier" in paper["journal"][-1]["texte"]


def test_scorer_legs_enter_on_trial_and_the_all_markets_list(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from sportai.db.models import AnalysisRun, Base
    from sportai.services.dashboard import all_markets_block
    from sportai.services.pronostic import legs_from_db

    def row(name, cote, proba, pid):
        return {"joueur": name, "joueur_id": pid, "nom_api": name, "cote_equipe": "domicile", "equipe": "Man City",
                "cote": cote, "bookmaker": "Bet365", "proba": proba, "proba_marche": round(1 / cote, 4),
                "valeur": round(proba * cote - 1, 4), "part": 0.4, "minutes_par_titularisation": 85.0, "valide": False}

    rows = [row("Erling Haaland", 1.90, 0.58, 1),  # +10 %: kept
            row("Phil Foden", 4.50, 0.26, 2),  # odds above 4.00: out
            row("Rodri", 3.00, 0.30, 3),  # -10 %: out
            row("Omar Marmoush", 2.40, 0.55, 4)]  # 55 % against 42 %: model too far above the market, out
    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    ko = NOW + timedelta(hours=3)
    f = {"match_id": "1", "match": "Man City - Real Madrid", "kickoff": ko.isoformat(), "skipped_reason": None,
         "expected_goals": [1.8, 1.2], "buteurs_cotes": rows}
    db.add(AnalysisRun(kind="prematch", competition="UCL", mode="real", status="completed", started_at=NOW,
                       config={"enabled_markets": []}, triggered_by="test", fixtures=[f]))
    db.commit()
    legs = legs_from_db(db, NOW.date())
    assert [l["pari"] for l in legs] == ["Erling Haaland buteur"]
    leg = legs[0]
    assert leg["marche"] == "Buteur" and not leg["valide"] and leg["validation"] == "non validé"
    assert leg["buteur"]["id"] == 1 and leg["proba_marche"] == round(1 / 1.9, 4) and "ucl" not in leg
    assert joueur._essais({"jambes": [leg]}) == {"Buteur"}
    # every scorer price is listed on the match page with the other markets, "non validé"
    block = all_markets_block(f, [], "Man City", "Real Madrid", {})
    assert {r["pari"] for r in block["analyses"]} == {f"{r['joueur']} buteur" for r in rows}
    assert all(r["validation"] == "non validé" and r["proba_marche_brute"] for r in block["analyses"])
    assert "1/cote" in block["note_buteurs"]


def test_run_fixture_gets_scorer_prices_and_leaves_non_analyse(monkeypatch, tmp_path):
    from sportai.connectors.base import Fixture
    from sportai.services import runs

    monkeypatch.setenv("SPORTAI_PLAYERS_CACHE", str(tmp_path))

    class Api:
        name = "api-football"

        def team_players(self, team, season):
            return {7: LASK, 40: LIVERPOOL}[team]

    fx = Fixture("api-football", "9", "UCL", NOW + timedelta(hours=3), "LASK Linz", "Liverpool", "real",
                 home_id=7, away_id=40)
    raw = [{"fixture": {"id": 9}, "bookmakers": [{"name": "Bet365", "bets": [{"name": "Anytime Goal Scorer", "values": [
        {"value": "Mohamed Salah", "odd": "1.95"}]}]}]}]
    fixtures = [{"match_id": "9", "match": "LASK Linz - Liverpool", "expected_goals": [1.0, 2.6],
                 "non_analyses": [{"marche": "Anytime Goal Scorer"}, {"marche": "Corners"}]},
                {"match_id": "10", "match": "x - y", "expected_goals": [1, 1]}]
    out = runs.scorer_markets(None, Api(), "UCL", raw, [fx], fixtures, NOW)
    assert [r["joueur"] for r in out[0]["buteurs_cotes"]] == ["Mohamed Salah"]
    assert out[0]["non_analyses"] == [{"marche": "Corners"}] and out[1] == fixtures[1]
