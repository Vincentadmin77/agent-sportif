"""Anytime scorer model from the players' decayed history, and the scorer position rule (docs/23-buteurs-stars.md)."""

import gzip
import json
import math
from datetime import datetime, timedelta, timezone

from sportai.engine.football import scorer_history as sh
from sportai.engine.football import scorers as sc
from sportai.services import buteurs, pronostic

DAY = 86400.0
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


def fixture(fid, ts, home=1, away=2, gh=0, ga=0, players=None, league=39, events=None, status="FT", lineups=True):
    """players: [(team, pid, name, pos, minutes, started)]; goals come from events [(team, pid, detail)]."""
    players = players or []
    teams = {}
    for team, pid, name, pos, mins, started in players:
        teams.setdefault(team, []).append({"player": {"id": pid, "name": name}, "statistics": [
            {"games": {"minutes": mins, "position": pos, "substitute": not started}}]})
    lu = [{"team": {"id": t}, "startXI": [{"player": {"id": pid}} for tm, pid, _, _, _, st in players
                                          if tm == t and st]} for t in teams] if lineups else []
    return {"fixture": {"id": fid, "timestamp": ts, "status": {"short": status}}, "league": {"id": league},
            "teams": {"home": {"id": home}, "away": {"id": away}}, "goals": {"home": gh, "away": ga},
            "events": [{"type": "Goal", "detail": d, "team": {"id": t}, "player": {"id": pid}}
                       for t, pid, d in events or []],
            "lineups": lu, "players": [{"team": {"id": t}, "players": ps} for t, ps in teams.items()]}


# --- decay and the order-free state --------------------------------------------------------------------------------

def test_decay_half_life_and_order_free_sums():
    assert math.isclose(sh.decay(90 * DAY, 90), 0.5) and math.isclose(sh.decay(0, 90), 1.0)
    a, b = [T0, 0.0], [T0, 0.0]
    for ts, v in ((T0, 1.0), (T0 + 30 * DAY, 2.0), (T0 + 200 * DAY, 3.0)):
        sh._add(a, ts, [v], 90)
    for ts, v in ((T0 + 200 * DAY, 3.0), (T0, 1.0), (T0 + 30 * DAY, 2.0)):
        sh._add(b, ts, [v], 90)
    want = 3.0 + 2.0 * 0.5 ** (170 / 90) + 1.0 * 0.5 ** (200 / 90)
    assert a[0] == b[0] == T0 + 200 * DAY and math.isclose(a[1], want) and math.isclose(b[1], want)


def test_fixture_goals_own_goals_missed_penalties_and_no_double_count():
    h = sh.History()
    x = fixture(1, T0, gh=3, ga=1, players=[(1, 10, "Striker", "F", 90, True), (1, 11, "Sub", "M", 20, False),
                                             (2, 20, "Defender", "D", 90, True), (2, 21, "Unused", "M", 0, False)],
                events=[(1, 10, "Normal Goal"), (1, 10, "Penalty"), (1, 20, "Own Goal"), (2, 20, "Normal Goal"),
                        (1, 11, "Missed Penalty")])
    assert h.add_fixture(x) and not h.add_fixture(x)  # counted once
    assert not h.add_fixture(fixture(2, T0, status="NS", players=[(1, 10, "Striker", "F", 90, True)]))
    st, sub, d = h.players["10"], h.players["11"], h.players["20"]
    assert st[3] == st[5] == 2 and st[4] == 90 and st[8] == 1 and st[9] == 90  # penalty in, own goal out
    assert sub[3] == 0 and sub[8] == 0  # a missed penalty is no goal; he came on, no start
    assert d[3] == 1 and d[1] == "D" and "21" not in h.players  # 0 minutes: not a match played
    assert h.teams["1"][1:4] == [3, 1, 1] and h.leagues["39"][1:] == [4, 2]


# --- the formula ---------------------------------------------------------------------------------------------------

def season(h, n=30, goals_every=2, start=T0, minutes=90):
    """Team 1 (striker 10, midfielder 12) against team 2, n matches 7 days apart, 2-1 each."""
    for i in range(n):
        ev = [(1, 10, "Normal Goal")] if i % goals_every == 0 else [(1, 12, "Normal Goal")]
        h.add_fixture(fixture(100 + i, start + i * 7 * DAY, gh=2, ga=1, events=ev + [(2, 30, "Normal Goal")],
                              players=[(1, 10, "Star", "F", minutes, True), (1, 12, "Mid", "M", 90, True),
                                       (2, 30, "Other", "F", 90, True)]))


