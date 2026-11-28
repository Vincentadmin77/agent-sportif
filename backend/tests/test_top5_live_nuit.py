"""top5_live: a match starting after midnight Paris is listed by the feeds on D+1 but belongs to D (28/11)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("top5_live", Path(__file__).resolve().parents[1] / "scripts" / "top5_live.py")
tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)


def _fx(home, away, when):
    return {"fixture": {"date": when}, "teams": {"home": {"name": home}, "away": {"name": away}}}


def test_foot_keeps_next_night_matches_only(monkeypatch):
    monkeypatch.setenv("API_FOOTBALL_KEY", "x")
    feeds = {"2026-11-27": [_fx("Cannes", "Le Puy Foot", "2026-11-27T20:00:00+01:00")],
             "2026-11-28": [_fx("Fluminense", "Cruzeiro", "2026-11-28T00:00:00+01:00"),
                            _fx("Lens", "Lille", "2026-11-28T21:00:00+01:00")]}
    monkeypatch.setattr(tl, "_get", lambda url, h=None: {"response": feeds[url.split("date=")[1][:10]]})
    got = tl.foot_fixtures("2026-11-27")
    assert set(got) == {"cannes - le puy foot", "fluminense - cruzeiro"}


def test_tennis_keeps_next_night_matches_only(monkeypatch):
    monkeypatch.setenv("API_TENNIS_KEY", "x")
    rows = [{"event_first_player": "A", "event_second_player": "B", "event_date": "2026-11-27", "event_time": "15:00"},
            {"event_first_player": "C", "event_second_player": "D", "event_date": "2026-11-28", "event_time": "02:30"},
            {"event_first_player": "E", "event_second_player": "F", "event_date": "2026-11-28", "event_time": "11:00"}]
    monkeypatch.setattr(tl, "_get", lambda url, h=None: {"result": rows})
    assert set(tl.tennis_fixtures("2026-11-27")) == {"a - b", "c - d"}
