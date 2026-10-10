import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from sportai.config import get_settings
from sportai.db.models import AlertRow, Base
from sportai.services.runs import execute_prematch
from sportai.worker import due_prematch, tick_live

T0 = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def test_schedule_and_live_alerts_in_simulation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "simulation")
    get_settings.cache_clear()
    eng = create_engine(f"sqlite:///{tmp_path}/w.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    settings = get_settings()

    assert due_prematch(db, T0, timedelta(hours=6), "simulation") == ["SIM"]
    run = execute_prematch(db, settings, "SIM", "test", now=T0)
    assert run.status == "completed"
    assert due_prematch(db, T0 + timedelta(minutes=5), timedelta(hours=6), "simulation") == []

    # Simulated kick-offs are T0+1h, T0+1h10, ...: the 45-minute pass of the first one opens at T0+15min.
    assert due_prematch(db, T0 + timedelta(minutes=16), timedelta(hours=6), "simulation") == ["SIM"]
    execute_prematch(db, settings, "SIM", "test", now=T0 + timedelta(minutes=16))
    assert due_prematch(db, T0 + timedelta(minutes=20), timedelta(hours=6), "simulation") == []

    assert tick_live(db, settings, T0 + timedelta(minutes=30)) == 0  # nothing has kicked off
    live_now = T0 + timedelta(hours=1, minutes=30)
    n = tick_live(db, settings, live_now)
    assert n >= 1
    alerts = list(db.scalars(select(AlertRow)))
    assert all(a.mode == "simulation" for a in alerts)
    assert tick_live(db, settings, live_now + timedelta(minutes=1)) == 0  # deduplicated
    get_settings.cache_clear()
