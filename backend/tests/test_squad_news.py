import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("agent_pass", Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py")
agent_pass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_pass)

NOW = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)


class FakeApi:
    def __init__(self, lineups):
        self._lineups, self.calls = lineups, []

    def lineups(self, fixture_id, ttl=300):
        self.calls.append("lineups")
        return self._lineups

    def team_players(self, team_id, season):
        self.calls.append(f"players {team_id} {season}")
        return [{"player": {"id": team_id * 100, "name": f"Gardien {team_id}"},
                 "statistics": [{"team": {"id": team_id}, "league": {"id": 135, "season": season},
                                 "games": {"appearences": 7, "lineups": 7, "minutes": 630}, "goals": {"total": 0}}]}]

    def injuries(self, fixture_id):
        self.calls.append("injuries")
        return [{"team": {"id": 10}, "player": {"id": 1000, "name": "Gardien 10", "type": "Missing Fixture",
                                                 "reason": "Red Card"}}]


def _fx(minutes_to_ko):
    return {"fixture": {"id": 5, "timestamp": int((NOW + timedelta(minutes=minutes_to_ko)).timestamp())},
            "league": {"id": 135, "season": 2026},
            "teams": {"home": {"id": 10, "name": "Juventus"}, "away": {"id": 20, "name": "Inter"}}}


def test_squad_news_absentees_once_a_day_and_lineups_only_near_kickoff():
    doc = {"domicile": "Juventus", "exterieur": "Inter", "scenario": "Estimation du modèle, pas un pari."}
    api = FakeApi([])
    assert agent_pass._squad_news(api, _fx(180), doc, [], NOW)
    assert "lineups" not in api.calls and api.calls.count("injuries") == 1  # too early for line-ups
    assert doc["absents"]["domicile"][0] == {"joueur": "Gardien 10", "raison": "suspendu", "detail": "Red Card",
                                             "titulaire_habituel": True}
    assert doc["scenario"].endswith("Absents chez Juventus : Gardien 10 (suspendu), dont 1 titulaire habituel.")
    api.calls.clear()
    assert agent_pass._squad_news(api, _fx(60), doc, [], NOW)  # line-ups not out: marked as waiting
    assert api.calls == ["lineups"] and doc["compositions"].get("attente")
    xi = [{"team": {"id": t}, "formation": "4-3-3", "substitutes": [],
           "startXI": [{"player": {"id": t * 10 + i, "name": f"P{t}-{i}", "pos": "D"}} for i in range(11)]} for t in (10, 20)]
    api = FakeApi(xi)
    assert agent_pass._squad_news(api, _fx(50), doc, [], NOW)
    assert "Onze de départ publié. Juventus (4-3-3) : P10-0" in doc["scenario"]
    api.calls.clear()
    assert not agent_pass._squad_news(api, _fx(40), doc, [], NOW)  # nothing left to fetch today
    assert api.calls == []
