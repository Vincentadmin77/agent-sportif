"""Calibrate the in-play red-card factors (LiveConfig.red_card_own_factor /
red_card_opp_factor) on real matches. See docs/12-cartons-rouges.md.

Fetches finished top-5-league fixtures (API-Football, 2024 and 2025 seasons)
with their events, 20 fixtures per request, cached as JSON so reruns cost no
request. Then, for each match whose first red card falls between minutes 10
and 85, compares goals scored after the red card (until a second red card or
the end) with the goals expected from leave-one-out team attack/defence rates
and the empirical within-match time profile of goals.

Usage (from backend/): API_FOOTBALL_KEY=... python scripts/calibrate_red_cards.py
"""
import glob, json, os, sys
from sportai.config import get_settings
from sportai.connectors.api_football import ApiFootballProvider

D = os.environ.get("REDCARD_CACHE", "/mnt/project-files/agent-sportif/data/redcards")
os.makedirs(D, exist_ok=True)
BUDGET = 2500
cnt_file = os.path.join(D, "_request_count.json")
prov = None
used = json.load(open(cnt_file))["used"] if os.path.exists(cnt_file) else 0


def get(path, params, name):
    global used, prov
    if prov is None:
        prov = ApiFootballProvider(get_settings().api_football_key)
    fn = os.path.join(D, name + ".json")
    if os.path.exists(fn):
        return json.load(open(fn))
    if used >= BUDGET:
        raise SystemExit("budget reached")
    data = prov._http.get_json(path, params, ttl=0)
    used += 1
    json.dump({"used": used}, open(cnt_file, "w"))
    if data.get("errors"):
        print("ERR", path, params, data["errors"], file=sys.stderr)
        return data
    if "ids" in params:
        data = {"response": [{k: x[k] for k in ("fixture", "league", "teams", "goals", "score", "events")} for x in data["response"]]}
    json.dump(data, open(fn, "w"))
    h = prov._http.last_headers
    print(path, params, "remaining day:", h.get("x-ratelimit-requests-remaining"), "used:", used, file=sys.stderr)
    return data


