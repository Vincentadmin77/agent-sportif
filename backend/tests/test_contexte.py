import gzip
import json
from datetime import datetime, timezone

from sportai.services import contexte as cx

NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)


def _fx(fid, ref, yellow, red=0, league=61, pens=0, home=1, away=2, hg=1, ag=0, status="FT", date="2026-09-01"):
    return {"fixture": {"id": fid, "referee": ref, "date": f"{date}T19:00:00+00:00", "status": {"short": status},
                        "venue": {"id": 7, "name": "Stade", "city": "Nice"}},
            "league": {"id": league, "season": 2026, "country": "France"},
            "teams": {"home": {"id": home, "name": "Nice"}, "away": {"id": away, "name": "Lens"}},
            "goals": {"home": hg, "away": ag}, "score": {"fulltime": {"home": hg, "away": ag}},
            "statistics": [{"team": {"id": home}, "statistics": [{"type": "Yellow Cards", "value": yellow},
                                                                 {"type": "Red Cards", "value": red}]},
                           {"team": {"id": away}, "statistics": [{"type": "Yellow Cards", "value": None}]}],
            "events": [{"type": "Goal", "detail": "Penalty"}] * pens}


def test_referee_index_merges_short_names_and_rates_severity(tmp_path):
    fixtures = ([_fx(i, "Strict Man, France", 7, 1, pens=1) for i in range(12)]
                + [_fx(100 + i, "S. Man", 7) for i in range(3)]
                + [_fx(200 + i, f"Other {i % 4}", 3) for i in range(60)])
    with gzip.open(tmp_path / "fixtures_ids_a.json.gz", "wt") as f:
        json.dump({"meta": {}, "data": {"response": fixtures}}, f)
    index = cx.build_referee_index(cx.referee_rows(tmp_path), NOW)
    assert index["arbitres"]["strict man"]["matchs"] == 15  # "S. Man" counted with "Strict Man"
    prof = cx.referee_profile(index, "S. Man")
    assert prof["profil"] == "sévère" and prof["matchs"] == 15
    assert "siffle souvent des penalties" in prof["phrase"]
    assert cx.referee_profile(index, "Other 1")["profil"] == "clément"
    assert cx.referee_profile(index, "Nobody")["profil"] == "inconnu"
    assert cx.referee_profile(index, None) is None


def _table(rows):
    return [{"league": {"standings": [[{"rank": i + 1, "team": {"id": tid}, "points": pts, "all": {"played": played},
                                        "description": desc, "form": "WWDLW"}
                                       for i, (tid, pts, played, desc) in enumerate(rows)]]}}]


def test_stakes_gaps_and_late_season():
    rows = [(1, 20, 7, "Champions League league stage"), (2, 18, 7, "Champions League league stage"),
            (3, 15, 7, None), (4, 10, 7, None), (5, 6, 7, "Relegation")] + [(9 + i, 5, 7, None) for i in range(10)]
    s = cx.stakes(_table(rows), 3)
    assert s["rang"] == 3 and s["au_dessus"] == {"rang": 2, "zone": "Ligue des champions", "ecart": 3}
    assert s["en_dessous"]["zone"] == "relégation" and s["en_dessous"]["ecart"] == 9 and "enjeu" not in s
    text = cx.stakes_sentence("Lens", s)
    assert text.startswith("Lens est 3e avec 15 pts (7 matchs)") and "3 pts derrière la 2e place" in text
    # 2 matches left out of 10, nothing to win or lose any more
    late = [(1, 30, 8, "Champions League"), (2, 20, 8, None), (3, 5, 8, "Relegation"), (4, 4, 8, "Relegation"),
            (5, 3, 8, "Relegation"), (6, 2, 8, "Relegation")]
    assert cx.stakes(_table(late), 2)["enjeu"] == "sans enjeu"
    assert cx.stakes(_table(rows), 99) is None


def test_cup_phase_below_playoffs_is_elimination():
    rows = [(1, 6, 2, "Promotion - Champions League (Play Offs: 1/8-finals)"),
            (2, 3, 2, "Promotion - Champions League (Play Offs: 1/16-finals)"), (3, 0, 2, None)]
    s = cx.stakes(_table(rows), 3, cup=True)
    assert s["zone"] == "élimination" and s["au_dessus"]["zone"] == "barrages de la phase finale"


def test_series_sentences():
    games = [_fx(i, "R", 0, home=1, hg=2, ag=1, date=f"2026-09-{i + 1:02d}") for i in range(10)]
    s = cx.series(games, 1, "Nice")
    assert s["victoires"] == 10 and s["plus_2_5"] == 10 and s["deux_marquent"] == 10
    assert "Nice : 10 victoires de suite." in s["phrases"]
    assert not any("sans défaite" in p for p in s["phrases"])  # the winning run already says it
    assert cx.series(games[:3], 1, "Nice")["phrases"] == []


