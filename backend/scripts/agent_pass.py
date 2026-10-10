"""One scheduled step of the agent, for the routines that drive the results page.

    python scripts/agent_pass.py plan                         # today's kick-offs and pass times (free request)
    python scripts/agent_pass.py prematch --page P --out O FR1 ES1
    python scripts/agent_pass.py scores --page P --out O [--final] FR1

--page is a folder holding the page's current documents (matchs/<id>.json);
--out receives the documents to write back, plus manifest.json. The analysis
database lives in STATE_DB so the 45 min pass can compare with the 3 h pass;
it is copied to a temporary file while the step runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DB = Path(os.getenv("STATE_DB", "/mnt/project-files/agent-sportif/state/agent.db"))
COMPETITIONS = ["FR1", "EN1", "ES1", "DE1", "IT1", "UCL"]


def _load_page(folder: str | None) -> dict[str, dict]:
    if not folder or not (Path(folder) / "matchs").exists():
        return {}
    return {p.stem: json.loads(p.read_text()) for p in (Path(folder) / "matchs").glob("*.json")}


def _write(out: str, docs: dict[str, dict], message: str, now: datetime) -> None:
    d = Path(out) / "matchs"
    d.mkdir(parents=True, exist_ok=True)
    for key, doc in docs.items():
        (d / f"{key}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    (Path(out) / "etat.json").write_text(json.dumps({"maj": now.isoformat(), "message": message}, ensure_ascii=False))
    (Path(out) / "manifest.json").write_text(json.dumps({"matchs": sorted(docs), "message": message}, ensure_ascii=False))
    print(json.dumps({"written": sorted(docs), "message": message}, ensure_ascii=False))


def plan(now: datetime) -> None:
    from sportai.config import get_settings
    from sportai.connectors.http import CachedClient
    from sportai.connectors.the_odds_api import BASE_URL, SPORT_KEYS
    from sportai.services.prematch import LOCAL_TZ

    http = CachedClient(BASE_URL)
    key = get_settings().the_odds_api_key
    by_time: dict[str, set] = defaultdict(set)
    for comp in COMPETITIONS:
        for ev in http.get_json(f"/v4/sports/{SPORT_KEYS[comp]}/events", {"apiKey": key, "dateFormat": "iso"}):
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if ko > now and ko.astimezone(LOCAL_TZ).date() == now.astimezone(LOCAL_TZ).date():
                by_time[ko.isoformat()].add(comp)
    steps = []
    for ko_s, comps in sorted(by_time.items()):
        ko = datetime.fromisoformat(ko_s)
        for kind, at in (("h3", ko - timedelta(hours=3)), ("m45", ko - timedelta(minutes=45))):
            if at > now:
                steps.append({"at": at.isoformat(), "kind": kind, "kickoff": ko_s, "competitions": sorted(comps)})
    if by_time:
        # One final step after the last match settles the whole day (2 credits per competition).
        last = max(datetime.fromisoformat(k) for k in by_time)
        steps.append({"at": (last + timedelta(hours=2, minutes=15)).isoformat(), "kind": "final",
                      "competitions": sorted(set().union(*by_time.values()))})
    merged: dict[tuple[str, str], set] = defaultdict(set)
    for st in steps:
        merged[(st["at"], st["kind"])].update(st["competitions"])
    steps = [{"at": at, "kind": kind, "competitions": sorted(c)} for (at, kind), c in sorted(merged.items())]
    print(json.dumps({"kickoffs": {k: sorted(v) for k, v in sorted(by_time.items())}, "steps": steps}, indent=1))


def prematch(comps: list[str], page: str | None, out: str, now: datetime) -> None:
    work = Path(tempfile.mkdtemp()) / "agent.db"
    if STATE_DB.exists():
        shutil.copy(STATE_DB, work)
    os.environ["DATABASE_URL"] = f"sqlite:///{work}"
    os.environ["DATA_MODE"] = "real"
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    from sportai import manage
    from sportai.config import get_settings
    from sportai.db.session import session_factory
    from sportai.services.dashboard import build_match_docs
    from sportai.services.runs import execute_prematch

    db = session_factory()()
    manage.init(db)
    current, docs, notes = _load_page(page), {}, []
    for comp in comps:
        run = execute_prematch(db, get_settings(), comp, "routine", now=now)
        notes.append(f"{comp} {run.status}" + (f" ({run.status_detail[:120]})" if run.status_detail else ""))
        if run.status == "completed":
            new = build_match_docs(db, run, {**current, **docs})
            docs.update(new)
    db.close()
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(work, STATE_DB)
    n_sel = sum(len(d.get("selections") or []) for d in docs.values())
    _write(out, docs, f"{len(docs)} match(s) mis à jour, {n_sel} sélection(s)", now)
    print("runs:", "; ".join(notes), file=sys.stderr)


def scores(comps: list[str], page: str | None, out: str, final: bool, now: datetime) -> None:
    from sportai.config import get_settings
    from sportai.connectors.the_odds_api import TheOddsApiProvider
    from sportai.services.dashboard import apply_scores

    s = get_settings()
    odds = TheOddsApiProvider(s.the_odds_api_key, regions=s.odds_regions)
    current, docs = _load_page(page), {}
    for comp in comps:
        docs.update(apply_scores(odds.scores(comp, completed_too=final), current, now))
    _write(out, docs, f"scores mis à jour pour {len(docs)} match(s)", now)
    print("quota:", odds.last_quota, file=sys.stderr)


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    for name in ("prematch", "scores"):
        p = sub.add_parser(name)
        p.add_argument("competitions", nargs="+", choices=COMPETITIONS)
        p.add_argument("--page")
        p.add_argument("--out", required=True)
        if name == "scores":
            p.add_argument("--final", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    if a.cmd == "plan":
        plan(now)
    elif a.cmd == "prematch":
        prematch(a.competitions, a.page, a.out, now)
    else:
        scores(a.competitions, a.page, a.out, a.final, now)


if __name__ == "__main__":
    main()