def fetch():
    leagues = [61, 39, 140, 78, 135]
    ids = []
    for lg in leagues:
        for s in (2024, 2025):
            d = get("/fixtures", {"league": lg, "season": s}, f"fixtures_{lg}_{s}")
            fin = [f for f in d["response"] if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
            print(lg, s, len(d["response"]), "finished", len(fin), file=sys.stderr)
            ids += [f["fixture"]["id"] for f in fin]
    ids = sorted(set(ids))
    limit = int(os.environ.get("MAXBATCH", "100000"))
    for n, i in enumerate(range(0, len(ids), 20)):
        if n >= limit:
            break
        chunk = ids[i:i + 20]
        get("/fixtures", {"ids": "-".join(map(str, chunk))}, f"batch_{chunk[0]}_{chunk[-1]}")
    print("total fixtures", len(ids), "requests used", used, file=sys.stderr)



def tkey(t):
    e = t.get("extra") or 0
    return t["elapsed"] + e / 100.0


def load():
    out = []
    for fn in sorted(glob.glob(D + "/batch_*.json")):
        for x in json.load(open(fn))["response"]:
            if x["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
                continue
            h, a = x["teams"]["home"]["id"], x["teams"]["away"]["id"]
            goals, reds = [], []
            for e in x["events"]:
                side = "h" if e["team"]["id"] == h else "a" if e["team"]["id"] == a else None
                if e["type"] == "Goal" and e["detail"] in ("Normal Goal", "Penalty", "Own Goal"):
                    if e.get("comments") == "Penalty Shootout":
                        continue
                    goals.append((tkey(e["time"]), side, e["detail"]))
                if e["type"] == "Card" and e["detail"] in ("Red Card", "Second Yellow card"):
                    reds.append((tkey(e["time"]), side, e["player"]["name"]))
            out.append(dict(id=x["fixture"]["id"], league=x["league"]["id"], season=x["league"]["season"],
                            h=h, a=a, hn=x["teams"]["home"]["name"], an=x["teams"]["away"]["name"],
                            fh=x["goals"]["home"], fa=x["goals"]["away"], goals=goals, reds=reds))
    return out



import numpy as np
from collections import defaultdict

fetch()

ms = load()
rng = np.random.default_rng(42)

# ---- team strength, leave-one-out, per league-season --------------------
by_ls = defaultdict(list)
for m in ms:
    by_ls[(m["league"], m["season"])].append(m)


def strengths(m):
    games = [x for x in by_ls[(m["league"], m["season"])] if x["id"] != m["id"]]
    hg = np.mean([x["fh"] for x in games]); ag = np.mean([x["fa"] for x in games])
    avg = (hg + ag) / 2
    gf = defaultdict(float); ga = defaultdict(float); n = defaultdict(int)
    for x in games:
        gf[x["h"]] += x["fh"]; ga[x["h"]] += x["fa"]; n[x["h"]] += 1
        gf[x["a"]] += x["fa"]; ga[x["a"]] += x["fh"]; n[x["a"]] += 1
    att = lambda t: gf[t] / n[t] / avg
    dfn = lambda t: ga[t] / n[t] / avg
    lam = att(m["h"]) * dfn(m["a"]) * hg
    mu = att(m["a"]) * dfn(m["h"]) * ag
    return lam, mu


# cache league averages per league-season to speed up (LOO with ~380 games is cheap enough)
# ---- time profile from matches without any red card ----------------------
ctrl = [m for m in ms if not m["reds"]]
gt = np.array([g[0] for m in ctrl for g in m["goals"]])
ngoals_ctrl = len(gt)


def S(t):
    """share of a match's goals scored strictly after time key t (non-red matches)."""
    return float((gt > t).mean())


def uniform(t):
    return max(95 - t, 0) / 95


# ---- red-card sample ---------------------------------------------------
rows = []
skipped = defaultdict(int)
for m in ms:
    if not m["reds"]:
        continue
    reds = sorted(m["reds"])
    t, side, _ = reds[0]
    if side is None:
        skipped["no side"] += 1; continue
    if not (10 <= t < 86):
        skipped["outside 10-85"] += 1; continue
    t2 = None
    for r in reds[1:]:
        t2 = r[0]; break
    if t2 is not None and t2 <= t:
        skipped["simultaneous reds"] += 1; continue
    lam, mu = strengths(m)
    own_pre = lam if side == "h" else mu
    opp_pre = mu if side == "h" else lam
    end = t2 if t2 is not None else 999
    own_after = sum(1 for g in m["goals"] if g[1] == side and t < g[0] <= end)
    opp_after = sum(1 for g in m["goals"] if g[1] != side and t < g[0] <= end)
    own_before = sum(1 for g in m["goals"] if g[1] == side and g[0] <= t)
    opp_before = sum(1 for g in m["goals"] if g[1] != side and g[0] <= t)
    share = S(t) - (S(t2) if t2 is not None else 0.0)
    ushare = uniform(t) - (uniform(t2) if t2 is not None else 0.0)
    state = "level" if own_before == opp_before else ("leading" if own_before > opp_before else "trailing")
    rows.append(dict(id=m["id"], t=t, single=t2 is None, own_obs=own_after, opp_obs=opp_after,
                     own_exp=own_pre * share, opp_exp=opp_pre * share,
                     own_uexp=own_pre * ushare, opp_uexp=opp_pre * ushare,
                     own_bobs=own_before, opp_bobs=opp_before,
                     own_bexp=own_pre * (1 - S(t)), opp_bexp=opp_pre * (1 - S(t)), state=state,
                     own_pre=own_pre, opp_pre=opp_pre))


def est(rs, o="own_obs", e="own_exp", B=10000):
    obs = np.array([r[o] for r in rs], float); exp = np.array([r[e] for r in rs], float)
    point = obs.sum() / exp.sum()
    idx = rng.integers(0, len(rs), (B, len(rs)))
    boots = obs[idx].sum(1) / exp[idx].sum(1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return dict(n=len(rs), obs=int(obs.sum()), exp=round(float(exp.sum()), 1), est=round(float(point), 3),
                lo=round(float(lo), 3), hi=round(float(hi), 3))


res = {"n_matches_total": len(ms), "n_matches_with_red": sum(1 for m in ms if m["reds"]),
       "skipped": dict(skipped), "ctrl_matches": len(ctrl),
       "time_profile": {f"S({t})": round(S(t), 3) for t in (15, 30, 45, 60, 75, 85)},
       "uniform95": {f"U({t})": round(uniform(t), 3) for t in (15, 30, 45, 60, 75, 85)}}
for name, rs in [("all (censored at 2nd red)", rows), ("single red only", [r for r in rows if r["single"]])]:
    res[name] = {
        "own (10 men)": est(rs), "opp (11 men)": est(rs, "opp_obs", "opp_exp"),
        "own vs uniform-95 baseline": est(rs, "own_obs", "own_uexp"),
        "opp vs uniform-95 baseline": est(rs, "opp_obs", "opp_uexp"),
        "own before red (diag)": est(rs, "own_bobs", "own_bexp"),
        "opp before red (diag)": est(rs, "opp_bobs", "opp_bexp"),
    }
for st in ("level", "leading", "trailing"):
    rs = [r for r in rows if r["state"] == st]
    res["state=" + st] = {"own (10 men)": est(rs), "opp (11 men)": est(rs, "opp_obs", "opp_exp")}
for lo_, hi_ in ((10, 45.99), (46, 70), (70.01, 86)):
    rs = [r for r in rows if lo_ <= r["t"] <= hi_]
    res[f"t in [{lo_},{hi_}]"] = {"own (10 men)": est(rs), "opp (11 men)": est(rs, "opp_obs", "opp_exp")}

# ---- control: score-state effect without red cards (same time points) -----
ts = [r["t"] for r in rows]
cstate = defaultdict(lambda: [0.0, 0.0])
cache = {}
for m in rng.choice(ctrl, size=min(len(ctrl), 1500), replace=False):
    lam, mu = strengths(m)
    for t in rng.choice(ts, 3):
        hb = sum(1 for g in m["goals"] if g[1] == "h" and g[0] <= t)
        ab = sum(1 for g in m["goals"] if g[1] == "a" and g[0] <= t)
        for side, pre, mine, theirs in (("h", lam, hb, ab), ("a", mu, ab, hb)):
            st = "level" if mine == theirs else ("leading" if mine > theirs else "trailing")
            obs = sum(1 for g in m["goals"] if g[1] == side and g[0] > t)
            cstate[st][0] += obs; cstate[st][1] += pre * S(t)
res["control (no red) obs/exp by own score state"] = {k: round(v[0] / v[1], 3) for k, v in cstate.items()}
print(json.dumps(res, indent=1))
json.dump(res, open(os.path.join(D, "_results.json"), "w"), indent=1)
