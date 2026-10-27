import importlib.util
from pathlib import Path

import sportai.connectors.api_football as api_football

_spec = importlib.util.spec_from_file_location("agent_pass", Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py")
agent_pass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_pass)


class FakeApi:
    def __init__(self, key):
        pass

    def fixtures_on(self, comps, day):
        return [{"teams": {"home": {"name": "Roma"}, "away": {"name": "Cagliari"}},
                 "score": {"halftime": {"home": 1, "away": 0}}}]


def test_final_pass_fills_half_time_score(monkeypatch):
    monkeypatch.setattr(api_football, "ApiFootballProvider", FakeApi)
    current = {"a": {"jour": "2026-10-27", "domicile": "Roma", "exterieur": "Cagliari", "statut": "en_direct"}}
    docs = {"a": {**current["a"], "statut": "termine", "score": "2 - 1"}}
    agent_pass._half_time_scores(["IT1"], current, docs)
    assert docs["a"]["score_mi_temps"] == "1 - 0"


def test_final_pass_keeps_known_half_time_score(monkeypatch):
    monkeypatch.setattr(api_football, "ApiFootballProvider", FakeApi)
    current = {"a": {"jour": "2026-10-27", "domicile": "Roma", "exterieur": "Cagliari", "statut": "termine",
                     "score_mi_temps": "0 - 0"}}
    docs = {}
    agent_pass._half_time_scores(["IT1"], current, docs)
    assert docs == {}
