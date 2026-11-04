import importlib.util
import shutil
import sqlite3
from pathlib import Path

_spec = importlib.util.spec_from_file_location("agent_pass", Path(__file__).resolve().parents[1] / "scripts" / "agent_pass.py")
agent_pass = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_pass)


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE analysis_runs (id TEXT PRIMARY KEY, competition TEXT)")
    con.execute("CREATE TABLE provider_status (provider TEXT PRIMARY KEY, quota TEXT)")
    con.execute("INSERT INTO analysis_runs VALUES ('a', 'UCL')")
    con.execute("INSERT INTO provider_status VALUES ('odds', '100')")
    con.commit()
    con.close()


def test_two_passes_side_by_side_keep_both_runs(tmp_path, monkeypatch):
    state = tmp_path / "agent.db"
    _db(state)
    monkeypatch.setattr(agent_pass, "STATE_DB", state)
    work1, work2 = tmp_path / "w1.db", tmp_path / "w2.db"
    shutil.copy(state, work1)
    shutil.copy(state, work2)  # both passes start from the same copy
    for work, run, quota in ((work1, "b", "90"), (work2, "c", "80")):
        con = sqlite3.connect(work)
        con.execute("INSERT INTO analysis_runs VALUES (?, 'BR1')", (run,))
        con.execute("UPDATE provider_status SET quota=?", (quota,))
        con.execute("CREATE TABLE IF NOT EXISTS extra (id TEXT PRIMARY KEY)")
        con.commit()
        con.close()
    agent_pass._merge_back(work1)
    agent_pass._merge_back(work2)  # a plain copy here used to erase run "b"
    con = sqlite3.connect(state)
    assert {r[0] for r in con.execute("SELECT id FROM analysis_runs")} == {"a", "b", "c"}
    assert con.execute("SELECT quota FROM provider_status").fetchone()[0] == "80"
    assert con.execute("SELECT count(*) FROM extra").fetchone()[0] == 0


def test_first_pass_copies_the_file(tmp_path, monkeypatch):
    state, work = tmp_path / "state" / "agent.db", tmp_path / "w.db"
    _db(work)
    monkeypatch.setattr(agent_pass, "STATE_DB", state)
    agent_pass._merge_back(work)
    assert sqlite3.connect(state).execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 1
