"""Modèle de buts NHL (Poisson par équipe, attaque/défense, avantage de la glace).

Ajusté sur le temps réglementaire (60 min) : un match fini en prolongation ou aux tirs au but
était à égalité au bout de 60 min (score du perdant pour les deux). Les matchs anciens pèsent
moins (demi-vie en jours). Donne les chances du 1N2 temps réglementaire (marché Winamax
« vainqueur »), du vainqueur prolongation comprise, des totaux (score final, tirs au but = 1 but)
et du handicap ±1,5 (prolongation comprise).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

MAXG = 12


def regulation_score(g: dict) -> tuple[int, int, str]:
    """(buts dom, buts ext en 60 min, 'REG'|'OT'|'SO')."""
    fin = (g.get("fin") or "").upper()
    bd, be = int(g["bd"]), int(g["be"])
    kind = "SO" if "SO" in fin else ("OT" if "OT" in fin else "REG")
    if kind == "REG":
        return bd, be, kind
    m = min(bd, be)
    return m, m, kind


def _t(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


class HockeyModel:
    def __init__(self, halflife_days: float = 120.0, prior_games: float = 20.0, iters: int = 30):
        self.h, self.prior, self.iters = halflife_days, prior_games, iters
        self.att, self.dfn, self.home, self.mu = {}, {}, 1.0, 1.5
        self.ot_home = 0.5
        self.kdraw = 1.0

    def fit(self, games: list[dict], asof: datetime):
        rows = []
        for g in games:
            t = _t(g["date"])
            if t >= asof:
                continue
            d, e, kind = regulation_score(g)
            w = 0.5 ** ((asof - t).total_seconds() / 86400 / self.h)
            rows.append((g["dom"], g["ext"], d, e, w, kind, int(g["bd"]) > int(g["be"])))
        if not rows:
            return self
        teams = {r[0] for r in rows} | {r[1] for r in rows}
        W = sum(r[4] for r in rows)
        self.mu = sum((r[2] + r[3]) * r[4] for r in rows) / (2 * W)
        att = {t: 1.0 for t in teams}
        dfn = {t: 1.0 for t in teams}
        home = 1.05
        for _ in range(self.iters):
            num = {t: self.prior * self.mu for t in teams}
            den = {t: self.prior * self.mu for t in teams}
            for h, a, d, e, w, *_ in rows:
                num[h] += w * d; den[h] += w * self.mu * home * dfn[a]
                num[a] += w * e; den[a] += w * self.mu * dfn[h]
            att = {t: num[t] / den[t] for t in teams}
            num = {t: self.prior * self.mu for t in teams}
            den = {t: self.prior * self.mu for t in teams}
            for h, a, d, e, w, *_ in rows:
                num[a] += w * d; den[a] += w * self.mu * home * att[h]
                num[h] += w * e; den[h] += w * self.mu * att[a]
            dfn = {t: num[t] / den[t] for t in teams}
            hn = sum(w * d for h, a, d, e, w, *_ in rows)
            hd = sum(w * self.mu * att[h] * dfn[a] for h, a, d, e, w, *_ in rows)
            home = hn / hd
        self.att, self.dfn, self.home = att, dfn, home
        # nul en 60 min plus fréquent que ne le dit Poisson (fin de match fermée) : facteur mesuré
        self.kdraw = 1.0
        obs = sum(w for h, a, d, e, w, *_ in rows if d == e) / W
        pred = sum(w * self._reg(h, a)[1] for h, a, d, e, w, *_ in rows[-1500:]) / sum(r[4] for r in rows[-1500:])
        self.kdraw = obs / pred if pred else 1.0
        ot = [(r[6], r[4]) for r in rows if r[5] != "REG"]
        if ot:
            self.ot_home = (sum(w for hw, w in ot if hw) + 10) / (sum(w for _, w in ot) + 20)
        return self

    def known(self, team: str) -> bool:
        return team in self.att

    def lambdas(self, dom: str, ext: str) -> tuple[float, float]:
        a = self.mu * self.home * self.att.get(dom, 1) * self.dfn.get(ext, 1)
        b = self.mu * self.att.get(ext, 1) * self.dfn.get(dom, 1)
        return a, b

    def _grid(self, dom, ext):
        a, b = self.lambdas(dom, ext)
        pa = [math.exp(-a) * a ** k / math.factorial(k) for k in range(MAXG)]
        pb = [math.exp(-b) * b ** k / math.factorial(k) for k in range(MAXG)]
        reg = {(i, j): pa[i] * pb[j] * (self.kdraw if i == j else 1.0) for i in range(MAXG) for j in range(MAXG)}
        z = sum(reg.values())
        return a, b, pa, pb, {k: v / z for k, v in reg.items()}

    def _reg(self, dom, ext):
        *_, reg = self._grid(dom, ext)
        p1 = sum(v for (i, j), v in reg.items() if i > j)
        px = sum(v for (i, j), v in reg.items() if i == j)
        return p1, px, 1 - p1 - px

    def probs(self, dom: str, ext: str) -> dict:
        a, b, pa, pb, reg = self._grid(dom, ext)
        p1 = sum(v for (i, j), v in reg.items() if i > j)
        px = sum(v for (i, j), v in reg.items() if i == j)
        p2 = 1 - p1 - px
        # en cas d'égalité : vainqueur en prolongation / tirs au but (+1 but au score final)
        s = a / (a + b)
        ot_home = min(0.65, max(0.35, 0.5 + (s - 0.5) * 0.8 + (self.ot_home - 0.5)))
        final = {}
        for (i, j), v in reg.items():
            if i != j:
                final[(i, j)] = final.get((i, j), 0) + v
            else:
                final[(i + 1, j)] = final.get((i + 1, j), 0) + v * ot_home
                final[(i, j + 1)] = final.get((i, j + 1), 0) + v * (1 - ot_home)
        tot = {}
        diff = {}
        for (i, j), v in final.items():
            tot[i + j] = tot.get(i + j, 0) + v
            diff[i - j] = diff.get(i - j, 0) + v
        return {"lam": (a, b), "reg_1x2": (p1, px, p2), "vainqueur": (p1 + px * ot_home, p2 + px * (1 - ot_home)),
                "total": tot, "ecart": diff, "pa": pa, "pb": pb, "final": final}


def p_over(pr: dict, line: float) -> float:
    return sum(v for k, v in pr["total"].items() if k > line)


def p_team_over(pr: dict, side: int, line: float) -> float:
    """Total d'une équipe en temps réglementaire (marché Winamax « fulltime »)."""
    p = pr["pa"] if side == 1 else pr["pb"]
    return sum(v for k, v in enumerate(p) if k > line)


def p_spread(pr: dict, side: int, hcp: float) -> float:
    """Handicap prolongation comprise : side 1 = domicile avec handicap hcp."""
    if side == 1:
        return sum(v for d, v in pr["ecart"].items() if d + hcp > 0)
    return sum(v for d, v in pr["ecart"].items() if -d + hcp > 0)
