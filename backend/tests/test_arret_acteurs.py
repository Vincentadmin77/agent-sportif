"""Vincent 2026-11-26: one actor per sport (foot = specialist, basket = Sur-mesure Analytrack); tennis unchanged."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from sportai.services import registre as R

_P = Path(__file__).resolve().parents[1] / "scripts" / "top5.py"
_spec = importlib.util.spec_from_file_location("top5", _P)
top5 = importlib.util.module_from_spec(_spec)
sys.modules["top5"] = top5
_spec.loader.exec_module(top5)
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)


def test_top5_keeps_only_foot_specialist_from_26_11():
    d = top5.build("2026-11-26", {}, {}, {}, {}, NOW)
    assert set(d["foot"]) == {"specialiste"}
    assert "basket" not in d
    assert set(d["tennis"]) == {"modele", "m", "specialiste", "test"}
    before = top5.build("2026-11-25", {}, {}, {}, {}, NOW)
    assert set(before["foot"]) == {"modele", "m", "specialiste", "test"}


def test_finale_keeps_only_basket_surmesure_from_26_11():
    d = top5.build_finale("2026-11-26", {}, {}, {}, {}, NOW)
    assert "foot" not in d
    assert set(d["basket"]) == {"test"}
    assert set(d["tennis"]) == {"finale", "test"}


def test_live_notes_stop_for_foot():
    doc = {"live_a_blanc": [{"pari": "x"}]}
    assert R.capture_live(doc, [], 10, NOW) == [{"pari": "x"}]