def test_rate_shrinks_to_the_position_prior_and_blends_form_and_career():
    h = sh.History()
    season(h)
    ts = T0 + 30 * 7 * DAY
    att = h.team_att(1, ts)
    lavg = h.league_avg(39, ts)
    assert 1.35 < lavg < 1.5 and lavg < att < 2.0  # team 1 scores 2 a match, shrunk to the league
    rec = h.players["10"]
    d = lambda hl: sh.decay(ts - rec[2], hl)  # noqa: E731
    prior = 0.22 * att
    form = (rec[3] * d(90) + 16 * prior) / (rec[4] * d(90) / 90 + 16)
    long = (rec[5] * d(3650) + 20 * prior) / (rec[6] * d(3650) / 90 + 20)
    assert math.isclose(h.rate90(10, ts, 1), 0.25 * form + 0.75 * long)
    # 15 goals in 30 matches (0.5 / 90) pulled towards 0.22 * att (about 0.4): between the two
    assert prior < h.rate90(10, ts, 1) < 0.5
    # a newcomer with one match: almost the prior; the line-up's position decides the prior
    h.add_fixture(fixture(999, ts - DAY, players=[(1, 50, "New", "M", 90, True)]))
    att = h.team_att(1, ts)
    assert math.isclose(h.rate90(50, ts, 1), 0.08 * att * (0.25 * 16 / 17 + 0.75 * 20 / 21), rel_tol=1e-3)
    assert h.rate90(50, ts, 1, position="F") > 2.5 * h.rate90(50, ts, 1)
    assert h.rate90(4242, ts) is None


def test_multiplier_opponent_defence_and_venue():
    h = sh.History()
    season(h)
    ts = T0 + 30 * 7 * DAY
    # team 2 concedes 2 a match: a weak defence, above 1
    home, src = h.multiplier(1, 2, True, ts, 39)
    away, _ = h.multiplier(1, 2, False, ts, 39)
    conc = (h.teams["2"][2] * sh.decay(ts - h.teams["2"][0], 150) + 5 * h.league_avg(39, ts)) / (
        h.teams["2"][3] * sh.decay(ts - h.teams["2"][0], 150) + 5)
    assert src == "adversaire" and math.isclose(home, conc / h.league_avg(39, ts) * 1.15)
    assert math.isclose(home / away, 1.15 ** 2) and home > 1.15
    # an opponent not in the history: the match model's goals over the team's usual goals, bounded
    m, src = h.multiplier(1, 777, True, ts, 2, team_xg=h.team_att(1, ts))
    assert src == "buts attendus du match" and math.isclose(m, 1.0)
    assert h.multiplier(1, 777, True, ts, 2, team_xg=50)[0] == sh.XG_MULT_BOUNDS[1]
    assert h.multiplier(555, 777, False, ts)[0] == (1 / 1.15, "neutre")[0]


def test_expected_minutes_when_starting():
    h = sh.History()
    season(h, n=10, minutes=60)
    ts = T0 + 10 * 7 * DAY
    m = h.start_minutes(10, ts)
    w = sum(sh.decay(ts - (T0 + i * 7 * DAY), 180) for i in range(10))
    assert math.isclose(m, (60 * w + 3 * 80) / (w + 3)) and 60 < m < 66
    assert 87 < h.start_minutes(12, ts) < 90  # always 90, the prior of 80 weighs 3 starts
    for i in range(10):  # extra time every match: 120 minutes counted, the expectation capped at 90
        h.add_fixture(fixture(500 + i, T0 + i * DAY, players=[(1, 60, "Iron", "D", 120, True)]))
    assert h.start_minutes(60, ts) == 90
    h.add_fixture(fixture(998, ts - DAY, players=[(1, 51, "Bench", "M", 15, False)]))
    assert h.start_minutes(51, ts) == 80  # no start yet: the prior


def test_probability_is_the_study_formula():
    lam = 0.975 * 0.6 * 80 / 90 * 1.2
    assert math.isclose(sh.star_proba(0.6, 1.2, 80), 1 - math.exp(-lam))
    assert sh.star_proba(0.6, 1.2, 120) == sh.star_proba(0.6, 1.2, 90) and sh.star_proba(0.6, 1.2, 0) == 0


# --- pricing a fixture ---------------------------------------------------------------------------------------------