class _Http:
    def __init__(self, data):
        self.data, self.calls = data, 0

    def get_json(self, path, params=None, ttl=0):
        self.calls += 1
        return self.data


def test_weather_at_kickoff_and_stadium_cache(tmp_path):
    geo = _Http({"results": [{"name": "Nice", "latitude": 43.7, "longitude": 7.26, "population": 340000}]})
    path = tmp_path / "stades.json"
    pos = cx.stadium_position(geo, {"id": 7, "name": "Allianz Riviera", "city": "Nice"}, "France", path)
    assert pos["lat"] == 43.7 and cx.stadium_position(geo, {"id": 7, "city": "Nice"}, "France", path) == pos
    assert geo.calls == 1
    meteo = _Http({"hourly": {"time": ["2026-10-21T18:00", "2026-10-21T19:00", "2026-10-21T20:00"],
                              "temperature_2m": [12, 11, 10], "precipitation": [0, 5.2, 3], "wind_speed_10m": [10, 20, 40],
                              "wind_gusts_10m": [20, 30, 70], "weather_code": [3, 65, 63]}})
    w = cx.weather_at(meteo, pos, datetime(2026, 10, 21, 19, tzinfo=timezone.utc))
    assert w["pluie_mm_h"] == 5.2 and w["vent_kmh"] == 40 and w["ciel"] == "forte pluie"
    assert "forte pluie" in w["effet"] and "vent fort" in w["effet"]


class _Api:
    def standings(self, league, season):
        raise RuntimeError("down")


class _Down:
    def get_json(self, *a, **k):
        raise RuntimeError("blocked")


def test_build_context_keeps_previous_when_sources_fail():
    src = cx.ContextSources(_Api(), NOW, referees={}, geo=_Down(), meteo=_Down(), calendars={})
    prev = {"meteo": {"phrase": "Météo au coup d'envoi : couvert."}, "classement": {"domicile": None, "exterieur": None}}
    ctx = cx.build_context(src, _fx(1, None, 0, status="NS", date="2026-10-21"), prev)
    assert src.weather_down and ctx["meteo"] == prev["meteo"] and "arbitre" not in ctx
    assert ctx["phrases"] == ["Météo au coup d'envoi : couvert."] and "pas encore pris en compte" in ctx["note"]


# ---------------------------------------------------------------- next match and rotation risk

def _next(fid, date, home, away, league=61, rnd="Regular Season - 12", name="Ligue 1", status="NS"):
    return {"fixture": {"id": fid, "date": f"{date}T19:00:00+00:00", "status": {"short": status}},
            "league": {"id": league, "name": name, "season": 2026, "round": rnd},
            "teams": {"home": {"id": home[0], "name": home[1]}, "away": {"id": away[0], "name": away[1]}}}


LYON, LORIENT, MARSEILLE, BAYERN = (10, "Lyon"), (11, "Lorient"), (12, "Marseille"), (50, "Bayern Munich")


def _l1_table():
    rows = sorted([(MARSEILLE, 22), (LYON, 15), (LORIENT, 6), ((1, "Nice"), 12), ((2, "Lens"), 11)], key=lambda r: -r[1])
    return [{"league": {"standings": [[{"rank": i + 1, "team": {"id": t[0], "name": t[1]}, "points": pts,
                                        "all": {"played": 9}, "description": None}
                                       for i, (t, pts) in enumerate(rows)]]}}]


class _NextApi:
    def __init__(self, nexts, down=False):
        self.nexts, self.down, self.calls = nexts, down, []

    def standings(self, league, season):
        if league != 61:
            raise RuntimeError("no table")
        return _l1_table()

    def team_next(self, team_id, n):
        self.calls.append(team_id)
        if self.down:
            raise RuntimeError("down")
        return self.nexts.get(team_id, [])


def test_importance_levels():
    assert cx.importance({"id": 2, "round": "League Stage - 4"}) == 3
    assert cx.importance({"id": 2, "round": "Round of 16"}) == 4
    assert cx.importance({"id": 61, "round": "Regular Season - 12"}) == 2
    assert cx.importance({"id": 61, "round": "Regular Season - 37"}, {"enjeu": "fort"}) == 3
    assert cx.importance({"id": 66, "name": "Coupe de France", "round": "Round of 64"}) == 1
    assert cx.importance({"id": 66, "name": "Coupe de France", "round": "Semi-finals"}) == 3
    assert cx.importance({"id": 667, "name": "Friendlies Clubs", "round": "Club Friendlies 1"}) == 0
    assert cx.importance({"id": 999, "name": "Some League", "round": "Regular Season - 3"}) == 2


