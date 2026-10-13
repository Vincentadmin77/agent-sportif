"""Daily refresh of the enriched goals model (BTTS, over/under 1.5 / 2.5 / 3.5).

1. Data: API-Football results + per-match statistics, cached on disk (one
   gzipped JSON per request, never downloaded twice; the same cache as
   scripts/backtest_goals.py). A refresh downloads the current-season fixture
   list of each league (a new dated snapshot file) and the statistics of the
   finished matches not cached yet, within a small request budget.
2. Fit: GoalsModel (sportai/engine/football/goals_model.py) on every cached
   season, as of now.
3. Save: the fitted coefficients and, for every (home, away) pair of each
   league's current season, the enriched and plain Dixon-Coles probabilities
   as of the refresh, to a JSON file the pre-match passes read the same day
   (Europe/Paris). A pass never uses a file from another day.

Only over/under 1.5 and 3.5 passed the out-of-sample backtest (fragile, see
docs/11-backtest-buts.md); BTTS and 2.5 are priced by the same model because it
beats plain Dixon-Coles on them, but stay "non validé".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sportai.connectors.teams import normalise
from sportai.engine.football import dixon_coles as dc
from sportai.engine.football import goals_model as gm

BASE_URL = "https://v3.football.api-sports.io"
LEAGUES = {"FR1": 61, "EN1": 39, "ES1": 140, "DE1": 78, "IT1": 135, "BR1": 71}
CALENDAR_YEAR = {"BR1"}
FINISHED = ("FT", "AET", "PEN")


def _journee(now: datetime):
    from sportai.services.prematch import journee  # late import: prematch loads this module

    return journee(now)
DEFAULT_CACHE = "/mnt/project-files/agent-sportif/data/api-football"
DEFAULT_MODEL_PATH = "/mnt/project-files/agent-sportif/state/goals_model.json"
MAX_REQUESTS_PER_RUN = 60
MIN_REMAINING = 3000
MODEL_LABEL = f"{gm.MODEL_NAME} {gm.MODEL_VERSION}"
DC_LABEL = f"{dc.MODEL_NAME} {dc.MODEL_VERSION}"


def cache_dir() -> Path:
    return Path(os.getenv("SPORTAI_GOALS_CACHE", DEFAULT_CACHE))


def model_path() -> Path:
    return Path(os.getenv("GOALS_MODEL_PATH", DEFAULT_MODEL_PATH))


def current_season(comp: str, now: datetime) -> int:
    if comp in CALENDAR_YEAR:
        return now.year
    return now.year if now.month >= 7 else now.year - 1


class BudgetExceeded(Exception):
    pass


class ApiFootballCache:
    """API-Football GET /fixtures with a per-run request budget; every response is written to a new file."""

    def __init__(self, folder: Path, api_key: str | None, max_requests: int = MAX_REQUESTS_PER_RUN,
                 transport: httpx.BaseTransport | None = None, pause: float = 0.25):
        self.folder, self.key, self.max_requests = folder, api_key, max_requests
        self.used = 0
        self.remaining: int | None = None
        self.pause = pause
        self._client = httpx.Client(base_url=BASE_URL, timeout=90, transport=transport,
                                    headers={"x-apisports-key": api_key or ""})

    def get(self, params: dict, filename: str) -> dict:
        path = self.folder / filename
        if path.exists():
            return _read(path)
        if not self.key:
            raise BudgetExceeded("API_FOOTBALL_KEY is not set")
        for _ in range(4):
            if self.used >= self.max_requests:
                raise BudgetExceeded(f"request budget of {self.max_requests} reached")
            if self.remaining is not None and self.remaining < MIN_REMAINING:
                raise BudgetExceeded(f"daily quota remaining {self.remaining} < {MIN_REMAINING}")
            r = self._client.get("/fixtures", params=params)
            self.used += 1
            self._ledger(filename, r.status_code)
            rem = r.headers.get("x-ratelimit-requests-remaining")
            self.remaining = int(rem) if rem and rem.isdigit() else self.remaining
            if r.status_code != 200:
                raise RuntimeError(f"api-football HTTP {r.status_code} on {filename}")
            data = r.json()
            errs = data.get("errors")
            if isinstance(errs, dict) and "rateLimit" in errs:
                time.sleep(30 if self.pause else 0)  # per-minute limit, shared with the live product
                continue
            if errs:
                raise RuntimeError(f"api-football refused {filename}: {errs}")
            break
        else:
            raise BudgetExceeded("rate limited repeatedly")
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as f:
            json.dump({"meta": {"params": params, "fetched_at": datetime.now(timezone.utc).isoformat(),
                                "requests_remaining": self.remaining}, "data": data}, f)
        tmp.rename(path)
        time.sleep(self.pause)
        return data

    def _ledger(self, name: str, status: int) -> None:
        try:
            with open(self.folder / "_requests_ledger.log", "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()} {status} {name}\n")
        except OSError:
            pass


def _read(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)["data"]


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).rstrip("%"))
    except ValueError:
        return None


def fixture_stats(f: dict) -> dict:
    home_id = f["teams"]["home"]["id"]
    res: dict = {"home": {}, "away": {}}
    for block in f.get("statistics") or []:
        side = "home" if block["team"]["id"] == home_id else "away"
        for s in block.get("statistics") or []:
            if s.get("type") in ("expected_goals", "Shots on Goal", "Total Shots"):
                res[side][s["type"]] = _num(s.get("value"))
    return res


def _finished(f: dict) -> bool:
    return f["fixture"]["status"]["short"] in FINISHED and f["score"]["fulltime"]["home"] is not None


def scan_cache(folder: Path) -> tuple[dict[tuple[int, int], dict], dict[int, dict]]:
    """Latest fixture list per (league id, season) and statistics per fixture id, from the cache."""
    lists: dict[tuple[int, int], tuple[str, dict]] = {}
    for p in folder.glob("fixtures_league*_season*.json.gz"):
        stem = p.name[len("fixtures_league"):-len(".json.gz")]
        league_s, _, rest = stem.partition("_season")
        season_s, _, stamp = rest.partition("_asof")
        key = (int(league_s), int(season_s))
        if key not in lists or stamp > lists[key][0]:  # the base file has an empty stamp: any snapshot is newer
            lists[key] = (stamp, p)
    stats: dict[int, dict] = {}
    for p in folder.glob("fixtures_ids_*.json.gz"):
        for f in _read(p).get("response", []):
            stats[f["fixture"]["id"]] = fixture_stats(f)
    return {k: _read(p) for k, (_, p) in lists.items()}, stats


def matches_from(data: dict, stats: dict[int, dict], comp: str, season: int) -> list[gm.Match]:
    out = []
    for f in data.get("response", []):
        # regular season only: play-offs mix in lower-division teams
        if not _finished(f) or not str(f["league"].get("round", "")).startswith("Regular Season"):
            continue
        ft = f["score"]["fulltime"]
        m = gm.Match(f["fixture"]["id"], comp, season,
                     datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).astimezone(timezone.utc),
                     f["teams"]["home"]["name"], f["teams"]["away"]["name"], int(ft["home"]), int(ft["away"]))
        s = stats.get(m.fid)
        if s:
            m.xg_h, m.xg_a = s["home"].get("expected_goals"), s["away"].get("expected_goals")
            m.sot_h, m.sot_a = s["home"].get("Shots on Goal"), s["away"].get("Shots on Goal")
            m.sh_h, m.sh_a = s["home"].get("Total Shots"), s["away"].get("Total Shots")
        out.append(m)
    return out


def season_teams(data: dict) -> list[str]:
    teams = set()
    for f in data.get("response", []):
        if str(f["league"].get("round", "")).startswith("Regular Season"):
            teams |= {f["teams"]["home"]["name"], f["teams"]["away"]["name"]}
    return sorted(teams)


def refresh_data(now: datetime, api: ApiFootballCache | None, leagues=tuple(LEAGUES)) -> tuple[dict, dict, dict]:
    """Download the current-season lists and the missing statistics (if api is given), then load everything.

    Returns (history by league, current-season teams by league, report)."""
    folder = cache_dir()
    report: dict = {"requests": 0, "new_stats": 0, "errors": []}
    if api is not None:
        stamp = now.strftime("%Y%m%d%H%M")
        _, stats = scan_cache(folder)
        for comp in leagues:
            season = current_season(comp, now)
            lid = LEAGUES[comp]
            try:
                data = api.get({"league": lid, "season": season}, f"fixtures_league{lid}_season{season}_asof{stamp}.json.gz")
            except (BudgetExceeded, RuntimeError, httpx.HTTPError) as e:
                report["errors"].append(f"{comp} list: {e}")
                continue
            missing = sorted(f["fixture"]["id"] for f in data.get("response", [])
                             if _finished(f) and f["fixture"]["id"] not in stats)
            for i in range(0, len(missing), 20):
                chunk = "-".join(map(str, missing[i:i + 20]))
                name = f"fixtures_ids_{hashlib.sha1(chunk.encode()).hexdigest()[:16]}.json.gz"
                try:
                    for f in api.get({"ids": chunk}, name).get("response", []):
                        stats[f["fixture"]["id"]] = fixture_stats(f)
                        report["new_stats"] += 1
                except (BudgetExceeded, RuntimeError, httpx.HTTPError) as e:
                    report["errors"].append(f"{comp} stats: {e}")
                    break
        report["requests"] = api.used
        report["quota_remaining"] = api.remaining
    lists, stats = scan_cache(folder)
    history: dict[str, list[gm.Match]] = defaultdict(list)
    teams: dict[str, list[str]] = {}
    for comp in leagues:
        lid, cur = LEAGUES[comp], current_season(comp, now)
        for (l, s), data in sorted(lists.items()):
            if l == lid and s <= cur:
                history[comp] += matches_from(data, stats, comp, s)
        if (lid, cur) in lists:
            teams[comp] = season_teams(lists[(lid, cur)])
        history[comp].sort(key=lambda m: (m.kickoff, m.fid))
    return dict(history), teams, report


def build_book(history: dict[str, list[gm.Match]], teams: dict[str, list[str]], now: datetime) -> dict:
    """Fit GoalsModel as of now and price every current-season pair of each league."""
    model = gm.GoalsModel.fit(history, now)
    leagues = {}
    for comp, ms in history.items():
        names = teams.get(comp) or []
        past = [m for m in ms if m.kickoff < now]
        train = [dc.MatchResult(m.day, m.home, m.away, m.hg, m.ag) for m in past
                 if (now.date() - m.day).days <= 3 * 365]
        if len(train) <= 200 or not names:
            continue
        dcm = dc.fit(train, reference_date=now.date(), with_uncertainty=False)
        season = current_season(comp, now)
        pairs = model.predict_pairs(past, [(h, a) for h in names for a in names if h != a], now, season, dcm)
        leagues[comp] = {
            "season": season, "teams": names, "n_matches": len(past),
            "unknown_teams": [t for t in names if not dcm.knows(t)],
            "pairs": {f"{h}|{a}": {k: round(v, 5) if isinstance(v, float) else
                                   {kk: round(vv, 5) for kk, vv in v.items()} for k, v in p.items()}
                      for (h, a), p in pairs.items()},
        }
    return {"model": MODEL_LABEL, "fitted_at": now.isoformat(), "day": f"{_journee(now):%Y-%m-%d}",
            "validated_markets": list(gm.VALIDATED_MARKETS), "n_train": model.n_train,
            "coefficients": model.to_dict(), "leagues": leagues}


def save_book(book: dict, path: Path | None = None) -> Path:
    path = path or model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False))
    tmp.rename(path)
    return path


# ------------------------------------------------------------------ pre-match side


def enriched_labels(p: dict) -> dict[str, float]:
    """Market labels (as in markets.MarketKey.label) from the enriched probabilities."""
    out = {"btts:yes": p["btts_yes"], "btts:no": 1.0 - p["btts_yes"]}
    for line in (1.5, 2.5, 3.5):
        over = p[f"over_{line}"]
        out[f"totals:over@{line:g}"], out[f"totals:under@{line:g}"] = over, 1.0 - over
    return out


@dataclass
class GoalsBook:
    data: dict

    @classmethod
    def load(cls, now: datetime, path: Path | None = None) -> GoalsBook | None:
        """The saved book if it was built this betting day (08:00-02:00 Paris), else None (passes then use plain DC)."""
        path = path or model_path()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if data.get("day") != f"{_journee(now):%Y-%m-%d}" or data.get("model") != MODEL_LABEL:
            return None
        return cls(data)

    def lookup(self, comp: str, home: str, away: str) -> tuple[dict[str, float] | None, str | None]:
        """(probabilities by market label, None) or (None, reason it fell back to Dixon-Coles)."""
        lg = self.data.get("leagues", {}).get(comp)
        if not lg:
            return None, f"pas de modèle de buts pour {comp}"
        index = {normalise(t): t for t in lg["teams"]}
        h, a = index.get(normalise(home)), index.get(normalise(away))
        if h is None or a is None:
            miss = [n for n, t in ((home, h), (away, a)) if t is None]
            return None, "équipe non reconnue : " + ", ".join(miss)
        p = lg["pairs"].get(f"{h}|{a}")
        if not p or "btts_yes" not in p:
            return None, "historique insuffisant pour le modèle de buts"
        return enriched_labels(p), None


def load_book_safely(now: datetime) -> GoalsBook | None:
    try:
        return GoalsBook.load(now)
    except Exception:  # noqa: BLE001 - the enriched model is optional: never lose a pass for it
        return None


def run_refresh(now: datetime, api_key: str | None, max_requests: int = MAX_REQUESTS_PER_RUN,
                download: bool = True) -> dict:
    api = ApiFootballCache(cache_dir(), api_key, max_requests) if download else None
    t0 = time.time()
    history, teams, report = refresh_data(now, api)
    report["load_seconds"] = round(time.time() - t0, 1)
    t1 = time.time()
    book = build_book(history, teams, now)
    report["fit_seconds"] = round(time.time() - t1, 1)
    path = save_book(book)
    report.update(path=str(path), day=book["day"], n_train=book["n_train"],
                  pairs={c: len(lg["pairs"]) for c, lg in book["leagues"].items()},
                  unknown_teams={c: lg["unknown_teams"] for c, lg in book["leagues"].items() if lg["unknown_teams"]},
                  last_match={c: max(m.kickoff for m in ms).isoformat() for c, ms in history.items() if ms})
    return report
