from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from sportai.config import get_settings
from sportai.db.models import Base, Selection
from sportai.services.movement import classify
from sportai.services.runs import execute_prematch

T0 = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def test_classify_market_moves_against_the_model():
    # Model says 55 %; market was 48 % at the 3 h pass.
    assert classify(0.55, 0.48, 0.52).kind == "confirmation"   # market moved towards the model
    assert classify(0.55, 0.48, 0.44).kind == "against"
    assert classify(0.55, 0.48, 0.485).kind == "stable"        # price has not moved yet
    assert classify(0.30, 0.40, 0.35).kind == "confirmation"   # also works when the model is below the market


def test_second_pass_reports_movement_since_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "simulation")
    get_settings.cache_clear()
    eng = create_engine(f"sqlite:///{tmp_path}/m.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    first = execute_prematch(db, get_settings(), "SIM", "test", now=T0)
    second = execute_prematch(db, get_settings(), "SIM", "test", now=T0 + timedelta(minutes=30))
    assert first.status == second.status == "completed"
    for s in db.scalars(select(Selection).where(Selection.run_id == first.id)):
        assert "Mouvement" not in s.rationale  # nothing to compare with
    for s in db.scalars(select(Selection).where(Selection.run_id == second.id)):
        assert "Mouvement depuis le passage précédent" in s.rationale
    get_settings.cache_clear()