def test_champions_league_three_days_later_is_a_high_risk():
    tonight = _next(1, "2026-10-21", LYON, LORIENT)
    api = _NextApi({LYON[0]: [tonight, _next(2, "2026-10-24", BAYERN, LYON, league=2, rnd="League Stage - 4",
                                              name="UEFA Champions League")],
                    LORIENT[0]: [tonight, _next(3, "2026-10-28", LORIENT, (1, "Nice"))]})
    src = cx.ContextSources(api, NOW, referees={}, geo=_Down(), meteo=_Down(), calendars={})
    ctx = cx.build_context(src, tonight)
    lyon = ctx["prochain_match"]["domicile"]
    assert lyon["adversaire"] == "Bayern Munich" and lyon["jours"] == 3 and lyon["competition"] == "Ligue des champions"
    assert lyon["plus_important"] and lyon["risque_rotation"] == "élevé" and lyon["lieu"] == "exterieur"
    assert lyon["source"] == "api" and lyon["adversaire_plus_fort"] is None  # not in the same table
    assert ("Lyon joue Bayern Munich 3 jours après (Ligue des champions, match plus important) : "
            "risque de rotation élevé.") in ctx["phrases"]
    lorient = ctx["prochain_match"]["exterieur"]
    assert lorient["jours"] == 7 and lorient["risque_rotation"] is None and "phrase" not in lorient
    assert not any("Lorient joue" in p for p in ctx["phrases"])
    assert "rotation élevé" in ctx["note"]


def test_stronger_league_opponent_is_a_moderate_risk_and_calendar_fallback():
    tonight = _next(1, "2026-10-21", LYON, LORIENT)
    # API-Football down: the season calendars give Lyon - Marseille four days later (names only)
    cal = {"FR1": {"saison": 2026, "matchs": [
        {"id": 1, "coup_envoi": "2026-10-21T19:00:00+00:00", "statut": "NS", "domicile": "Lyon", "exterieur": "Lorient"},
        {"id": 9, "coup_envoi": "2026-11-02T20:00:00+00:00", "statut": "NS", "domicile": "Lyon", "exterieur": "Nice"},
        {"id": 8, "coup_envoi": "2026-10-25T19:45:00+00:00", "statut": "NS", "domicile": "Olympique Marseille",
         "exterieur": "Olympique Lyonnais"}]}}
    src = cx.ContextSources(_NextApi({}, down=True), NOW, referees={}, geo=_Down(), meteo=_Down(), calendars=cal)
    lyon = cx.build_context(src, tonight)["prochain_match"]["domicile"]
    assert lyon["source"] == "calendrier" and lyon["adversaire"] == "Olympique Marseille" and lyon["jours"] == 4
    assert lyon["adversaire_plus_fort"] is True and not lyon["plus_important"]
    assert lyon["force_adversaire_suivant"]["rang"] == 1 and lyon["force_adversaire_ce_soir"]["rang"] == 5
    assert lyon["risque_rotation"] == "modéré"
    assert lyon["phrase"] == ("Lyon joue Olympique Marseille 4 jours après (Ligue 1, adversaire plus fort) : "
                              "risque de rotation modéré.")


def test_next_match_kept_from_earlier_pass_and_not_fetched_again():
    tonight = _next(1, "2026-10-21", LYON, LORIENT)
    prev = {"prochain_match": {"domicile": {"equipe": "Lyon", "risque_rotation": "élevé", "phrase": "Lyon joue X."}}}
    api = _NextApi({})
    src = cx.ContextSources(api, NOW, referees={}, geo=_Down(), meteo=_Down(), calendars={})
    ctx = cx.build_context(src, tonight, prev, next_refresh=False)
    assert api.calls == [] and ctx["prochain_match"] == prev["prochain_match"] and "Lyon joue X." in ctx["phrases"]
    # looked up again but nothing found (no next fixture, no calendar): the earlier finding stays
    ctx = cx.build_context(src, tonight, prev)
    assert api.calls == [LYON[0], LORIENT[0]] and ctx["prochain_match"] == prev["prochain_match"]


def test_a_more_important_match_more_than_four_days_later_is_no_risk():
    tonight = _next(1, "2026-10-21", LYON, LORIENT)
    nxt = cx.next_from_fixtures([tonight, _next(2, "2026-10-26", BAYERN, LYON, league=2, rnd="Round of 16")],
                                LYON[0], tonight)
    info = cx.next_match({"id": 10, "name": "Lyon"}, {"id": 11, "name": "Lorient"}, tonight, nxt, 2, [])
    assert info["importance"] == 4 and info["jours"] == 5 and info["risque_rotation"] is None and not info["raisons"]
