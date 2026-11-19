"""Hockey sur glace NHL (Vincent 18/11/2026) : passage de la nuit, positions de chaque acteur, Top 5, règlement.

Mêmes règles que les autres sports : une position par match et par acteur, en simple (cote 1,50-2,30),
figée 45 min avant le coup d'envoi, jamais réécrite ensuite. Cotes : WINAMAX SEUL (décision de Vincent 18/11),
via OddsPapi (compteur partagé avec les autres sports + compteur hockey, plafond hockey 1 000 par mois).
Résultats et historique : tableau des scores public (ESPN), gratuit, test interne.

Acteurs (chacun garde ses 5 meilleures positions du jour dans son Top 5) :
- modele : modèle de buts (sportai/engine/hockey/poisson.py) et marché d'accord (écart <= 10 pts, modèle >= marché),
  confiance = moitié modèle, moitié marché ;
- m (le Mathématicien) : son choix avant match où le marché voit le plus de chances ;
- test (le Sur-mesure Analytrack) : style tipster, jamais de victoire sèche : « équipe ou nul » (couverture des deux
  issues chez Winamax), handicap, totaux ; cote 1,50-2,00, plus forte chance du marché ;
- surmesure (le Sur-mesure en test) : favori à petite cote (<= 1,45, chance >= 68 %) + total du même match,
  cote du combiné 1,60-2,00 = produit des deux cotes, chance >= 50 %.
Le Direct hockey viendra ensuite (cotes en direct à trouver).

La journée hockey est la soirée américaine : les matchs de la nuit (Paris) comptent pour la veille.

    python scripts/hockey.py passage --jour 2026-11-18 [--doc DOC.json] --out DOC.json
    python scripts/hockey.py regler --doc DOC.json --out DOC.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sportai.engine.hockey.poisson import HockeyModel, p_over, p_spread, p_team_over  # noqa: E402

BASE = "/mnt/project-files/agent-sportif"
HIST = f"{BASE}/data/hockey-espn"
PAPI = "https://api.oddspapi.io/v4"
BOOK = "winamax.fr"
SPORT_ID, NHL = 15, 234
CNT_ALL = f"{BASE}/cotes-fr/oddspapi/compteur.json"
CNT_HK = f"{BASE}/hockey/oddspapi_compteur.json"
PLAFOND_HK = 1000
FREEZE = dt.timedelta(minutes=45)
ODDS_REFRESH = dt.timedelta(hours=3)  # cotes étrangères
WINAMAX_REFRESH = dt.timedelta(hours=8)  # Winamax : 1er relevé + relevé avant le gel (budget 1 000 / mois)
COTE_MIN, COTE_MAX = 1.50, 2.30
# Vincent 19/11 : les acteurs ciblent 1,70-2,00 sur les cotes étrangères (The Odds API, région eu) ;
# la cote jouée et affichée reste celle de Winamax, sur la même ligne.
ETR_MIN, ETR_MAX = 1.70, 2.00
ODDS_API = "https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds/"
N_TOP = 5
SAISON = "2026-2027"
UTC = dt.timezone.utc

REGLES = {
    "modele": "Une position par match : le modèle de buts et le marché d'accord, cote étrangère 1,70-2,00, jouée chez Winamax.",
    "m": "Une position par match : le choix où le marché voit le plus de chances, cote étrangère 1,70-2,00, jouée chez Winamax.",
    "test": "Une position par match, handicap ou total de buts du match, cote étrangère 1,70-2,00, jouée chez Winamax.",
    "surmesure": "Favori à petite cote + nombre de buts du même match, cote du combiné 1,60-2,00.",
}
TOP_REGLES = {
    "modele": "Ses 5 positions de plus forte confiance (modèle et marché à parts égales), une par match.",
    "m": "Ses 5 choix avant match où le marché voit le plus de chances, une par match.",
    "test": "Ses 5 positions où le marché voit le plus de chances, une par match.",
    "surmesure": "Ses 5 combinés où le marché voit le plus de chances, un par match.",
}


def now():
    return dt.datetime.now(UTC)


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def fr(x):
    return f"{x:g}".replace(".", ",")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return "".join(c for c in s if c.isalnum())


def same(a, b):
    a, b = norm(a), norm(b)
    return a == b or (len(a) > 5 and len(b) > 5 and (a in b or b in a)) or a.split()[-1:] == b.split()[-1:] and a[-6:] == b[-6:]


# ---------- ESPN (calendrier, scores, historique) ----------
def espn_day(us_date: str) -> list[dict]:
    u = f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={us_date.replace('-', '')}"
    for _ in range(3):
        try:
            j = json.load(urllib.request.urlopen(u, timeout=30))
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    else:
        return []
    out = []
    for e in j.get("events", []):
        if (e.get("season") or {}).get("type") not in (2, 3):
            continue
        c = e["competitions"][0]
        t = {x["homeAway"]: x for x in c["competitors"]}
        st = e["status"]["type"]
        out.append({"id": e["id"], "date": e["date"], "dom": t["home"]["team"]["displayName"],
                    "ext": t["away"]["team"]["displayName"],
                    "bd": int(t["home"].get("score") or 0), "be": int(t["away"].get("score") or 0),
                    "fin": st.get("detail"), "etat": st.get("state"), "termine": bool(st.get("completed")),
                    "periode": e["status"].get("period"), "reporte": st.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED")})
    return out


def history(refresh_from: dt.date | None = None) -> list[dict]:
    games = []
    for fn in sorted(os.listdir(HIST)):
        if fn.startswith("nhl_") and fn.endswith(".json.gz"):
            games += json.load(gzip.open(f"{HIST}/{fn}", "rt"))
    if refresh_from:
        cur_fn = f"{HIST}/nhl_{SAISON}.json.gz"
        cur = {g["id"]: g for g in json.load(gzip.open(cur_fn, "rt"))} if os.path.exists(cur_fn) else {}
        last = max((when(g["date"]).date() for g in cur.values()), default=dt.date(2026, 10, 1))
        d = last - dt.timedelta(days=1)
        added = 0
        while d <= refresh_from:
            for g in espn_day(d.isoformat()):
                if g["termine"] and not g["reporte"]:
                    cur[g["id"]] = {k: g[k] for k in ("id", "date", "dom", "ext", "bd", "be", "fin", "periode")}
                    added += 1
            d += dt.timedelta(days=1)
        with gzip.open(cur_fn, "wt") as f:
            json.dump(list(cur.values()), f)
        games = [g for g in games if not str(g.get("id")) in cur] + list(cur.values())
    seen = {}
    for g in games:
        seen[g["id"]] = g
    return sorted(seen.values(), key=lambda g: g["date"])


# ---------- OddsPapi Winamax ----------
def _count(fn):
    m = dt.date.today().strftime("%Y-%m")
    d = json.load(open(fn)) if os.path.exists(fn) else {}
    return d, m


def papi(path, **q):
    hk, m = _count(CNT_HK)
    if hk.get(m, 0) >= PLAFOND_HK:
        raise RuntimeError("plafond hockey OddsPapi du mois atteint")
    for fn in (CNT_ALL, CNT_HK):
        d, m = _count(fn)
        d[m] = d.get(m, 0) + 1
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        json.dump(d, open(fn, "w"))
    q["apiKey"] = os.environ["ODDSPAPI_KEY"]
    url = f"{PAPI}{path}?{urllib.parse.urlencode(q)}"
    for _ in range(3):
        try:
            d = json.load(urllib.request.urlopen(url, timeout=60))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3); continue
            raise
        if isinstance(d, dict) and (d.get("error") or {}).get("code") == "RATE_LIMITED":
            time.sleep(1.5); continue
        if isinstance(d, dict) and d.get("error"):
            raise RuntimeError(d["error"].get("message"))
        return d
    raise RuntimeError("OddsPapi : trop de requêtes rapprochées")


_MK = None


def markets_index():
    global _MK
    if _MK is None:
        _MK = {str(m["marketId"]): m for m in json.load(gzip.open(f"{BASE}/cotes-fr/oddspapi/markets.json.gz", "rt"))
               if m["sportId"] == SPORT_ID}
    return _MK


def winamax_lines(raw: dict) -> list[dict]:
    """Marchés Winamax lisibles : [{period, type, hcp, outcomes {nom: cote}}]."""
    mk = markets_index()
    out = []
    for mid, v in (raw or {}).items():
        m = mk.get(str(mid))
        if not m:
            continue
        names = {str(o["outcomeId"]): o["outcomeName"] for o in m["outcomes"]}
        oc = {}
        for oid, o in (v.get("outcomes") or {}).items():
            pl = (o.get("players") or {}).get("0") or {}
            if pl.get("price") and pl.get("active", True):
                oc[names.get(str(oid), oid)] = float(pl["price"])
        if oc:
            out.append({"period": m["period"], "type": m["marketType"], "hcp": float(m["handicap"]), "outcomes": oc})
    return out


# ---------- cotes étrangères (The Odds API) ----------
def foreign_odds(jour: str, need_fresh: bool) -> dict:
    """Cache hockey/oddsapi/<jour>.json, 1 appel pour toute la nuit (totaux + handicaps, région eu : 2 crédits)."""
    d = f"{BASE}/hockey/oddsapi"
    os.makedirs(d, exist_ok=True)
    fn = f"{d}/{jour}.json"
    old = json.load(open(fn)) if os.path.exists(fn) else None
    if old:
        age = now() - when(old["releve_a"])
        if age < (dt.timedelta(minutes=30) if need_fresh else ODDS_REFRESH):
            return old
    try:
        q = urllib.parse.urlencode({"apiKey": os.environ["THE_ODDS_API_KEY"], "regions": "eu",
                                    "markets": "totals,spreads", "oddsFormat": "decimal"})
        data = json.load(urllib.request.urlopen(f"{ODDS_API}?{q}", timeout=60))
        new = {"releve_a": iso(now()), "matchs": data}
        json.dump(new, open(fn, "w"))
        return new
    except Exception:  # noqa: BLE001
        return old or {"releve_a": None, "matchs": []}


def foreign_lines(fo: dict, dom: str, ext: str) -> list[dict]:
    """Lignes étrangères au format de winamax_lines : cote médiane des sites (2 au moins)."""
    import statistics
    g = next((x for x in fo.get("matchs", []) if same(x["home_team"], dom) and same(x["away_team"], ext)), None)
    if not g:
        return []
    acc = {}
    for b in g.get("bookmakers", []):
        if b["key"] in ("betfair_ex_eu", "matchbook"):
            continue
        for mk in b.get("markets", []):
            if mk["key"] == "totals":
                for o in mk["outcomes"]:
                    acc.setdefault(("totals", float(o["point"])), {}).setdefault(o["name"], []).append(o["price"])
            elif mk["key"] == "spreads":
                pts = {o["name"]: float(o["point"]) for o in mk["outcomes"]}
                h = pts.get(g["home_team"])
                if h is None:
                    continue
                for o in mk["outcomes"]:
                    k = "1" if o["name"] == g["home_team"] else "2"
                    acc.setdefault(("spreads", h), {}).setdefault(k, []).append(o["price"])
    out = []
    for (ty, h), oc in acc.items():
        if len(oc) == 2 and all(len(v) >= 2 for v in oc.values()):
            out.append({"period": "result", "type": ty, "hcp": h,
                        "outcomes": {k: statistics.median(v) for k, v in oc.items()}, "sites": min(len(v) for v in oc.values())})
    return out


def with_foreign(win_cands: list[dict], etr_cands: list[dict]) -> list[dict]:
    """Candidats étrangers jouables chez Winamax : cote = Winamax, cote_etrangere et chance = sites étrangers."""
    wmap = {c["pari"]: c for c in win_cands}
    out = []
    for c in etr_cands:
        w = wmap.get(c["pari"])
        if w:
            out.append({**c, "cote": w["cote"], "cote_etrangere": c["cote"], "proba_marche_winamax": w["proba_marche"]})
    return out


# ---------- candidats ----------
def candidates(match: dict, lines: list[dict], pr: dict | None) -> list[dict]:
    dom, ext = match["domicile"], match["exterieur"]
    out = []

    def add(pari, marche, cote, pm, p, famille, regle):
        out.append({"pari": pari, "marche": marche, "cote": round(cote, 2), "proba_marche": round(pm, 4),
                    "proba": round(p, 4) if p is not None else None, "famille": famille, "regle": regle})

    for L in lines:
        oc, inv = L["outcomes"], {k: 1 / v for k, v in L["outcomes"].items()}
        z = sum(inv.values())
        pm = {k: v / z for k, v in inv.items()}
        per, ty, h = L["period"], L["type"], L["hcp"]
        if per == "fulltime" and ty == "1x2" and {"1", "X", "2"} <= set(oc):
            p1, px, p2 = pr["reg_1x2"] if pr else (None, None, None)
            add(f"Victoire {dom} (temps réglementaire)", "vainqueur", oc["1"], pm["1"], p1, "vainqueur", {"issue": "1"})
            add(f"Victoire {ext} (temps réglementaire)", "vainqueur", oc["2"], pm["2"], p2, "vainqueur", {"issue": "2"})
            add("Match nul à la fin du temps réglementaire", "vainqueur", oc["X"], pm["X"], px, "vainqueur", {"issue": "X"})
            for side, k, team in ((1, "1", dom), (2, "2", ext)):
                c = 1 / (1 / oc[k] + 1 / oc["X"])
                pp = None if not pr else (p1 if side == 1 else p2) + px
                add(f"{team} ou nul (temps réglementaire)", "double_chance", c, pm[k] + pm["X"], pp, "equipe_ou_nul",
                    {"issue": k + "X", "cote_type": "couverture de deux issues"})
        elif per == "result" and ty == "totals" and {"Over", "Under"} <= set(oc):
            po = p_over(pr, h) if pr else None
            add(f"Plus de {fr(h)} buts dans le match", "total", oc["Over"], pm["Over"], po, "total", {"plus": h})
            add(f"Moins de {fr(h)} buts dans le match", "total", oc["Under"], pm["Under"],
                None if po is None else 1 - po - (pr["total"].get(int(h), 0) if h == int(h) else 0), "total", {"moins": h})
        elif per == "result" and ty == "spreads" and {"1", "2"} <= set(oc) and h != int(h):
            ph = p_spread(pr, 1, h) if pr else None
            add(f"{dom} handicap {'+' if h > 0 else ''}{fr(h)} buts", "handicap", oc["1"], pm["1"], ph, "handicap", {"side": 1, "hcp": h})
            add(f"{ext} handicap {'+' if -h > 0 else ''}{fr(-h)} buts", "handicap", oc["2"], pm["2"],
                None if ph is None else 1 - ph, "handicap", {"side": 2, "hcp": -h})
        elif per == "fulltime" and ty in ("teamtotals-team1", "teamtotals-team2") and {"Over", "Under"} <= set(oc) and h != int(h):
            side = 1 if ty.endswith("1") else 2
            team = dom if side == 1 else ext
            po = p_team_over(pr, side, h) if pr else None
            add(f"{team} : plus de {fr(h)} buts (temps réglementaire)", "total_equipe", oc["Over"], pm["Over"], po, "total_equipe", {"side": side, "plus": h})
            add(f"{team} : moins de {fr(h)} buts (temps réglementaire)", "total_equipe", oc["Under"], pm["Under"],
                None if po is None else 1 - po, "total_equipe", {"side": side, "moins": h})
    return out


def _etr(c):
    return c.get("cote_etrangere") is not None and ETR_MIN <= c["cote_etrangere"] <= ETR_MAX


def pick(actor: str, cands: list[dict]) -> dict | None:
    if actor == "modele":
        ok = [c for c in cands if _etr(c) and c["proba"] is not None
              and c["proba"] >= c["proba_marche"] - 0.005 and abs(c["proba"] - c["proba_marche"]) <= 0.10]
        best = max(ok, key=lambda c: (c["proba"] + c["proba_marche"]) / 2, default=None)
        return best and {**best, "confiance": round((best["proba"] + best["proba_marche"]) / 2, 4)}
    if actor == "m":
        ok = [c for c in cands if _etr(c)]
        best = max(ok, key=lambda c: c["proba_marche"], default=None)
        return best and {**best, "confiance": best["proba_marche"]}
    if actor == "test":
        ok = [c for c in cands if _etr(c) and c["famille"] in ("handicap", "total")]
        best = max(ok, key=lambda c: c["proba_marche"], default=None)
        return best and {**best, "confiance": best["proba_marche"]}
    if actor == "surmesure":
        cands = [c for c in cands if c.get("cote_etrangere") is None]  # combinés : cotes Winamax
        favs = [c for c in cands if c["famille"] in ("vainqueur", "equipe_ou_nul", "handicap") and c["cote"] <= 1.45 and c["proba_marche"] >= 0.68]
        tots = [c for c in cands if c["famille"] == "total" and c["proba_marche"] >= 0.55]
        combos = []
        for f in favs:
            for t in tots:
                cote = f["cote"] * t["cote"]
                ch = f["proba_marche"] * t["proba_marche"]
                if 1.60 <= cote <= 2.00 and ch >= 0.50:
                    combos.append({"pari": f"{f['pari']} et {t['pari'][0].lower()}{t['pari'][1:]}", "marche": "combine_meme_match",
                                   "cote": round(cote, 2), "proba_marche": round(ch, 4), "proba": None, "famille": "combine",
                                   "regle": {"jambes": [f, t], "cote_type": "produit des deux cotes"}, "confiance": round(ch, 4)})
        return max(combos, key=lambda c: c["confiance"], default=None)
    raise ValueError(actor)


# ---------- règlement ----------
def settle_leg(c: dict, m: dict) -> str | None:
    s = m.get("score_final")
    if not s:
        return None
    bd, be = s["domicile"], s["exterieur"]
    rd, re_ = s["reg_domicile"], s["reg_exterieur"]
    r = c.get("regle") or {}
    f = c["famille"]
    if f == "combine":
        legs = [settle_leg(x, m) for x in r["jambes"]]
        if None in legs:
            return None
        return "perdu" if "perdu" in legs else ("rembourse" if all(x == "rembourse" for x in legs) else "gagne")
    if f == "vainqueur":
        o = "1" if rd > re_ else ("X" if rd == re_ else "2")
        return "gagne" if o == r["issue"] else "perdu"
    if f == "equipe_ou_nul":
        o = "1" if rd > re_ else ("X" if rd == re_ else "2")
        return "gagne" if o in r["issue"] else "perdu"
    if f == "total":
        t = bd + be
        L = r.get("plus", r.get("moins"))
        if t == L:
            return "rembourse"
        return "gagne" if (t > L) == ("plus" in r) else "perdu"
    if f == "handicap":
        d = (bd - be) if r["side"] == 1 else (be - bd)
        return "gagne" if d + r["hcp"] > 0 else "perdu"
    if f == "total_equipe":
        g = rd if r["side"] == 1 else re_
        L = r.get("plus", r.get("moins"))
        return "gagne" if (g > L) == ("plus" in r) else "perdu"
    return None


def ratio(pos):
    g = sum(1 for p in pos if p.get("resultat") == "gagne")
    pe = sum(1 for p in pos if p.get("resultat") == "perdu")
    r = sum(1 for p in pos if p.get("resultat") == "rembourse")
    return {"gagnees": g, "perdues": pe, "remboursees": r, "jouees": g + pe}


def score_final(g):
    bd, be = g["bd"], g["be"]
    fin = (g.get("fin") or "").upper()
    if "OT" in fin or "SO" in fin:
        rd = re_ = min(bd, be)
        apres = "tirs au but" if "SO" in fin else "prolongation"
    else:
        rd, re_, apres = bd, be, None
    return {"domicile": bd, "exterieur": be, "reg_domicile": rd, "reg_exterieur": re_, "apres": apres}


def refresh_status(doc, games_by_id):
    for mid, m in doc["matchs"].items():
        g = games_by_id.get(m["espn_id"])
        if not g:
            continue
        if g["reporte"]:
            m["statut"], m["reporte"] = "reporte", True
        elif g["termine"]:
            m["statut"] = "termine"
            m["score_final"] = score_final(g)
            m["score"] = f"{g['bd']}-{g['be']}" + (f" ({m['score_final']['apres']})" if m["score_final"]["apres"] else "")
        elif g["etat"] == "in":
            m["statut"] = "en_cours"
            m["score"] = f"{g['bd']}-{g['be']}"
            m["periode"] = g["periode"]


def apply_results(doc):
    for actor, block in doc["acteurs"].items():
        for mid, p in block["positions"].items():
            if not p.get("pari"):
                continue
            m = doc["matchs"].get(mid, {})
            if m.get("reporte"):
                p["resultat"] = "rembourse"
            elif m.get("statut") == "termine":
                p["resultat"] = settle_leg(p, m)
            p["statut"], p["score"] = m.get("statut"), m.get("score")
    build_top5(doc)


def build_top5(doc):
    t = now()
    top = doc.setdefault("top5", {})
    for actor, block in doc["acteurs"].items():
        prev = top.get(actor, {}).get("positions", [])
        fixed = [p for p in prev if p.get("fige")]
        used = {p["match_id"] for p in fixed}
        pool = []
        for mid, p in block["positions"].items():
            if not p.get("pari") or mid in used:
                continue
            pool.append({"match_id": mid, "match": doc["matchs"][mid]["match"], "competition": "NHL",
                         "coup_envoi": doc["matchs"][mid]["coup_envoi"], **{k: p.get(k) for k in
                         ("pari", "cote", "cote_etrangere", "confiance", "resultat", "fige", "statut", "score")}})
        pool.sort(key=lambda p: -p["confiance"])
        frozen_pool = [p for p in pool if p.get("fige")]
        free = [p for p in pool if not p.get("fige")]
        keep = fixed + frozen_pool
        keep = sorted(keep, key=lambda p: -p["confiance"])[:N_TOP]
        keep += free[: max(0, N_TOP - len(keep))]
        # résultats à jour pour les figées
        for p in keep:
            src = block["positions"].get(p["match_id"], {})
            p.update({k: src.get(k) for k in ("resultat", "statut", "score")})
        top[actor] = {"regle": TOP_REGLES[actor], "positions": keep, "ratio": ratio(keep),
                      "candidats": sum(1 for p in block["positions"].values() if p.get("pari"))}
    doc["top5_regle"] = "Chaque acteur garde ses 5 positions de plus forte confiance du jour, une par match, figées 45 min avant le coup d'envoi."


# ---------- passage ----------
def passage(a):
    doc = json.load(open(a.doc)) if a.doc and os.path.exists(a.doc) else {}
    jour = dt.date.fromisoformat(a.jour)
    doc.setdefault("jour", a.jour)
    doc.update({"sport": "hockey", "competition": "NHL", "source_cotes": "Winamax"})
    doc.setdefault("matchs", {})
    doc.setdefault("acteurs", {k: {"regle": REGLES[k], "positions": {}} for k in REGLES})
    t = now()
    games = history(refresh_from=jour - dt.timedelta(days=1))
    model = HockeyModel().fit(games, t)
    day = espn_day(a.jour)
    by_id = {g["id"]: g for g in day}
    # matchs OddsPapi du créneau (1 requête par jour, gardée en cache)
    cache_dir = f"{BASE}/hockey/oddspapi"
    os.makedirs(cache_dir, exist_ok=True)
    fx_fn = f"{cache_dir}/fixtures_{a.jour}.json"
    if os.path.exists(fx_fn):
        fixtures = json.load(open(fx_fn))
    else:
        start = dt.datetime.combine(jour, dt.time(16, 0), UTC)
        fixtures = [f for f in papi("/fixtures", sportId=SPORT_ID, **{"from": iso(start), "to": iso(start + dt.timedelta(hours=20))})
                    if f.get("tournamentId") == NHL]
        json.dump(fixtures, open(fx_fn, "w"))
    erreurs = []
    near_any = any(not g["reporte"] and FREEZE < when(g["date"]) - t <= dt.timedelta(minutes=a.figer_dans) for g in day)
    fo = foreign_odds(a.jour, near_any) if any(when(g["date"]) - t > FREEZE for g in day) else {"matchs": []}
    doc["cotes_etrangeres_releve_a"] = fo.get("releve_a")
    erreurs = []
    for g in day:
        mid = f"hockey-{g['id']}"
        ko = when(g["date"])
        m = doc["matchs"].setdefault(mid, {})
        m.update({"espn_id": g["id"], "match": f"{g['dom']} - {g['ext']}", "domicile": g["dom"], "exterieur": g["ext"],
                  "competition": "NHL", "coup_envoi": iso(ko)})
        m.setdefault("statut", "a_venir")
        if g["reporte"]:
            m["statut"], m["reporte"] = "reporte", True
            continue
        if t >= ko - FREEZE:
            for blk in doc["acteurs"].values():
                p = blk["positions"].get(mid)
                if p and p.get("pari"):
                    p["fige"] = True
            continue  # trop tard pour changer : positions figées telles quelles
        fx = next((f for f in fixtures if same(f["participant1Name"], g["dom"]) and same(f["participant2Name"], g["ext"])), None)
        if not fx:
            m["cotes_etat"] = "match introuvable chez Winamax"
            continue
        m["fixture_id"] = fx["fixtureId"]
        o_fn = f"{cache_dir}/odds_{fx['fixtureId']}.json"
        need = True
        if os.path.exists(o_fn):
            od = json.load(open(o_fn))
            age = t - when(od["releve_a"])
            near = ko - t <= dt.timedelta(minutes=a.figer_dans)
            need = age > WINAMAX_REFRESH or (near and age > dt.timedelta(minutes=30))
        if need:
            try:
                time.sleep(1.2)
                d = papi("/odds", fixtureId=fx["fixtureId"], bookmakers=BOOK)
                raw = ((d.get("bookmakerOdds") or {}).get(BOOK) or {}).get("markets") or {}
                od = {"releve_a": iso(t), "markets": raw}
                json.dump(od, open(o_fn, "w"))
            except Exception as e:  # noqa: BLE001
                erreurs.append(str(e))
                if not os.path.exists(o_fn):
                    continue
                od = json.load(open(o_fn))
        lines = winamax_lines(od["markets"])
        pr = model.probs(g["dom"], g["ext"]) if model.known(g["dom"]) and model.known(g["ext"]) else None
        if pr:
            p1, px, p2 = pr["reg_1x2"]
            a_, b_ = pr["lam"]
            m["modele"] = {"buts_attendus": {"domicile": round(a_, 2), "exterieur": round(b_, 2)},
                           "proba_60min": {"domicile": round(p1, 3), "nul": round(px, 3), "exterieur": round(p2, 3)},
                           "proba_vainqueur": {"domicile": round(pr["vainqueur"][0], 3), "exterieur": round(pr["vainqueur"][1], 3)}}
        m["cotes_releve_a"] = od["releve_a"]
        one = next((L for L in lines if L["period"] == "fulltime" and L["type"] == "1x2"), None)
        if one:
            m["cotes_1n2"] = {"domicile": one["outcomes"].get("1"), "nul": one["outcomes"].get("X"), "exterieur": one["outcomes"].get("2")}
        wc = candidates(m, lines, pr)
        flines = foreign_lines(fo, g["dom"], g["ext"])
        cands = wc + with_foreign(wc, candidates(m, flines, pr))
        m["nb_marches"] = len(lines)
        m["nb_lignes_etrangeres"] = len(flines)
        for actor, blk in doc["acteurs"].items():
            cur = blk["positions"].get(mid)
            if cur and cur.get("fige"):
                continue
            c = pick(actor, cands)
            if c:
                blk["positions"][mid] = {**c, "fige": False, "maj": iso(t)}
            else:
                blk["positions"][mid] = {"pari": None, "raison": "rien ne respecte sa règle sur ce match"}
        if ko - t <= dt.timedelta(minutes=a.figer_dans):
            for blk in doc["acteurs"].values():
                p = blk["positions"].get(mid)
                if p and p.get("pari"):
                    p["fige"], p["fige_a"] = True, iso(t)
    refresh_status(doc, by_id)
    apply_results(doc)
    doc["maj"] = iso(t)
    doc["erreurs"] = erreurs
    hk, mo = _count(CNT_HK)
    doc["requetes_mois"] = hk.get(mo, 0)
    json.dump(doc, open(a.out, "w"), ensure_ascii=False, indent=1)
    n = {k: sum(1 for p in v["positions"].values() if p.get("pari")) for k, v in doc["acteurs"].items()}
    print(f"{a.jour}: {len(day)} matchs, positions {n}, requêtes hockey du mois {doc['requetes_mois']}", erreurs[:2])


def regler(a):
    doc = json.load(open(a.doc))
    day = espn_day(doc["jour"])
    refresh_status(doc, {g["id"]: g for g in day})
    for blk in doc["acteurs"].values():
        for p in blk["positions"].values():
            if p.get("pari"):
                p["fige"] = True
    apply_results(doc)
    doc["maj"] = iso(now())
    json.dump(doc, open(a.out, "w"), ensure_ascii=False, indent=1)
    print({k: v["ratio"] for k, v in doc["top5"].items()})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("passage")
    p.add_argument("--jour", required=True)
    p.add_argument("--doc")
    p.add_argument("--out", required=True)
    p.add_argument("--figer-dans", type=int, default=50, help="fige les matchs qui commencent dans moins de N minutes")
    r = sub.add_parser("regler")
    r.add_argument("--doc", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args()
    passage(a) if a.cmd == "passage" else regler(a)


if __name__ == "__main__":
    main()
