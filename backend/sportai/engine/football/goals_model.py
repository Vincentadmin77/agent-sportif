"""Enriched goals model: BTTS and over 1.5 / 2.5 / 3.5 from Dixon-Coles + team form.

Backtested in scripts/backtest_goals.py (report: docs/11-backtest-buts.md).
Out of sample, only over 1.5 and over 3.5 passed the validation rule, and only
by a small margin (see VALIDATED_MARKETS); BTTS and over 2.5 did not.

Pieces:
  * build_features: pre-match features of every match from strictly earlier
    kickoffs (current-season home/away goals with shrinkage, rolling xG / shots
    on target / total shots, shot regularity, league goal rate, team BTTS and
    over-2.5 rates);
  * walk_forward_dc: weekly Dixon-Coles refit giving lambda, mu, rho and the
    plain DC market probabilities;
  * a per-market L2 logistic regression stacking the DC probability with those
    features (GoalsModel.fit / GoalsModel.predict).

Beating a base rate is not beating bookmakers: a probability from this model
still has to pass the usual value / divergence-from-market checks.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
from scipy.optimize import minimize

from sportai.engine.football import dixon_coles as dc
from sportai.engine.football.markets import MarketKey, outcome

MODEL_NAME = "goals-logit"
MODEL_VERSION = "1.0.0"
MARKETS = {"btts_yes": None, "over_1.5": 1.5, "over_2.5": 2.5, "over_3.5": 3.5}
# Markets that passed the out-of-sample rule (docs/11-backtest-buts.md), and the
# L2 penalty chosen for each market on the validation season.
VALIDATED_MARKETS = ("over_1.5", "over_3.5")
L2_BY_MARKET = {"btts_yes": 0.3, "over_1.5": 0.03, "over_2.5": 0.03, "over_3.5": 0.03}


@dataclass
class Match:
    fid: int
    comp: str
    season: int
    kickoff: datetime
    home: str
    away: str
    hg: int
    ag: int
    xg_h: float | None = None
    xg_a: float | None = None
    sot_h: float | None = None
    sot_a: float | None = None
    sh_h: float | None = None
    sh_a: float | None = None

    @property
    def day(self) -> date:
        return self.kickoff.date()



def shrink(total: float, n: int, prior: float, k: float) -> float:
    return (total + k * prior) / (n + k)


@dataclass
class TeamState:
    season: int = -1
    home_gf: float = 0.0
    home_ga: float = 0.0
    home_n: int = 0
    away_gf: float = 0.0
    away_ga: float = 0.0
    away_n: int = 0
    # rolling, any venue: (xg_for, xg_against, sot_for, sot_against, sh_for, sh_against, btts, over25)
    recent: deque = field(default_factory=lambda: deque(maxlen=19))
    shots_home: deque = field(default_factory=lambda: deque(maxlen=10))  # (for, against) at home
    shots_away: deque = field(default_factory=lambda: deque(maxlen=10))


SHOT_FEATURES = ("sh_h_for", "sh_a_against", "sh_a_for", "sh_h_against", "shcv_h", "shcv_a",
                 "shot_att_h", "shot_att_a")


def _rolling(vals: list[float | None], n: int, prior: float, k: float) -> float:
    v = [x for x in vals[-n:] if x is not None]
    return shrink(sum(v), len(v), prior, k)


def build_features(matches: list[Match]) -> dict[int, dict]:
    """Pre-match features for every match, from strictly earlier kickoffs only.

    Matches are processed by kickoff time: all matches sharing a kickoff get
    their features before any of them updates the state.
    """
    teams: dict[str, TeamState] = defaultdict(TeamState)
    feats: dict[int, dict] = {}
    by_ko: dict[datetime, list[Match]] = defaultdict(list)
    for m in matches:
        by_ko[m.kickoff].append(m)

    # running league aggregates per season
    season_tot: dict[int, list[float]] = defaultdict(list)
    prior = {"hg": 1.5, "ag": 1.2, "xg": 1.35, "sot": 4.3, "sh": 12.5, "btts": 0.5, "o25": 0.5, "shsd": 4.0}
    window: deque = deque(maxlen=760)  # ~2 seasons of league history for priors

    def league_priors() -> dict:
        if len(window) < 50:
            return prior
        w = list(window)
        hg = np.mean([m.hg for m in w]); ag = np.mean([m.ag for m in w])
        xg = [x for m in w for x in (m.xg_h, m.xg_a) if x is not None]
        sot = [x for m in w for x in (m.sot_h, m.sot_a) if x is not None]
        sh = [x for m in w for x in (m.sh_h, m.sh_a) if x is not None]
        return {"hg": hg, "ag": ag,
                "xg": float(np.mean(xg)) if len(xg) > 50 else (hg + ag) / 2,
                "sot": float(np.mean(sot)) if len(sot) > 50 else prior["sot"],
                "sh": float(np.mean(sh)) if len(sh) > 50 else prior["sh"],
                "btts": float(np.mean([m.hg > 0 and m.ag > 0 for m in w])),
                "o25": float(np.mean([m.hg + m.ag > 2.5 for m in w])), "shsd": prior["shsd"]}

    cached_priors = None
    since_priors = 0
    for ko in sorted(by_ko):
        group = by_ko[ko]
        if cached_priors is None or since_priors >= 20:
            cached_priors, since_priors = league_priors(), 0
        P = cached_priors
        for m in group:
            h, a = teams[m.home], teams[m.away]
            for t in (h, a):
                if t.season != m.season:
                    t.season = m.season
                    t.home_gf = t.home_ga = t.away_gf = t.away_ga = 0.0
                    t.home_n = t.away_n = 0
            cur = season_tot[m.season]
            prev = season_tot.get(m.season - 1) or []
            prev_rate = float(np.mean(prev)) if prev else P["hg"] + P["ag"]
            f = {
                "season": m.season,
                "h_home_gf": shrink(h.home_gf, h.home_n, P["hg"], 4),
                "h_home_ga": shrink(h.home_ga, h.home_n, P["ag"], 4),
                "a_away_gf": shrink(a.away_gf, a.away_n, P["ag"], 4),
                "a_away_ga": shrink(a.away_ga, a.away_n, P["hg"], 4),
                "league_rate": shrink(sum(cur), len(cur), prev_rate, 60),
                "n_h": h.home_n + h.away_n, "n_a": a.home_n + a.away_n,
            }
            for side, t in (("h", h), ("a", a)):
                r = list(t.recent)
                # xG when present, otherwise shots-on-target proxy (goals per SoT ~ league ratio)
                ratio = P["xg"] / max(P["sot"], 1e-6)
                xgf = [x[0] if x[0] is not None else (x[2] * ratio if x[2] is not None else None) for x in r]
                xga = [x[1] if x[1] is not None else (x[3] * ratio if x[3] is not None else None) for x in r]
                f[f"xgf_{side}"] = _rolling(xgf, 8, P["xg"], 3)
                f[f"xga_{side}"] = _rolling(xga, 8, P["xg"], 3)
                f[f"sotf_{side}"] = _rolling([x[2] for x in r], 8, P["sot"], 3)
                f[f"sota_{side}"] = _rolling([x[3] for x in r], 8, P["sot"], 3)
                f[f"btts_{side}"] = _rolling([x[6] for x in r], 19, P["btts"], 6)
                f[f"o25_{side}"] = _rolling([x[7] for x in r], 19, P["o25"], 6)
            # total shots, split by venue: home team at home, away team away
            hs, as_ = list(h.shots_home), list(a.shots_away)
            f["sh_h_for"] = _rolling([x[0] for x in hs], 10, P["sh"], 3)
            f["sh_h_against"] = _rolling([x[1] for x in hs], 10, P["sh"], 3)
            f["sh_a_for"] = _rolling([x[0] for x in as_], 10, P["sh"], 3)
            f["sh_a_against"] = _rolling([x[1] for x in as_], 10, P["sh"], 3)
            # shot regularity: coefficient of variation of total shots (for and against), all venues
            for side, t in (("h", h), ("a", a)):
                r = list(t.recent)[-10:]
                sf = [x[4] for x in r if x[4] is not None]
                sa = [x[5] for x in r if x[5] is not None]
                cvf = (np.std(sf, ddof=1) / max(np.mean(sf), 1)) if len(sf) >= 4 else 0.33
                cva = (np.std(sa, ddof=1) / max(np.mean(sa), 1)) if len(sa) >= 4 else 0.33
                f[f"shcv_{side}"] = 0.5 * (cvf + cva)
            # interaction: expected shot volume of the match x its irregularity
            f["shot_att_h"] = (f["sh_h_for"] + f["sh_a_against"]) / 2 * f["shcv_h"]
            f["shot_att_a"] = (f["sh_a_for"] + f["sh_h_against"]) / 2 * f["shcv_a"]
            feats[m.fid] = f
        for m in group:
            h, a = teams[m.home], teams[m.away]
            h.home_gf += m.hg; h.home_ga += m.ag; h.home_n += 1
            a.away_gf += m.ag; a.away_ga += m.hg; a.away_n += 1
            btts, o25 = float(m.hg > 0 and m.ag > 0), float(m.hg + m.ag > 2.5)
            h.recent.append((m.xg_h, m.xg_a, m.sot_h, m.sot_a, m.sh_h, m.sh_a, btts, o25))
            a.recent.append((m.xg_a, m.xg_h, m.sot_a, m.sot_h, m.sh_a, m.sh_h, btts, o25))
            if m.sh_h is not None and m.sh_a is not None:
                h.shots_home.append((m.sh_h, m.sh_a))
                a.shots_away.append((m.sh_a, m.sh_h))
            season_tot[m.season].append(m.hg + m.ag)
            window.append(m)
            since_priors += 1
    return feats


# --------------------------------------------------------------------- walk-forward DC


def hit(m: Match, market: str) -> float:
    if market == "btts_yes":
        return float(m.hg > 0 and m.ag > 0)
    return float(m.hg + m.ag > MARKETS[market])


def market_probs(sm: np.ndarray) -> dict[str, float]:
    out = {"btts_yes": outcome(sm, MarketKey("btts", "yes")).win}
    for k, line in MARKETS.items():
        if line is not None:
            out[k] = outcome(sm, MarketKey("totals", "over", line)).win
    return out


def walk_forward_dc(matches: list[Match], start_season: int) -> dict[int, dict]:
    """Weekly Dixon-Coles refit on the past 3 years + base rates, for every match of
    start_season and later (matches sorted by kickoff). Promoted teams with fewer
    than 6 known matches are skipped, as in production."""
    first = next(m.day for m in matches if m.season >= start_season)
    day = first - timedelta(days=first.weekday())  # Monday
    last = matches[-1].day
    res: dict[int, dict] = {}
    cfg = dc.FitConfig()
    dcm = [dc.MatchResult(m.day, m.home, m.away, m.hg, m.ag) for m in matches]
    days = np.array([m.day.toordinal() for m in matches])
    while day <= last:
        lo, hi = np.searchsorted(days, day.toordinal()), np.searchsorted(days, (day + timedelta(days=7)).toordinal())
        week = [m for m in matches[lo:hi] if m.season >= start_season]
        tlo = np.searchsorted(days, (day - timedelta(days=3 * 365)).toordinal())
        train = dcm[tlo:lo]
        if week and len(train) > 200:
            model = dc.fit(train, reference_date=day - timedelta(days=1), config=cfg, with_uncertainty=False)
            past = matches[tlo:lo]
            base = {k: float(np.mean([hit(p, k) for p in past])) for k in MARKETS}
            past1 = [p for p in past if (day - p.day).days <= 365]
            base1y = {k: float(np.mean([hit(p, k) for p in past1])) for k in MARKETS}
            for m in week:
                if not (model.knows(m.home) and model.knows(m.away)):
                    continue
                lam, mu = model.expected_goals(m.home, m.away)
                sm = dc.score_matrix(lam, mu, model.rho)
                res[m.fid] = {"lam": lam, "mu": mu, "rho": model.rho, "dc": market_probs(sm), "base": base,
                              "base1y": base1y}
        day += timedelta(days=7)
    return res


# ------------------------------------------------------------------- enriched models


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def logit_design(rows: list[dict], market: str, featset: str = "full") -> np.ndarray:
    """Features for the per-market logistic regression (pairs summed to limit collinearity).

    featset: "full", "noshots" (no total-shot / shot-regularity features),
    "noregularity" (total-shot volumes kept, shot regularity removed) or
    "dc_only" (logit of the DC probability only: a pure recalibration of DC).
    """
    if featset == "dc_only":
        return np.asarray([[_logit(r["dc"][market])] for r in rows], dtype=float)
    shots = featset in ("full", "noregularity")
    regularity = featset == "full"
    cols = []
    for r in rows:
        f = r["f"]
        x = [
            _logit(r["dc"][market]),
            math.log(r["lam"] + r["mu"]),
            math.log(f["h_home_gf"] + f["a_away_ga"]),
            math.log(f["a_away_gf"] + f["h_home_ga"]),
            math.log(f["xgf_h"] + f["xga_a"]),
            math.log(f["xgf_a"] + f["xga_h"]),
            math.log(f["sotf_h"] + f["sota_a"] + 0.1),
            math.log(f["sotf_a"] + f["sota_h"] + 0.1),
            math.log(f["league_rate"]),
            f["btts_h"] + f["btts_a"],
            f["o25_h"] + f["o25_a"],
            min(f["n_h"], f["n_a"]) < 8,
        ]
        if shots:
            x += [math.log(f["sh_h_for"] + f["sh_a_against"]), math.log(f["sh_a_for"] + f["sh_h_against"])]
        if regularity:
            x += [
                f["shcv_h"] + f["shcv_a"],
                math.log(f["sh_h_for"] + f["sh_a_against"]) * f["shcv_h"]
                + math.log(f["sh_a_for"] + f["sh_h_against"]) * f["shcv_a"],
            ]
        cols.append(x)
    return np.asarray(cols, dtype=float)



class Standardiser:
    def __init__(self, X: np.ndarray):
        self.m = X.mean(axis=0)
        self.s = X.std(axis=0)
        self.s[self.s < 1e-9] = 1.0

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return (X - self.m) / self.s


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float, offset: np.ndarray | None = None) -> np.ndarray:
    """L2-penalised logistic regression (intercept not penalised). Returns [b0, b...]."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    off = np.zeros(n) if offset is None else offset

    def f(b):
        z = Xb @ b + off
        p = 1 / (1 + np.exp(-z))
        ll = np.sum(np.logaddexp(0, z) - y * z) / n + l2 * (b[1:] @ b[1:]) / 2
        g = Xb.T @ (p - y) / n
        g[1:] += l2 * b[1:]
        return ll, g

    return minimize(f, np.zeros(k + 1), jac=True, method="L-BFGS-B").x


