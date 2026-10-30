"""One specialist agent per sport, run à blanc (owner 2026-10-30: "un agent par sport ... qui s'inspire des meilleurs").

Each specialist keeps its own sport's markets and a decision method taken from the best-known people of its field.
It reads the same match documents as the official passes (no extra API call for football and basketball), gives ONE
choice per match or passes, and is measured apart: never published, never in the official ratio, never in the M's.
The M's settings and the official positions do not move (pause until the M's report of 2026-11-02).

Football (inspired by Benham / Bloom, Sumpter, Tippett): the market is the best forecaster there is, so the chance
used is mostly the market's (MARKET_WEIGHT) and the model only nudges it; the model is built on expected goals and
shots (process) rather than past scores. A position needs value at the best odds AND a model that agrees with the
market (never more than FOOT_MAX_ABOVE above it: when it is, the model usually missed news). Only the three priority
axes (result, goals, both teams score), never "moins de 0,5", no quarter lines.

Basketball (inspired by Voulgaris): a projected score, then a bet only when the projection is far from the bookmakers'
main number, in POINTS (total >= BASKET_TOTAL_GAP, spread >= BASKET_SPREAD_GAP), at the main line; never more than
twice that far (the ratings do not see injuries yet: a huge gap is usually a missing player, not an edge). Totals and spreads
only: no moneyline on a favourite. Early in the season the ratings are young, so the gap needed is larger.

Tennis (inspired by Sackmann): surface Elo is the forecast (overall and surface ratings blended, K shrinking with
experience). A position only on the winner (what Elo forecasts; games, sets and aces derived from it were weaker on
the 2024-26 backtest), when Elo and the market agree closely (TENNIS_MAX_GAP), there is value at the best odds, and neither player is back from a long break (Elo is stale after TENNIS_LAYOFF_DAYS without a match).
"""

from __future__ import annotations

from datetime import date, datetime

ODDS = (1.50, 2.30)
SETTLED = ("gagne", "perdu", "rembourse")

# --- football
MARKET_WEIGHT = 0.65
FOOT_FAMILIES = frozenset({"victoire", "double_chance", "rembourse_si_nul", "btts_oui", "plus_1_5", "plus_2_5",
                           "plus_3_5", "moins_1_5", "moins_2_5", "moins_3_5"})
FOOT_MIN_CHANCE = 0.55
FOOT_MAX_ABOVE = 0.06
FOOT_MIN_VALUE = 1.0

# --- basketball
BASKET_TOTAL_GAP = 4.0
BASKET_SPREAD_GAP = 3.0
BASKET_MAX_FACTOR = 2.0  # further than twice the gap: usually an absence the ratings do not see (no injury list yet)
BASKET_EARLY_DAYS = 21  # first three weeks of a season: gaps x BASKET_EARLY_FACTOR
BASKET_EARLY_FACTOR = 1.5
BASKET_SEASON_START = {"NBA": date(2026, 10, 20), "WNBA": date(2026, 5, 15)}

# --- tennis
TENNIS_MARKETS = ("vainqueur",)  # Elo is a match-win model; the games markets derived from it did worse (30/10)
TENNIS_MAX_GAP = 0.05
TENNIS_MIN_CHANCE = 0.55
TENNIS_LAYOFF_DAYS = 60

NAMES = {"foot": "spécialiste foot", "basket": "spécialiste basket", "tennis": "spécialiste tennis"}


def _pc(x: float) -> str:
    return f"{round(x * 100)} %"


def _pts(x: float) -> str:
    return f"{x:.1f}".replace(".0", "").replace(".", ",") + " points"


