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
    assert set(d["tennis"]) == {"m"}
    before = top5.build("2026-11-25", {}, {}, {}, {}, NOW)
    assert set(before["foot"]) == {"modele", "m", "specialiste", "test"}


def test_finale_keeps_only_basket_surmesure_from_26_11():
    d = top5.build_finale("2026-11-26", {}, {}, {}, {}, NOW)
    assert "foot" not in d
    assert set(d["basket"]) == {"test"}
    assert "tennis" not in d


def test_m_selection_drops_foot_and_basket_from_26_11():
    reg = {"jour": "2026-11-26", "fiches": {}, "selection_m": [
        {"sport": "foot", "match_id": 1, "pari": "a", "coup_envoi": "2026-11-26T20:00:00Z"}]}
    out = R.selection_m(reg, {"jour": "2026-11-26"}, NOW)
    assert out["selection_m"] == [] and not out.get("selection_m_retires")


def test_m_no_longer_chooses_foot_or_basket_from_26_11():
    reg = {"jour": "2026-11-26", "fiches": {"2026-11-26|1|M": {"sport": "foot"},
                                             "2026-11-26|basket-2|M": {"sport": "basket"},
                                             "2026-11-26|t-3|M": {"sport": "tennis"}}}
    R.m_records(reg, {"9": {"jour": "2026-11-26"}}, NOW, "x")
    R.basket_m_records(reg, {}, NOW, "x")
    assert list(reg["fiches"]) == ["2026-11-26|t-3|M"]
