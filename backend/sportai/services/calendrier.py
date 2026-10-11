"""The full season calendar of each followed competition (API-Football), kept in the shared folder.

The planner reads it to know, every morning, which matches are played today and when, so it schedules the
3 h, 45 min and live passes itself. Only today's matches are analysed (owner's rule); the calendar only says
where and when. One request per competition, refreshed when older than six hours (kick-off times move).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sportai.services.prematch import LOCAL_TZ

FOLDER = Path("/mnt/project-files/agent-sportif/data/calendrier")
MAX_AGE = timedelta(hours=6)
NOT_PLAYED = {"NS", "TBD"}
DROPPED = {"PST", "CANC", "ABD"}


def season_for(competition: str, day: date) -> int:
    """API-Football season of a competition on a day (Brazil plays calendar-year seasons)."""
    if competition == "BR1":
        return day.year
    return day.year if day.month >= 7 else day.year - 1


def rows(fixtures: list[dict]) -> list[dict]:
    out = []
    for f in fixtures:
        rnd = str((f.get("league") or {}).get("round") or "")
        out.append({"id": f["fixture"]["id"], "coup_envoi": f["fixture"]["date"],
                    "statut": f["fixture"]["status"]["short"], "domicile": f["teams"]["home"]["name"],
                    "exterieur": f["teams"]["away"]["name"], "journee": rnd})
    return sorted(out, key=lambda r: r["coup_envoi"])


def load(competition: str, folder: Path = FOLDER) -> dict | None:
    p = folder / f"{competition}.json"
    return json.loads(p.read_text()) if p.exists() else None


def refresh(api, competitions: list[str], now: datetime, folder: Path = FOLDER, force: bool = False) -> dict[str, dict]:
    """The calendar of each competition, fetched again when missing, stale or from another season."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for comp in competitions:
        season = season_for(comp, now.astimezone(LOCAL_TZ).date())
        cal = load(comp, folder)
        if (force or not cal or cal.get("saison") != season
                or now - datetime.fromisoformat(cal["maj"]) > MAX_AGE):
            cal = {"competition": comp, "saison": season, "maj": now.isoformat(),
                   "matchs": rows(api.season_fixtures(comp, season))}
            tmp = folder / f".{comp}.json.tmp"
            tmp.write_text(json.dumps(cal, ensure_ascii=False))
            tmp.replace(folder / f"{comp}.json")
        out[comp] = cal
    return out


def kickoffs_on(calendars: dict[str, dict], day: date, after: datetime) -> dict[str, set]:
    """Kick-off times (ISO, UTC) of the matches still to be played on a Paris day, with their competitions."""
    by_time: dict[str, set] = defaultdict(set)
    for comp, cal in calendars.items():
        for m in cal.get("matchs") or []:
            ko = datetime.fromisoformat(m["coup_envoi"])
            if m["statut"] in DROPPED or ko <= after or ko.astimezone(LOCAL_TZ).date() != day:
                continue
            by_time[ko.astimezone(after.tzinfo).isoformat()].add(comp)
    return by_time


def next_days(calendars: dict[str, dict], start: date, days: int = 7) -> list[dict]:
    """How many matches each competition plays on each of the next days (for the planner's log)."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        count = {comp: sum(1 for m in cal.get("matchs") or []
                           if datetime.fromisoformat(m["coup_envoi"]).astimezone(LOCAL_TZ).date() == d
                           and m["statut"] not in DROPPED)
                 for comp, cal in calendars.items()}
        out.append({"jour": f"{d}", **{c: n for c, n in count.items() if n}})
    return out
