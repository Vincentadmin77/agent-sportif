"""Simulation provider: a fictional league for demos and end-to-end tests.

Everything it returns is tagged mode="simulation", uses fictional team names
prefixed "SIM", and can never be published (the publication service refuses
non-real data). It is deterministic for a given seed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from sportai.connectors.base import Fixture, HistoricalResult, LiveSnapshot, OddsQuote
from sportai.engine.football.dixon_coles import score_matrix
from sportai.engine.football.markets import MarketKey, outcome

TEAMS = [f"SIM {n}" for n in (
    "Aurore", "Basalte", "Cobalt", "Dauphins", "Étoile", "Faucons", "Granit", "Horizon",
    "Iris", "Jaguars", "Krypton", "Lynx", "Mistral", "Nébuleuse", "Orage", "Phénix",
)]
COMPETITION = "SIM"
BOOKMAKERS = ("sim_book_a", "sim_book_b")


class SimulationProvider:
    name = "simulation"

    def __init__(self, seed: int = 7, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc).replace(microsecond=0)
        rng = np.random.default_rng(seed)
        self._rng = rng
        self.att = dict(zip(TEAMS, rng.normal(0, 0.3, len(TEAMS))))
        self.dfc = dict(zip(TEAMS, rng.normal(0, 0.25, len(TEAMS))))
        self.home_adv = 0.25

    def rates(self, home: str, away: str) -> tuple[float, float]:
        return (float(np.exp(self.home_adv + self.att[home] + self.dfc[away])),
                float(np.exp(self.att[away] + self.dfc[home])))

    def results(self, competition: str = COMPETITION, season: str = "sim") -> list[HistoricalResult]:
        rng = np.random.default_rng(1)
        out, day = [], self.now.date() - timedelta(days=600)
        for _ in range(2):
            for h in TEAMS:
                for a in TEAMS:
                    if h == a:
                        continue
                    lam, mu = self.rates(h, a)
                    out.append(HistoricalResult(self.name, COMPETITION, day, h, a,
                                                int(rng.poisson(lam)), int(rng.poisson(mu)), "simulation"))
                    day += timedelta(days=1)
                    if day >= self.now.date():
                        return out
        return out

    def upcoming_fixtures(self, competition: str = COMPETITION, days_ahead: int = 3) -> list[Fixture]:
        order = list(TEAMS)
        out = []
        for k in range(0, len(order), 2):
            out.append(Fixture(self.name, f"sim-{k // 2}", COMPETITION, self.now + timedelta(hours=20 + k),
                               order[k], order[k + 1], "simulation"))
        return out

    def odds(self, competition: str = COMPETITION, noise: float = 0.06) -> list[OddsQuote]:
        """Prices from the true model with bookmaker noise and a 5% margin."""
        rng = np.random.default_rng(11)
        quotes = []
        for fx in self.upcoming_fixtures():
            lam, mu = self.rates(fx.home, fx.away)
            m = score_matrix(lam, mu, -0.05)
            groups = [[MarketKey("1x2", s) for s in ("home", "draw", "away")],
                      [MarketKey("totals", s, 2.5) for s in ("over", "under")],
                      [MarketKey("btts", s) for s in ("yes", "no")]]
            for bk in BOOKMAKERS:
                for g in groups:
                    p = np.array([outcome(m, k).win for k in g])
                    noisy = np.clip(p * np.exp(rng.normal(0, noise, len(p))), 0.02, 0.98)
                    noisy = noisy / noisy.sum() * 1.05
                    for k, q in zip(g, noisy):
                        quotes.append(OddsQuote(self.name, bk, fx.provider_event_id, fx.home, fx.away, fx.kickoff,
                                                k.market, k.selection, k.line, round(float(1 / q), 2),
                                                self.now - timedelta(seconds=30), "simulation", "valid",
                                                self.now + timedelta(minutes=15)))
        return quotes

    def live_snapshot(self, provider_event_id: str, minute: float = 25, score=(0, 1),
                      xg=(0.9, 0.3)) -> LiveSnapshot:
        return LiveSnapshot(self.name, provider_event_id, minute, score[0], score[1],
                            self.now - timedelta(seconds=5), "simulation", home_xg=xg[0], away_xg=xg[1])

