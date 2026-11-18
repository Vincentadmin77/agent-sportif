"""Test du modèle hockey sur les saisons passées (marche en avant, réajusté chaque semaine).
Mesure : log-loss du 1N2 temps réglementaire, du vainqueur prolongation comprise et des totaux 5,5 / 6,5,
contre une référence naïve (fréquences moyennes), et la calibration par tranche."""
import gzip, json, math, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0, ".")
from sportai.engine.hockey.poisson import HockeyModel, regulation_score, p_over

D = "/mnt/project-files/agent-sportif/data/hockey-espn/"
S = ["2022-2023", "2023-2024", "2024-2025", "2025-2026"]
games = []
for s in S:
    for g in json.load(gzip.open(D + f"nhl_{s}.json.gz", "rt")):
        g["saison"] = s; games.append(g)
games.sort(key=lambda g: g["date"])
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
test = [g for g in games if g["saison"] in ("2024-2025", "2025-2026")]
ll = defaultdict(float); n = 0; base = defaultdict(float); cal = defaultdict(lambda: [0, 0, 0.0])
model, last = None, None
freq = {"1": 0.40, "X": 0.22, "2": 0.38}
for g in test:
    t = T(g["date"])
    if last is None or (t - last).days >= 7:
        model = HockeyModel(halflife_days=float(sys.argv[1]) if len(sys.argv)>1 else 150, prior_games=float(sys.argv[2]) if len(sys.argv)>2 else 8).fit(games, t); last = t
    pr = model.probs(g["dom"], g["ext"])
    d, e, kind = regulation_score(g)
    o = "1" if d > e else ("X" if d == e else "2")
    p1, px, p2 = pr["reg_1x2"]
    ll["1N2"] -= math.log({"1": p1, "X": px, "2": p2}[o]); base["1N2"] -= math.log(freq[o])
    hw = int(g["bd"]) > int(g["be"])
    pv = pr["vainqueur"][0]
    ll["vainqueur"] -= math.log(pv if hw else 1 - pv); base["vainqueur"] -= math.log(0.54 if hw else 0.46)
    tot = int(g["bd"]) + int(g["be"])
    for L, bf in ((5.5, 0.52), (6.5, 0.33)):
        po = p_over(pr, L); ov = tot > L
        ll[f"plus {L}"] -= math.log(po if ov else 1 - po); base[f"plus {L}"] -= math.log(bf if ov else 1 - bf)
    for name, p, hit in (("fav vainqueur", max(pv, 1 - pv), (pv >= .5) == hw), ("nul 60 min", px, o == "X")):
        b = round(p * 20) / 20; c = cal[(name, b)]; c[0] += 1; c[1] += hit; c[2] += p
    n += 1
print("matchs testés", n)
for k in ll: print(f"{k:12s} modèle {ll[k]/n:.4f}  naïf {base[k]/n:.4f}")
for (name, b), (c, h, p) in sorted(cal.items()):
    if c >= 40: print(f"{name:14s} prévu {p/c:.3f} réel {h/c:.3f} ({c})")