def squads():
    item = lambda pid, name, last, pos, team: {  # noqa: E731
        "player": {"id": pid, "name": name, "firstname": "", "lastname": last},
        "statistics": [{"team": {"id": team}, "league": {"id": 39, "season": 2026},
                        "games": {"appearences": 8, "lineups": 8, "minutes": 700, "position": pos},
                        "goals": {"total": 2}}]}
    return {"domicile": sc.squad_from_api([item(10, "S. Star", "Star", "Attacker", 1),
                                           item(12, "M. Mid", "Mid", "Midfielder", 1)], 1, 2026),
            "exterieur": sc.squad_from_api([item(30, "O. Other", "Other", "Attacker", 2),
                                            item(31, "U. Unknown", "Unknown", "Attacker", 2)], 2, 2026)}


def test_price_scorers_uses_the_history_and_falls_back_to_the_season_share():
    h = sh.History()
    season(h)
    ts = T0 + 30 * 7 * DAY
    odds = {"Sam Star": {"cote": 2.1, "bookmaker": "Bet365"}, "Uli Unknown": {"cote": 3.5, "bookmaker": "Bet365"}}
    xg = {"domicile": 1.8, "exterieur": 1.2}
    ctx = {"equipes": {"domicile": 1, "exterieur": 2}, "ts": ts, "ligue": 39}
    lineup = {"domicile": {10: "titulaire", 12: "titulaire"}, "exterieur": {30: "titulaire", 31: "titulaire"}}
    rows, dropped = sc.price_scorers(odds, squads(), xg, lineup, h, ctx)
    star = next(r for r in rows if r["joueur_id"] == 10)
    other = next(r for r in rows if r["joueur_id"] == 31)
    rate, mult, mins = h.rate90(10, ts, 1), h.multiplier(1, 2, True, ts, 39)[0], h.start_minutes(10, ts)
    assert star["modele"] == "historique" and star["composition"] == "titulaire"
    assert star["proba"] == round(sh.star_proba(rate, mult, mins), 4)
    # "part" keeps the live page's formula: team goals x part x minutes / 90 gives the same chance
    assert math.isclose(1 - math.exp(-1.8 * star["part"] * mins / 90), star["proba"], abs_tol=2e-4)
    assert star["minutes_par_titularisation"] == round(mins, 1)
    assert other["modele"] == "saison" and "buts_90" not in other  # not in the history: this-season share
    # without a history, everyone on the season share
    rows2, _ = sc.price_scorers(odds, squads(), xg, lineup)
    assert {r["modele"] for r in rows2} == {"saison"}


def test_one_price_per_player_and_suspicious_lists_dropped():
    sq = squads()
    odds = sc.best_scorer_odds([
        {"name": "Bet365", "bets": [{"name": "Anytime Goal Scorer", "values": [
            {"value": "Sam Star", "odd": "2.0"}, {"value": "S. Star", "odd": "2.2"},
            {"value": "Uli Unknown", "odd": "3.0"}]}]},
        {"name": "Betano", "bets": [{"name": "Anytime Goal Scorer", "values": [
            {"value": "Sam Star", "odd": "1.6"}, {"value": "Mid", "odd": "1.7"}, {"value": "Oli Other", "odd": "2.0"},
            {"value": "Uli Unknown", "odd": "2.1"}, {"value": "M. Mid", "odd": "1.9"}]}]}])
    clean, dropped = sc.clean_scorer_odds(odds, sq)
    # two spellings of Star: one row, the best price of the bookmakers kept (Bet365 2.2)
    assert clean[("domicile", 10)]["cote"] == 2.2 and clean[("domicile", 10)]["bookmaker"] == "Bet365"
    assert sum(1 for k in clean if k[1] == 10) == 1
    # Betano prices 2 home players under 2.30 (fine) but the away side too: 2 is not more than 3, kept
    assert clean[("exterieur", 30)]["bookmaker"] == "Betano"
    # 4 players of one team under 2.30 from one bookmaker: that list is another market, left out
    odds["Kim Kalu"] = {"cote": 1.8, "bookmaker": "Betano", "bookmakers": 1, "prix": {"Betano": 1.8}}
    odds["Bo Bako"] = {"cote": 2.25, "bookmaker": "Betano", "bookmakers": 1, "prix": {"Betano": 2.25}}
    sq["domicile"].players[13] = sc.PlayerSeason(13, "K. Kalu", "Kim", "Kalu")
    sq["domicile"].players[14] = sc.PlayerSeason(14, "B. Bako", "Bo", "Bako")
    clean, dropped = sc.clean_scorer_odds(odds, sq)
    assert clean[("domicile", 10)]["bookmaker"] == "Bet365" and ("domicile", 12) not in clean
    assert any("Betano (domicile)" in d for d in dropped)
    assert clean[("exterieur", 30)]["bookmaker"] == "Betano"  # the other team's list is not touched


