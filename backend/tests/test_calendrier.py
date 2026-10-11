from datetime import date, datetime, timezone

from sportai.services import calendrier


def _fx(i, ko, status="NS", home="Rennes", away="Lyon"):
    return {"fixture": {"id": i, "date": ko, "status": {"short": status}}, "league": {"round": "Regular Season - 8"},
            "teams": {"home": {"name": home}, "away": {"name": away}}}


class FakeApi:
    calls = 0

    def season_fixtures(self, comp, season):
        FakeApi.calls += 1
        return [_fx(1, "2026-10-11T18:45:00+00:00"), _fx(2, "2026-10-11T12:00:00+00:00", "FT"),
                _fx(3, "2026-10-11T19:00:00+00:00", "PST"), _fx(4, "2026-10-12T19:00:00+00:00")]


def test_calendar_gives_today_kickoffs_and_is_cached(tmp_path):
    now = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)
    cals = calendrier.refresh(FakeApi(), ["FR1", "BR1"], now, tmp_path)
    assert cals["FR1"]["saison"] == 2026 and cals["BR1"]["saison"] == 2026
    assert dict(calendrier.kickoffs_on(cals, date(2026, 10, 11), now)) == {
        "2026-10-11T18:45:00+00:00": {"FR1", "BR1"}}
    calendrier.refresh(FakeApi(), ["FR1", "BR1"], now, tmp_path)
    assert FakeApi.calls == 2  # still fresh: read from the folder
    assert calendrier.next_days(cals, date(2026, 10, 11), 2)[1] == {"jour": "2026-10-12", "FR1": 1, "BR1": 1}
