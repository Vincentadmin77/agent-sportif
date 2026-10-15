"""The agent's day on its own server, without Claude: the same steps as the Claude routines (docs/10-routines.md).

    python scripts/server_runner.py            # runs for ever (systemd service agent-runner)
    python scripts/server_runner.py --once     # one look at the clock, run what is due, then stop

The page is a folder (PAGE_DIR, default /srv/agent-sportif/page) holding the same collections as the
results page: matchs/, pronostic/, stats/, joueur/, reglages/ and etat/agent.json. Each step runs
agent_pass.py with --page PAGE_DIR, then copies what it wrote back into the folder. The website reads
that folder. What the runner has already done today is kept in PAGE_DIR/../runner.json, so a restart
does not run a pass twice.

- 07:50 Paris: goals-refresh, morning analysis of every match of the day (prematch --matin), then plan.
- h3 / m15: prematch; m45: prematch --goals; final: scores --final.
- Live: every 2 min from kick-off to 2 h 30 after it; when a match has just ended, the day's tickets,
  bilan and bankroll are settled at once (owner, 2026-10-11).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
SCRIPT = Path(__file__).resolve().parent / "agent_pass.py"
PAGE_DIR = Path(os.getenv("PAGE_DIR", "/srv/agent-sportif/page"))
COMPETITIONS = ["FR1", "EN1", "ES1", "DE1", "IT1", "BR1", "UCL"]
MORNING = (7, 50)
LIVE_EVERY = timedelta(minutes=2)
LIVE_WINDOW = timedelta(hours=2, minutes=30)
COLLECTIONS = ("matchs", "pronostic", "stats", "joueur")


def log(msg: str) -> None:
    print(f"{datetime.now(PARIS):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def betting_day(moment: datetime) -> date:
    """Same rule as journee(): 00:00-02:00 Paris counts for the evening before."""
    return (moment.astimezone(PARIS) - timedelta(hours=2)).date()


def active_competitions(page: Path) -> list[str]:
    """reglages/championnats chosen on the page; missing or empty: all."""
    try:
        chosen = json.loads((page / "reglages" / "championnats.json").read_text()).get("actifs") or []
    except (OSError, ValueError, AttributeError):
        chosen = []
    return [c for c in chosen if c in COMPETITIONS] or list(COMPETITIONS)


def run_pass(args: list[str], page: Path) -> tuple[Path, str]:
    """Run one agent_pass.py step on a fresh output folder; returns (out folder, last output line)."""
    out = Path(tempfile.mkdtemp(prefix="agent-out-"))
    cmd = [sys.executable, str(SCRIPT), *args]
    if args[0] not in ("plan", "goals-refresh"):
        cmd += ["--page", str(page), "--out", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {res.stderr.strip()[-400:]}")
    lines = [l for l in res.stdout.strip().splitlines() if l.strip()]
    return out, res.stdout if args[0] == "plan" else (lines[-1] if lines else "")


def merge(out: Path, page: Path, *, keep_started: bool = False, only_days: set[str] | None = None) -> list[str]:
    """Copy the documents a step wrote into the page folder; returns the doc ids written.

    keep_started: a prematch pass leaves alone the matches already under way or over (the live loop owns them).
    only_days: a live cycle writes the day documents (pronostic/joueur) of these days only, so an earlier day's
    finished matches still in the page do not rewrite that day.
    """
    written = []
    for sub in COLLECTIONS:
        for src in sorted((out / sub).glob("*.json")):
            dst = page / sub / src.name
            if sub == "matchs" and keep_started and dst.exists():
                try:
                    if json.loads(dst.read_text()).get("statut") in ("en_direct", "termine"):
                        continue
                except ValueError:
                    pass
            if only_days is not None and sub != "stats" and src.stem[:4].isdigit() and src.stem[:10] not in only_days:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)  # readers never see half a file
            written.append(f"{sub}/{src.stem}")
    if (out / "etat.json").exists():
        (page / "etat").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out / "etat.json", page / "etat" / "agent.json")
    shutil.rmtree(out, ignore_errors=True)
    return written


class Runner:
    def __init__(self, page: Path = PAGE_DIR, state_file: Path | None = None, run=run_pass):
        self.page = page
        self.state_file = state_file or page.parent / "runner.json"
        self.run = run
        try:
            self.state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self.state = {}

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=1))

    def step(self, args: list[str], **merge_opts) -> list[str]:
        try:
            out, last = self.run(args, self.page)
        except Exception as e:  # noqa: BLE001 - one failed pass never stops the day
            log(f"ERREUR {e}")
            return []
        written = merge(out, self.page, **merge_opts)
        log(f"{' '.join(args)}: {last[:200]}")
        return written

    def morning(self, now: datetime) -> None:
        comps = active_competitions(self.page)
        try:
            self.run(["goals-refresh"], self.page)
        except Exception as e:  # noqa: BLE001 - without it the passes fall back on Dixon-Coles
            log(f"goals-refresh indisponible: {e}")
        self.step(["prematch", "--matin", *comps])
        self.replan(now)

    def replan(self, now: datetime) -> None:
        try:
            _, raw = self.run(["plan", "--competitions", *active_competitions(self.page)], self.page)
            plan = json.loads(raw[raw.index("{"):])
        except Exception as e:  # noqa: BLE001
            log(f"planning indisponible: {e}")
            return
        self.state.update(plan_day=str(betting_day(now)), kickoffs=plan.get("kickoffs", {}),
                          steps=[s for s in plan.get("steps", []) if s["kind"] != "live"], done=[])
        self.save()
        log(f"planning: {len(plan.get('kickoffs', {}))} horaire(s) de match, {len(self.state['steps'])} passage(s)")

    def live_comps(self, now: datetime) -> list[str]:
        comps: set[str] = set()
        for ko, cs in (self.state.get("kickoffs") or {}).items():
            start = datetime.fromisoformat(ko)
            if start <= now <= start + LIVE_WINDOW:
                comps.update(cs)
        return sorted(comps)

    def live(self, now: datetime, comps: list[str]) -> None:
        written = self.step(["live", *comps], only_days={str(betting_day(now)), str(betting_day(now) - timedelta(days=1))})
        ended = set()
        for doc_id in written:
            if doc_id.startswith("matchs/"):
                try:
                    doc = json.loads((self.page / f"{doc_id}.json").read_text())
                except (OSError, ValueError):
                    continue
                if doc.get("statut") == "termine" and doc_id not in self.state.setdefault("settled", []):
                    ended.add(doc.get("jour") or doc_id[7:17])
                    self.state["settled"].append(doc_id)
        for day in sorted(ended):  # a match just ended: tickets, bilan and bankroll right away
            self.step(["pronostic", "--day", day])
        self.state["settled"] = self.state.get("settled", [])[-200:]

    def tick(self, now: datetime) -> None:
        local = now.astimezone(PARIS)
        today = str(betting_day(now))
        if (local.hour, local.minute) >= MORNING and self.state.get("morning") != today:
            self.state["morning"] = today
            self.save()
            log("analyse du matin")
            self.morning(now)
        for st in self.state.get("steps", []):
            key = f"{st['at']}|{st['kind']}"
            if key in self.state.get("done", []) or datetime.fromisoformat(st["at"]) > now:
                continue
            self.state.setdefault("done", []).append(key)
            self.save()
            if now - datetime.fromisoformat(st["at"]) > timedelta(minutes=30) and st["kind"] != "final":
                log(f"passage {st['kind']} de {st['at']} trop ancien, sauté")
                continue
            comps = st["competitions"]
            if st["kind"] == "final":
                self.step(["scores", "--final", *comps])
            else:
                self.step(["prematch", *(["--goals"] if st["kind"] == "m45" else []), *comps], keep_started=True)
        comps = self.live_comps(now)
        last = self.state.get("last_live")
        if comps and (not last or now - datetime.fromisoformat(last) >= LIVE_EVERY):
            self.state["last_live"] = now.isoformat()
            self.live(now, comps)
        self.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    runner = Runner()
    log(f"agent démarré, page {PAGE_DIR}")
    while True:
        runner.tick(datetime.now(timezone.utc))
        if a.once:
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