def test_history_file_built_and_updated_incrementally(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()

    def write(name, fixtures):
        with gzip.open(folder / name, "wt") as f:
            json.dump({"meta": {"params": {"ids": "x"}}, "data": {"response": fixtures}}, f)

    pl = [(1, 10, "Star", "F", 90, True)]
    write("fixtures_ids_a.json.gz", [fixture(1, T0, gh=1, players=pl, events=[(1, 10, "Normal Goal")])])
    out = tmp_path / "h.json.gz"
    r = buteurs.update_history(folder, out)
    assert r["matchs"] == 1 and r["fichiers"] == 1 and out.exists()
    # a new file, holding the same fixture again and a new one: only the new one counts
    write("fixtures_ids_b.json.gz", [fixture(1, T0, gh=1, players=pl, events=[(1, 10, "Normal Goal")]),
                                     fixture(2, T0 + DAY, gh=1, players=pl, events=[(1, 10, "Normal Goal")])])
    r = buteurs.update_history(folder, out)
    assert r["matchs"] == 1 and r["fichiers"] == 1 and r["total_matchs"] == 2
    assert buteurs.update_history(folder, out)["fichiers"] == 0
    h = buteurs.load_history(out)
    assert math.isclose(h.players["10"][5], 1 + 0.5 ** (1 / 3650), rel_tol=1e-4)
    assert buteurs.update_history(folder, out, rebuild=True)["total_matchs"] == 2


# --- the scorer position -------------------------------------------------------------------------------------------

NOW = datetime(2026, 10, 28, 18, tzinfo=timezone.utc)


def leg(cote, proba, composition="titulaire", minutes_to_ko=40, marche="Résultat du match", buteur=True, pm=None,
        match="a-b"):
    out = {"match_id": f"2026-10-28-{match}", "match": match, "competition": "Bundesliga",
           "coup_envoi": (NOW + timedelta(minutes=minutes_to_ko)).isoformat(), "cote": cote, "bookmaker": "Bet365",
           "proba": proba, "proba_marche": round(1 / cote, 4) if pm is None else pm,
           "valeur": round(proba * cote - 1, 4), "_divergence_max": 0.10, "_p_cmp": proba}
    if buteur:
        out.update(pari="Buteur : H. Kane", marche="Buteur", valide=False, validation="non validé",
                   buteur={"id": 1, "nom": "Harry Kane", "composition": composition})
    else:
        out.update(pari="Victoire a", marche=marche, valide=True)
    return out


def test_scorer_pick_rule():
    ok = pronostic.pick_ok
    assert ok(leg(2.22, 0.535))  # Kane 21/10: 1/2.22 = 0.45, +8.5 points
    assert not ok(leg(2.22, 0.535, composition="inconnue"))  # line-up not read: never
    assert not ok(leg(2.22, 0.535, composition="remplacant"))  # on the bench: never
    assert not ok(leg(2.0, 0.52))  # 2 points over 1/odds: not enough value
    assert not ok(leg(2.0, 0.62))  # 12 points over: model and market too far apart
    assert not ok(leg(1.45, 0.75)) and not ok(leg(2.40, 0.45))  # odds outside 1.50-2.30


def test_scorer_position_only_once_fixed_and_after_result_markets():
    no = lambda l: False  # noqa: E731
    pick = lambda legs: pronostic.build_picks(legs, {}, None, NOW, NOW.date(), no, no)[0]["2026-10-28-a-b"]  # noqa
    alone = leg(2.22, 0.535)
    assert pick([alone])["pari"] == "Buteur : H. Kane"  # no result/goals position: the scorer
    assert pick([alone])["validation"] == "non validé"
    assert pick([leg(2.22, 0.535, minutes_to_ko=120)])["pari"] is None  # 2 h before: not yet
    # a result position the match already has keeps priority unless the scorer is COUNT_MARGIN surer
    result = leg(1.80, 0.60, buteur=False, pm=0.55)
    assert pick([alone, result])["pari"] == "Victoire a"
    weak = leg(1.95, 0.53, buteur=False, pm=0.50)  # 0.515 confidence; the scorer 0.49: not 0.10 surer
    assert pick([alone, weak])["pari"] == "Victoire a"
    assert not pronostic.top_ok({**alone, "pari": "x"})
