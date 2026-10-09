"""Forecast and betting-record evaluation.

Scores probabilities (Brier, log-loss, calibration by bucket) and reports
betting results with sample size and a bootstrap confidence interval, so a
short winning run is never presented as proof of an edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def brier_multiclass(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean multi-class Brier score. probs: (n, k); outcomes: (n,) class indices."""
    onehot = np.eye(probs.shape[1])[outcomes]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def log_loss(probs: np.ndarray, outcomes: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(probs[np.arange(len(outcomes)), outcomes], eps, 1.0)
    return float(-np.mean(np.log(p)))


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


def calibration(p: np.ndarray, hit: np.ndarray, n_buckets: int = 10) -> list[CalibrationBucket]:
    """Binary calibration table: predicted probability vs observed frequency."""
    edges = np.linspace(0, 1, n_buckets + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & ((p < hi) | (hi == 1.0))
        if mask.sum() == 0:
            continue
        out.append(
            CalibrationBucket(float(lo), float(hi), int(mask.sum()), float(p[mask].mean()), float(hit[mask].mean()))
        )
    return out


@dataclass(frozen=True)
class BettingRecord:
    n_bets: int
    hit_rate: float
    roi: float
    roi_ci_low: float
    roi_ci_high: float
    note: str


def betting_record(profits: np.ndarray, won: np.ndarray, seed: int = 0, n_boot: int = 5000) -> BettingRecord:
    """ROI per unit staked with a 95% bootstrap interval."""
    n = len(profits)
    if n == 0:
        return BettingRecord(0, float("nan"), float("nan"), float("nan"), float("nan"), "no bets")
    rng = np.random.default_rng(seed)
    boots = rng.choice(profits, size=(n_boot, n), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    note = "sample too small to conclude" if n < 300 or lo <= 0 <= hi else "interval excludes zero"
    return BettingRecord(n, float(won.mean()), float(profits.mean()), float(lo), float(hi), note)