def _n(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


# ---------------------------------------------------------------- football

def foot_options(m: dict) -> list[dict]:
    from sportai.engine.football.markets import is_quarter
    from sportai.services.revue import _keys, famille

    keys = _keys(m.get("domicile") or "", m.get("exterieur") or "")
    rows = ((m.get("tous_marches") or {}).get("analyses") or []) + ((m.get("buts") or {}).get("marches") or [])
    out, seen = [], set()
    for r in rows:
        key, cote, pm, p = keys.get(r.get("pari")), r.get("cote"), r.get("proba_marche"), r.get("proba")
        if key is None or is_quarter(key.line) or r.get("pari") in seen:
            continue
        if not all(isinstance(x, (int, float)) for x in (cote, pm, p)) or not ODDS[0] <= cote <= ODDS[1]:
            continue
        if famille(key.market, key.selection, key.line) not in FOOT_FAMILIES:
            continue
        seen.add(r["pari"])
        chance = MARKET_WEIGHT * pm + (1 - MARKET_WEIGHT) * p
        out.append({"pari": r["pari"], "marche": r.get("marche"), "cote_prise": cote, "p_marche": round(pm, 4),
                    "p_modele": round(p, 4), "chance": round(chance, 4),
                    "ok": chance >= FOOT_MIN_CHANCE and chance * cote >= FOOT_MIN_VALUE and 0 <= p - pm <= FOOT_MAX_ABOVE})
    return sorted(out, key=lambda o: -o["chance"])


def foot_choice(m: dict) -> dict:
    opts = foot_options(m)
    ok = [o for o in opts if o["ok"]]
    if ok:
        c = {k: v for k, v in ok[0].items() if k != "ok"}
        c["raison"] = (f"Le marché la voit à {_pc(c['p_marche'])}, le modèle (buts attendus, tirs) à {_pc(c['p_modele'])} : "
                       f"ils sont d'accord et la cote de {_n(c['cote_prise'])} paie plus que la chance.")
        return c
    if not opts:
        return {"pari": None, "raison": "Je passe : aucune position de résultat, de buts ou « les deux marquent » entre 1,50 et 2,30."}
    b = opts[0]
    if b["p_modele"] - b["p_marche"] > FOOT_MAX_ABOVE:
        why = f"le modèle ({_pc(b['p_modele'])}) est trop au-dessus du marché ({_pc(b['p_marche'])}), il a sans doute raté une info"
    elif b["p_modele"] < b["p_marche"]:
        why = f"le modèle ({_pc(b['p_modele'])}) y croit moins que le marché ({_pc(b['p_marche'])}) : pas de valeur"
    else:
        why = f"chance retenue {_pc(b['chance'])}, il faut {_pc(FOOT_MIN_CHANCE)} et une cote qui paie plus que la chance"
    return {"pari": None, "raison": f"Je passe : la meilleure option, {b['pari']} à {_n(b['cote_prise'])} : {why}.",
            "meilleure_option": {k: b[k] for k in ("pari", "cote_prise", "p_marche", "p_modele")}}


def foot_settle(choice: dict, m: dict) -> None:
    from sportai.engine.football.markets import settle_score
    from sportai.services.revue import _keys, _score

    if not choice.get("pari") or choice.get("resultat") in SETTLED or m.get("statut") != "termine":
        return
    sc, key = _score(m), _keys(m.get("domicile") or "", m.get("exterieur") or "").get(choice["pari"])
    res = settle_score(key, *sc) if sc and key else None
    if res in SETTLED:
        choice["resultat"] = res


# ---------------------------------------------------------------- basketball

def _main_line(lines: list[dict], market: str) -> float | None:
    """The bookmakers' main number of a market: the line where the best odds of the two sides are closest."""
    by = {}
    for r in lines:
        if r.get("marche") == market and isinstance(r.get("ligne"), (int, float)) and isinstance(r.get("cote"), (int, float)):
            side = by.setdefault(r["ligne"], {})
            side[r["selection"]] = max(side.get(r["selection"], 0), r["cote"])
    both = {ln: s for ln, s in by.items() if len(s) == 2}
    return min(both, key=lambda ln: abs(1 / min(both[ln].values()) - 1 / max(both[ln].values()))) if both else None


def _best_at(lines: list[dict], market: str, sel: str, line: float) -> dict | None:
    rows = [r for r in lines if r.get("marche") == market and r.get("selection") == sel and r.get("ligne") == line
            and isinstance(r.get("cote"), (int, float))]
    return max(rows, key=lambda r: r["cote"]) if rows else None


def basket_choice(bm: dict, day: str | None = None) -> dict:
    from sportai.services.basket import label

    lines = (bm.get("cotes_cloture") or {}).get("lignes") or []
    pts = bm.get("points") or {}
    proj_t, proj_m = pts.get("total_prevu"), pts.get("ecart_prevu")  # ecart = home - away
    start = BASKET_SEASON_START.get(bm.get("competition") or bm.get("code_ligue") or "")
    early = bool(start and day and (date.fromisoformat(day) - start).days < BASKET_EARLY_DAYS)
    f = BASKET_EARLY_FACTOR if early else 1.0
    cands = []
    mt, mh = _main_line(lines, "total"), _main_line(lines, "handicap")  # handicap lines are the home side's
    if mt is not None and isinstance(proj_t, (int, float)):
        gap = proj_t - mt
        if BASKET_TOTAL_GAP * f <= abs(gap) <= BASKET_TOTAL_GAP * f * BASKET_MAX_FACTOR:
            cands.append(("total", "over" if gap > 0 else "under", mt, abs(gap), abs(gap) / 18.0))
    if mh is not None and isinstance(proj_m, (int, float)):
        gap = proj_m + mh  # home covers when margin + line > 0
        if BASKET_SPREAD_GAP * f <= abs(gap) <= BASKET_SPREAD_GAP * f * BASKET_MAX_FACTOR:
            cands.append(("handicap", "home" if gap > 0 else "away", mh, abs(gap), abs(gap) / 12.0))
    home, away = bm.get("domicile") or "", bm.get("exterieur") or ""
    for market, sel, line, gap, _ in sorted(cands, key=lambda c: -c[4]):
        r = _best_at(lines, market, sel, line)
        if r and ODDS[0] <= r["cote"] <= ODDS[1]:
            what = "le total" if market == "total" else "l'écart"
            return {"pari": label(market, sel, line, home, away), "marche": market, "selection": sel, "ligne": line,
                    "cote_prise": r["cote"], "p_marche": r.get("proba_marche"), "p_modele": r.get("proba"),
                    "ecart_points": round(gap, 1),
                    "raison": f"Ma projection s'écarte de {_pts(gap)} du chiffre des bookmakers sur {what}."}
    if mt is None and mh is None:
        return {"pari": None, "raison": "Je passe : pas de ligne de total ni de handicap."}
    gaps = []
    if mt is not None and isinstance(proj_t, (int, float)):
        gaps.append(f"total {_pts(abs(proj_t - mt))}")
    if mh is not None and isinstance(proj_m, (int, float)):
        gaps.append(f"écart {_pts(abs(proj_m + mh))}")
    need = (f"entre {_pts(BASKET_TOTAL_GAP * f)} et {_pts(BASKET_TOTAL_GAP * f * BASKET_MAX_FACTOR)} sur le total, "
            f"entre {_pts(BASKET_SPREAD_GAP * f)} et {_pts(BASKET_SPREAD_GAP * f * BASKET_MAX_FACTOR)} sur l'écart")
    return {"pari": None, "raison": f"Je passe : écart entre ma projection et les bookmakers : {', '.join(gaps) or 'aucun'} ; il faut {need}"
                                    f"{' (début de saison)' if early else ''}. Au-delà, c'est souvent un absent que je ne vois pas."}


def basket_settle(choice: dict, bm: dict) -> None:
    from sportai.services.basket import settle

    if not choice.get("pari") or choice.get("resultat") in SETTLED or bm.get("statut") != "termine" or not bm.get("score"):
        return
    hp, ap = (int(x) for x in str(bm["score"]).replace(" ", "").split("-"))
    choice["resultat"] = settle(choice["marche"], choice["selection"], choice.get("ligne"), hp, ap)


# ---------------------------------------------------------------- tennis (candidates from scripts/backtest_tennis.py)

def tennis_choice(cands: list[dict], layoff_days: float | None) -> dict | None:
    """The tennis specialist's pick among the priced options of a match (keys of backtest_tennis.candidates), or None.
    Returns the candidate with its own fields "chance" and "ok"."""
    if layoff_days is not None and layoff_days > TENNIS_LAYOFF_DAYS:
        return None
    ok = [c for c in cands if c["key"][0] in TENNIS_MARKETS and abs(c["p"] - c["fair"]) <= TENNIS_MAX_GAP
          and c["p"] * c["best"] >= 1.0 and c["conf"] >= TENNIS_MIN_CHANCE]
    return max(ok, key=lambda c: c["conf"]) if ok else None


def tennis_pass_reason(cands: list[dict], layoff_days: float | None) -> str:
    if layoff_days is not None and layoff_days > TENNIS_LAYOFF_DAYS:
        return f"Je passe : un des joueurs revient après {int(layoff_days)} jours sans match, son Elo n'est plus fiable."
    own = [c for c in cands if c["key"][0] in TENNIS_MARKETS]
    if not own:
        return "Je passe : aucune cote de vainqueur entre 1,50 et 2,30."
    b = max(own, key=lambda c: c["conf"])
    if abs(b["p"] - b["fair"]) > TENNIS_MAX_GAP:
        return f"Je passe : l'Elo ({_pc(b['p'])}) et le marché ({_pc(b['fair'])}) ne sont pas d'accord sur la meilleure option."
    return f"Je passe : la meilleure option n'a que {_pc(b['conf'])} de chance ou pas de valeur à la cote."


# ---------------------------------------------------------------- ratio

def ratio(choices: list[dict]) -> dict:
    """Positions settled won / lost (refunds left out) with the average odds and the break-even."""
    done = [c for c in choices if c.get("pari") and c.get("resultat") in ("gagne", "perdu")
            and not c.get("enregistre_apres_coup_envoi")]
    n, w = len(done), sum(c["resultat"] == "gagne" for c in done)
    if not n:
        return {"positions": 0, "gagnees": 0}
    avg = sum(c["cote_prise"] for c in done) / n
    return {"positions": n, "gagnees": w, "ratio": round(w / n, 3), "cote_moyenne": round(avg, 2),
            "point_mort": round(sum(1 / c["cote_prise"] for c in done) / n, 3)}


def record(prev: dict | None, new: dict, ko: datetime | None, now: datetime, frozen: bool) -> dict:
    """The stored choice of a match: recomputed at every run before kick-off until frozen, then kept as it is.
    A match first seen after kick-off is kept out of the ratio."""
    if prev and (prev.get("fige") or (ko and now >= ko)):
        return prev
    late = bool(ko and now >= ko)
    out = {**new, "decision": now.isoformat(timespec="seconds"), "fige": frozen or late}
    if late:
        out["enregistre_apres_coup_envoi"] = True
    return out
