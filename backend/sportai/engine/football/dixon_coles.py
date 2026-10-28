"""Dixon-Coles model for football scores.

Each team has an attack and a defence strength (both sum to zero across the
league), plus a league-wide scoring intercept and a home advantage. Goals are
Poisson with
    lambda_home = exp(intercept + home_adv + attack[home] + defence[away])
    mu_away     = exp(intercept + attack[away] + defence[home])
and the joint probability of low scores (0-0, 1-0, 0-1, 1-1) is corrected by
the Dixon-Coles tau factor with parameter rho.

Matches are weighted by exp(-xi * age_in_days) so recent form counts more,
and a small ridge penalty keeps strengths of teams with few matches near zero.

Parameter uncertainty is estimated with a Laplace approximation: the inverse
Hessian of the penalised negative log-likelihood is used as the covariance of
the parameters, and samples from it give probability intervals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

MODEL_NAME = "dixon-coles"
MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class MatchResult:
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int


@dataclass
class FitConfig:
    xi: float = 0.0019  # time decay per day (half-life ~ 365 days)
    ridge: float = 0.05  # L2 penalty on attack/defence strengths
    max_goals: int = 10  # score matrix truncation
    min_matches_per_team: int = 6


@dataclass(frozen=True)
class RateCalibration:
    """Post-fit correction of the expected goals of a match, for a competition the model is biased on.

    With d = log(lambda / mu) the strength gap of the match (home advantage included) and
    s = log(lambda * mu) / 2 its goal level, the corrected gap is stretch * d + home_shift, the level is
    kept. stretch > 1 widens the gap between unequal teams (favourites more likely, fewer draws),
    home_shift > 0 adds home advantage. RateCalibration() changes nothing.
    """

    stretch: float = 1.0
    home_shift: float = 0.0

    def apply(self, lam: float, mu: float) -> tuple[float, float]:
        level, gap = (np.log(lam) + np.log(mu)) / 2, np.log(lam) - np.log(mu)
        gap = self.stretch * gap + self.home_shift
        return float(np.exp(level + gap / 2)), float(np.exp(level - gap / 2))


@dataclass
class DixonColesModel:
    teams: list[str]
    attack: np.ndarray
    defence: np.ndarray
    intercept: float
    home_adv: float
    rho: float
    covariance: np.ndarray | None
    n_matches: int
    matches_per_team: dict[str, int]
    reference_date: date
    config: FitConfig = field(default_factory=FitConfig)
    version: str = MODEL_VERSION
    calibration: RateCalibration | None = None  # applied to every expected-goals pair, central and sampled

    @property
    def index(self) -> dict[str, int]:
        return {t: i for i, t in enumerate(self.teams)}

    def knows(self, team: str) -> bool:
        return self.matches_per_team.get(team, 0) >= self.config.min_matches_per_team

    def expected_goals(self, home: str, away: str, factors: tuple[float, float] | None = None) -> tuple[float, float]:
        """factors: optional (f_home, f_away) multiplying each side's rate after the calibration (missing key
        players, services/lineups.absence_factor); rho is unchanged. None changes nothing."""
        lam_mu = self._calibrated(*_rates(self._params(), self.index[home], self.index[away], len(self.teams)))
        return _with_factors(lam_mu, factors)

    def _calibrated(self, lam: float, mu: float) -> tuple[float, float]:
        return self.calibration.apply(lam, mu) if self.calibration is not None else (lam, mu)

    def score_matrix(self, home: str, away: str, factors: tuple[float, float] | None = None) -> np.ndarray:
        lam, mu = self.expected_goals(home, away, factors)
        return score_matrix(lam, mu, self.rho, self.config.max_goals)

    def sample_score_matrices(
        self, home: str, away: str, n: int = 400, seed: int = 0, factors: tuple[float, float] | None = None
    ) -> list[np.ndarray]:
        """Score matrices under parameter uncertainty (Laplace approximation); factors as in expected_goals."""
        if self.covariance is None:
            return [self.score_matrix(home, away, factors)]
        rng = np.random.default_rng(seed)
        theta = self._params()
        draws = rng.multivariate_normal(theta, self.covariance, size=n, method="eigh")
        i, j, k = self.index[home], self.index[away], len(self.teams)
        out = []
        for d in draws:
            lam, mu = _with_factors(self._calibrated(*_rates(d, i, j, k)), factors)
            rho = float(np.clip(d[-1], -0.3, 0.3))
            out.append(score_matrix(lam, mu, rho, self.config.max_goals))
        return out

    def _params(self) -> np.ndarray:
        return np.concatenate([self.attack, self.defence, [self.intercept, self.home_adv, self.rho]])


def _with_factors(lam_mu: tuple[float, float], factors: tuple[float, float] | None) -> tuple[float, float]:
    if factors is None:
        return lam_mu
    return lam_mu[0] * float(factors[0]), lam_mu[1] * float(factors[1])


def score_matrix(lam: float, mu: float, rho: float, max_goals: int = 10) -> np.ndarray:
    """P(home = i, away = j) for i, j in 0..max_goals, renormalised."""
    g = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(g, lam), poisson.pmf(g, mu))
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    m = np.clip(m, 0.0, None)
    return m / m.sum()


def fit(
    matches: list[MatchResult],
    reference_date: date | None = None,
    config: FitConfig | None = None,
    with_uncertainty: bool = True,
) -> DixonColesModel:
    """Fit by penalised weighted maximum likelihood on matches before reference_date."""
    config = config or FitConfig()
    if reference_date is None:
        reference_date = max(m.played_on for m in matches)
    data = [m for m in matches if m.played_on <= reference_date]
    if len(data) < 20:
        raise ValueError(f"need at least 20 matches to fit, got {len(data)}")

    teams = sorted({m.home for m in data} | {m.away for m in data})
    idx = {t: i for i, t in enumerate(teams)}
    k = len(teams)
    hi = np.array([idx[m.home] for m in data])
    ai = np.array([idx[m.away] for m in data])
    hg = np.array([m.home_goals for m in data], dtype=float)
    ag = np.array([m.away_goals for m in data], dtype=float)
    age = np.array([(reference_date - m.played_on).days for m in data], dtype=float)
    w = np.exp(-config.xi * age)

    def nll(theta: np.ndarray) -> tuple[float, np.ndarray]:
        return _penalised_nll(theta, hi, ai, hg, ag, w, k, config.ridge)

    theta0 = np.zeros(2 * k + 3)
    theta0[2 * k + 1] = 0.25
    bounds = [(-3, 3)] * (2 * k) + [(-2, 2), (-1, 1), (-0.3, 0.3)]
    res = minimize(nll, theta0, method="L-BFGS-B", jac=True, bounds=bounds, options={"maxiter": 2000})
    if not res.success:
        raise RuntimeError(f"Dixon-Coles fit did not converge: {res.message}")
    theta = _centre(res.x, k)

    cov = None
    if with_uncertainty:
        cov = _laplace_covariance(nll, theta)

    counts: dict[str, int] = {}
    for m in data:
        counts[m.home] = counts.get(m.home, 0) + 1
        counts[m.away] = counts.get(m.away, 0) + 1

    return DixonColesModel(
        teams=teams,
        attack=theta[:k],
        defence=theta[k : 2 * k],
        intercept=float(theta[2 * k]),
        home_adv=float(theta[2 * k + 1]),
        rho=float(theta[2 * k + 2]),
        covariance=cov,
        n_matches=len(data),
        matches_per_team=counts,
        reference_date=reference_date,
        config=config,
    )


def _rates(theta: np.ndarray, i: int, j: int, k: int) -> tuple[float, float]:
    att, dfc, c, home = theta[:k], theta[k : 2 * k], theta[2 * k], theta[2 * k + 1]
    return float(np.exp(c + home + att[i] + dfc[j])), float(np.exp(c + att[j] + dfc[i]))


def _penalised_nll(theta, hi, ai, hg, ag, w, k, ridge) -> tuple[float, np.ndarray]:
    """Penalised weighted negative log-likelihood and its analytic gradient."""
    att, dfc = theta[:k], theta[k : 2 * k]
    c, home, rho = theta[2 * k], theta[2 * k + 1], theta[2 * k + 2]
    lam = np.exp(c + home + att[hi] + dfc[ai])
    mu = np.exp(c + att[ai] + dfc[hi])

    # Dixon-Coles correction and its derivatives w.r.t. log(lam), log(mu), rho.
    tau = np.ones_like(lam)
    dt_dlam = np.zeros_like(lam)
    dt_dmu = np.zeros_like(lam)
    dt_drho = np.zeros_like(lam)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    lm = lam * mu
    tau[m00] = 1 - lm[m00] * rho
    dt_dlam[m00] = dt_dmu[m00] = -lm[m00] * rho
    dt_drho[m00] = -lm[m00]
    tau[m01] = 1 + lam[m01] * rho
    dt_dlam[m01] = lam[m01] * rho
    dt_drho[m01] = lam[m01]
    tau[m10] = 1 + mu[m10] * rho
    dt_dmu[m10] = mu[m10] * rho
    dt_drho[m10] = mu[m10]
    tau[m11] = 1 - rho
    dt_drho[m11] = -1.0
    tau = np.clip(tau, 1e-10, None)

    ll = np.log(tau) + hg * np.log(lam) - lam + ag * np.log(mu) - mu
    g_lam = w * (hg - lam + dt_dlam / tau)
    g_mu = w * (ag - mu + dt_dmu / tau)

    grad = np.zeros_like(theta)
    grad[:k] = -(np.bincount(hi, g_lam, k) + np.bincount(ai, g_mu, k))
    grad[k : 2 * k] = -(np.bincount(ai, g_lam, k) + np.bincount(hi, g_mu, k))
    grad[2 * k] = -(g_lam.sum() + g_mu.sum())
    grad[2 * k + 1] = -g_lam.sum()
    grad[2 * k + 2] = -(w * dt_drho / tau).sum()

    # Strengths are only identified up to constants absorbed by the intercept;
    # sum-to-zero penalties pin them down and the ridge shrinks small-sample teams.
    sa, sd = att.sum(), dfc.sum()
    value = -(w * ll).sum() + ridge * (att @ att + dfc @ dfc) + 10.0 * (sa**2 + sd**2)
    grad[:k] += 2 * ridge * att + 20.0 * sa
    grad[k : 2 * k] += 2 * ridge * dfc + 20.0 * sd
    return float(value), grad


def _centre(theta: np.ndarray, k: int) -> np.ndarray:
    theta = theta.copy()
    for block in (slice(0, k), slice(k, 2 * k)):
        shift = theta[block].mean()
        theta[block] -= shift
        theta[2 * k] += shift
    return theta


def _laplace_covariance(f, theta: np.ndarray, eps: float = 1e-5) -> np.ndarray | None:
    """Inverse Hessian of f at theta, the Hessian built from gradient differences."""
    n = len(theta)
    h = np.zeros((n, n))
    for a in range(n):
        e = np.zeros(n)
        e[a] = eps
        h[a] = (f(theta + e)[1] - f(theta - e)[1]) / (2 * eps)
    h = (h + h.T) / 2
    try:
        cov = np.linalg.inv(h)
    except np.linalg.LinAlgError:
        return None
    # Numerical noise can leave tiny negative eigenvalues; clip them.
    vals, vecs = np.linalg.eigh(cov)
    if vals.max() <= 0:
        return None
    return (vecs * np.clip(vals, 0, None)) @ vecs.T
