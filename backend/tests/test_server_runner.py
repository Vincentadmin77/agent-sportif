import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import server_runner as sr  # noqa: E402


def fake_run(tmp_path, calls, plan):
    def run(args, page):
        calls.append(args)
        out = tmp_path / f"out{len(calls)}"
        (out / "matchs").mkdir(parents=True)
        if args[0] == "plan":
            return out, json.dumps(plan)
        if args[0] == "live":
            (out / "matchs" / "2026-10-15_a-b.json").write_text(json.dumps({"statut": "termine", "jour": "2026-10-15"}))
            (out / "pronostic").mkdir()
            (out / "pronostic" / "2026-10-12.json").write_text("{}")  # an earlier day: left alone
        if args[0] == "prematch":
            (out / "matchs" / "2026-10-15_a-b.json").write_text(json.dumps({"statut": "a_venir"}))
            (out / "etat.json").write_text('{"message": "ok"}')
        return out, '{"message": "ok"}'
    return run


def test_day_of_steps(tmp_path):
    page = tmp_path / "page"
    (page / "reglages").mkdir(parents=True)
    (page / "reglages" / "championnats.json").write_text('{"actifs": ["FR1", "XX"]}')
    plan = {"kickoffs": {"2026-10-15T19:00:00+00:00": ["FR1"]},
            "steps": [{"at": "2026-10-15T16:00:00+00:00", "kind": "h3", "competitions": ["FR1"]},
                      {"at": "2026-10-15T18:15:00+00:00", "kind": "m45", "competitions": ["FR1"]},
                      {"at": "2026-10-15T19:00:00+00:00", "kind": "live", "competitions": ["FR1"]},
                      {"at": "2026-10-15T21:15:00+00:00", "kind": "final", "competitions": ["FR1"]}]}
    calls = []
    r = sr.Runner(page, run=fake_run(tmp_path, calls, plan))
    r.tick(datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc))  # 07:00 Paris: nothing yet
    assert calls == []
    r.tick(datetime(2026, 10, 15, 5, 55, tzinfo=timezone.utc))  # 07:55 Paris: morning
    assert [c[0] for c in calls] == ["goals-refresh", "prematch", "plan"]
    assert calls[1] == ["prematch", "--matin", "FR1"]
    assert json.loads((page / "etat" / "agent.json").read_text())["message"] == "ok"
    r.tick(datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc))  # no second morning
    assert len(calls) == 3
    r.tick(datetime(2026, 10, 15, 18, 16, tzinfo=timezone.utc))  # h3 too old, m45 runs
    assert calls[3] == ["prematch", "--goals", "FR1"]
    r.tick(datetime(2026, 10, 15, 19, 1, tzinfo=timezone.utc))  # live, match over: settle the day
    assert calls[4] == ["live", "FR1"] and calls[5] == ["pronostic", "--day", "2026-10-15"]
    assert not (page / "pronostic" / "2026-10-12.json").exists()
    r.tick(datetime(2026, 10, 15, 19, 2, tzinfo=timezone.utc))  # under 2 min: no live call
    assert len(calls) == 6
    r.tick(datetime(2026, 10, 15, 19, 4, tzinfo=timezone.utc))  # live again, already settled
    assert calls[6] == ["live", "FR1"] and len(calls) == 7
    # a prematch pass no longer overwrites the finished match
    r.step(["prematch", "FR1"], keep_started=True)
    assert json.loads((page / "matchs" / "2026-10-15_a-b.json").read_text())["statut"] == "termine"
    r2 = sr.Runner(page, run=fake_run(tmp_path, calls, plan))  # restart: nothing twice
    r2.tick(datetime(2026, 10, 15, 21, 20, tzinfo=timezone.utc))
    assert ["scores", "--final", "FR1"] in calls[-2:] and calls.count(["scores", "--final", "FR1"]) == 1
    r2.tick(datetime(2026, 10, 15, 21, 25, tzinfo=timezone.utc))
    assert calls.count(["scores", "--final", "FR1"]) == 1