def predict_logistic(b: np.ndarray, X: np.ndarray, offset: np.ndarray | None = None) -> np.ndarray:
    z = b[0] + X @ b[1:] + (0 if offset is None else offset)
    return 1 / (1 + np.exp(-z))




# ------------------------------------------------------------------ production API


@dataclass
class GoalsModel:
    """Per-market logistic regressions on standardised features (fitted on the past only)."""

    coefs: dict[str, list[float]]
    means: dict[str, list[float]]
    scales: dict[str, list[float]]
    fitted_until: str
    n_train: int
    featset: str = "full"
    version: str = MODEL_VERSION

    @classmethod
    def fit(cls, history: dict[str, list[Match]], as_of: datetime, start_season: dict[str, int] | None = None,
            markets: tuple[str, ...] = tuple(MARKETS), featset: str = "full") -> "GoalsModel":
        """Fit on every match of `history` (by league, any order) that kicked off before `as_of`.

        Training rows start at the 2nd season present for each league (the 1st one
        only warms up Dixon-Coles and the form features), unless start_season says otherwise.
        """
        rows = training_rows(history, as_of, start_season)
        if len(rows) < 300:
            raise ValueError(f"need at least 300 training matches, got {len(rows)}")
        coefs, means, scales = {}, {}, {}
        for mk in markets:
            X = logit_design(rows, mk, featset)
            y = np.array([hit(r["m"], mk) for r in rows])
            st = Standardiser(X)
            coefs[mk] = fit_logistic(st(X), y, L2_BY_MARKET[mk]).tolist()
            means[mk], scales[mk] = st.m.tolist(), st.s.tolist()
        return cls(coefs, means, scales, as_of.isoformat(), len(rows), featset)

    def predict(self, comp_matches: list[Match], home: str, away: str, kickoff: datetime, season: int,
                markets: tuple[str, ...] = VALIDATED_MARKETS, dc_model: dc.DixonColesModel | None = None
                ) -> dict[str, float] | None:
        """Enriched probabilities for one fixture, from the league's past matches only.

        comp_matches: finished matches of the fixture's league (any order; only
        those before `kickoff` are used). Returns None when Dixon-Coles does not
        know one of the teams (promoted team without enough history).
        """
        past = sorted((m for m in comp_matches if m.kickoff < kickoff), key=lambda m: (m.kickoff, m.fid))
        if dc_model is None:
            monday = kickoff.date() - timedelta(days=kickoff.weekday())
            train = [dc.MatchResult(m.day, m.home, m.away, m.hg, m.ag) for m in past
                     if m.day < monday and (monday - m.day).days <= 3 * 365]
            if len(train) <= 200:
                return None
            dc_model = dc.fit(train, reference_date=monday - timedelta(days=1), with_uncertainty=False)
        if not (dc_model.knows(home) and dc_model.knows(away)):
            return None
        target = Match(-1, past[0].comp if past else "", season, kickoff, home, away, 0, 0)
        feats = build_features(past + [target])[-1]  # the target's result is never read: features come first
        lam, mu = dc_model.expected_goals(home, away)
        row = {"m": target, "f": feats, "lam": lam, "mu": mu, "rho": dc_model.rho,
               "dc": market_probs(dc.score_matrix(lam, mu, dc_model.rho))}
        out = {}
        for mk in markets:
            X = logit_design([row], mk, self.featset)
            z = (X - np.array(self.means[mk])) / np.array(self.scales[mk])
            out[mk] = float(predict_logistic(np.array(self.coefs[mk]), z)[0])
        return out

    def predict_pairs(self, comp_matches: list[Match], pairs: list[tuple[str, str]], at: datetime, season: int,
                      dc_model: dc.DixonColesModel, markets: tuple[str, ...] = tuple(MARKETS)
                      ) -> dict[tuple[str, str], dict]:
        """Enriched (and plain DC) probabilities for many (home, away) pairs at once, as of `at`.

        Same features as predict(), computed in one pass: every pair is a pseudo-match
        kicking off at `at`, so all of them see exactly the matches before `at` and none
        sees another. Pairs with a team unknown to dc_model are left out.
        """
        past = sorted((m for m in comp_matches if m.kickoff < at), key=lambda m: (m.kickoff, m.fid))
        comp = past[0].comp if past else ""
        known = [(h, a) for h, a in pairs if h != a and dc_model.knows(h) and dc_model.knows(a)]
        targets = [Match(-(k + 1), comp, season, at, h, a, 0, 0) for k, (h, a) in enumerate(known)]
        feats = build_features(past + targets)
        rows = []
        for t in targets:
            lam, mu = dc_model.expected_goals(t.home, t.away)
            rows.append({"m": t, "f": feats[t.fid], "lam": lam, "mu": mu, "rho": dc_model.rho,
                         "dc": market_probs(dc.score_matrix(lam, mu, dc_model.rho))})
        out: dict[tuple[str, str], dict] = {(r["m"].home, r["m"].away): {"lam": r["lam"], "mu": r["mu"], "dc": r["dc"]}
                                            for r in rows}
        if not rows:
            return out
        for mk in markets:
            X = logit_design(rows, mk, self.featset)
            z = (X - np.array(self.means[mk])) / np.array(self.scales[mk])
            p = predict_logistic(np.array(self.coefs[mk]), z)
            for r, pi in zip(rows, p):
                out[(r["m"].home, r["m"].away)][mk] = float(pi)
        return out

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "GoalsModel":
        return cls(**d)


def training_rows(history: dict[str, list[Match]], as_of: datetime,
                  start_season: dict[str, int] | None = None) -> list[dict]:
    rows = []
    for comp, ms in history.items():
        ms = sorted((m for m in ms if m.kickoff < as_of), key=lambda m: (m.kickoff, m.fid))
        if not ms:
            continue
        seasons = sorted({m.season for m in ms})
        s0 = (start_season or {}).get(comp, seasons[1] if len(seasons) > 1 else seasons[0])
        feats = build_features(ms)
        wf = walk_forward_dc(ms, s0)
        rows += [{"m": m, "f": feats[m.fid], **wf[m.fid]} for m in ms if m.fid in wf]
    return rows
