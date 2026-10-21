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
    src = cx.ContextSources(_Api(), NOW, referees={}, geo=_Down(), meteo=_Down())
    prev = {"meteo": {"phrase": "Météo au coup d'envoi : couvert."}, "classement": {"domicile": None, "exterieur": None}}
    ctx = cx.build_context(src, _fx(1, None, 0, status="NS", date="2026-10-21"), prev)
    assert src.weather_down and ctx["meteo"] == prev["meteo"] and "arbitre" not in ctx
    assert ctx["phrases"] == ["Météo au coup d'envoi : couvert."] and "pas encore pris en compte" in ctx["note"]
