"""openfootball/football.json — public-domain (CC0) historical results.

Used for model fitting and backtests. Results only: no odds, no xG.
"""

from __future__ import annotations

from datetime import date

import httpx

from sportai.connectors.base import HistoricalResult
from sportai.connectors.http import CachedClient

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
FILES = {"FR1": "fr.1", "EN1": "en.1", "ES1": "es.1", "DE1": "de.1", "IT1": "it.1"}


class OpenFootballProvider:
    name = "openfootball"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._http = CachedClient(BASE_URL, transport=transport, timeout=30)

    def results(self, competition: str, season: str) -> list[HistoricalResult]:
        data = self._http.get_json(f"/{season}/{FILES[competition]}.json", ttl=24 * 3600)
        return parse(data, competition)


def parse(data: dict, competition: str) -> list[HistoricalResult]:
    out = []
    for m in data.get("matches", []):
        score = m.get("score")
        ft = score.get("ft") if isinstance(score, dict) else None
        if not ft or len(ft) != 2 or ft[0] is None:
            continue  # not played yet
        out.append(HistoricalResult(OpenFootballProvider.name, competition, date.fromisoformat(m["date"]),
                                    m["team1"], m["team2"], int(ft[0]), int(ft[1]), "real"))
    return out
