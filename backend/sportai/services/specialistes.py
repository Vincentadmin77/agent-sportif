"""One specialist agent per sport, run à blanc (owner 2026-10-30: "un agent par sport ... qui s'inspire des meilleurs").

Each specialist keeps its own sport's markets and a decision method taken from the best-known people of its field.
It reads the same match documents as the official passes (no extra API call for football and basketball), gives ONE
choice per match or passes, and is measured apart: never published, never in the official ratio, never in the M's.
The M's settings and the official positions do not move (pause until the M's report of 2026-11-02).

Football (inspired by Benham / Bloom, Sumpter, Tippett): the market is the best forecaster there is, so the chance
used is mostly the market's (MARKET_WEIGHT) and the model only nudges it; the model is built on expected goals and
shots (process) rather than past scores. A position needs value at the best odds AND a model that agrees with the
market (never more than FOOT_MAX_ABOVE above it: when it is, the model usually missed news). Only the three priority
axes (result, goals, both teams score), never "moins de 0,5", no quarter lines. Never back (result bets) a team whose
goals beat its expected goals, net of conceded, by FOOT_LUCK_GAP more than its opponent's over the last 8 games (the
market overrates lucky teams: backing the unlucky side gained +2.3 % against the closing odds on 5 seasons).

Basketball (inspired by Voulgaris): a projected score, then a bet only when the projection is far from the bookmakers'
main number, in POINTS (total >= BASKET_TOTAL_GAP, spread >= BASKET_SPREAD_GAP), at the main line; never more than
twice that far (a huge gap is usually news the projection misses). Injuries (basket_absents): the points lost by an
absent regular are taken off the projection, and no position while a key player is doubtful. The three
basketball axes (winner, handicap, total points; owner 2026-10-30: the axes never change): the winner is taken on the
side the spread gap points to, when its handicap price is out of 1.50-2.30 and the projection has it winning. Early in the season the ratings are young, so the gap needed is larger.

Tennis (inspired by Sackmann): surface Elo is the forecast (overall and surface ratings blended, K shrinking with
experience), but no published Elo beats the market: the chance is a logit blend, 90 % market (margin removed by the
power method) and 10 % Elo (Benter's blend), on the five tennis axes (winner, sets, games handicap, total games,
aces), with an expected value of at least 3 % at the best
odds, and never when a player is back from a long break (Elo is stale after TENNIS_LAYOFF_DAYS without a match).
"""

from __future__ import annotations

import math
import statistics
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
FOOT_LUCK_MATCHES = 8
FOOT_LUCK_GAP = 2.0  # never back the side whose goals beat its xG (net of conceded) by 2+ more than the other's
# Weakened sides (owner 2026-11-27, bilans/2026-11-27-absences-forme): with the official line-ups, a side missing
# 3 of its 4 key players from the XI, or 4+ of its usual starters from the squad: its straight win is never
# backed; its double chance / draw no bet lose ROTATION_PENALTY (owner: adapt to win-or-draw rather than drop it).
# 12 661 league matches 2020-2026, win at odds 1.50-2.30: 44.7 % (2020-23) and
# 46.5 % (2024-26) against ~54 % for full sides. A side with 4+ usual starters out of its XI (on the bench or out of
# the squad) did a little worse (about -2 pts on both periods); rotation alone (3+ on the bench) was not shown to
# hurt but counts at the owner's request. Both only lightly: that side's straight win loses ROTATION_PENALTY.
WEAK_KEY_MISSING = 3
WEAK_KEY_COUNT = 4
WEAK_OUT = 4
ROTATION_BENCH = 3
DOWN_MISSING = 4  # usual starters not in the XI (bench + out of the squad)
# Goals markets (owner 2026-11-27, same 12 661 matches): both sides without 2+ of their regular attackers ->
# fewer goals than the odds expect (over 2.5: -1.9 / -2.4 pts on both periods); a regular goalkeeper missing ->
# more goals (+1.2 / +1.9 pts). One side alone without its attackers: no effect (it also concedes more).
# Applied lightly: GOALS_SHIFT on the chance of "plus de" / "moins de" options (2.5 and 3.5 lines).
# Every market (owner 2026-11-27 « les acteurs doivent s'ajuster sur tous les marchés »), outcomes against the odds,
# relative to normal matches, same direction on 2020-23 and 2024-26:
# - opponent of a very weakened side: its win +1.6 / +2.5 pts, its double chance +2.0 / +3.0 -> +GOALS_SHIFT;
# - regular keeper missing: both teams score +1.0 / +3.5 pts -> +GOALS_SHIFT on "les deux marquent : oui".
# Rotation alone showed nothing stable on any market: only its own result bets lose ROTATION_PENALTY.
GOALS_ATT_MISSING = 2
GOALS_SHIFT = 0.03
GOALS_LINES = ("2_5", "3_5")
ROTATION_PENALTY = 0.03
REGULAR_SHARE = 0.5
# Rotation and calendar (owner 2026-11-27 « tu dois t'adapter au marché », bilans/2026-11-27-absences-forme/
# test-rotation-nombre.md, league matches, favourite at 2.30 or less, against the closing odds):
# - usual starters on the bench by choice: the favourite wins MORE than priced, more so the more are benched
#   (3-4: +3.6 / +4.4 pts; 6+: +8.7 / +6.9): the market over-reacts, so rotation no longer lowers a side;
# - the favourite with its usual XI (0-2 benched) in a league match 2 to 4 days before a Champions League match:
#   its double chance -4.3 / -3.9 pts, its win -1.9 / -2.4 -> its win, double chance and draw no bet lose
#   UCL_PENALTY. Not in European games (not tested there).
UCL_PENALTY = 0.03
UCL_BENCH_MAX = 2
# Owner 2026-11-27 12:48-12:51Z, two groups only. Usual starters missing from the XI (on the bench or out of the
# squad), counting only those who matter (bench_that_matters: key players, or replaced by a clearly weaker player):
# 1 to 3 = nothing; 4 or more = « équipe 2 », treated like a very weakened side (no straight win, double chance /
# draw no bet at -3 pts). No more "à la baisse" penalty.
B_TEAM_BENCH = 4

# --- basketball
BASKET_TOTAL_GAP = 4.0
BASKET_SPREAD_GAP = 3.0
BASKET_MAX_FACTOR = 2.0  # further than twice the gap: usually an absence the ratings do not see (no injury list yet)
BASKET_EARLY_DAYS = 21  # first three weeks of a season: gaps x BASKET_EARLY_FACTOR
BASKET_EARLY_FACTOR = 1.5
BASKET_SEASON_START = {"NBA": date(2026, 10, 20), "WNBA": date(2026, 5, 15)}

# --- tennis
# Vincent's five tennis axes (fixed, owner 2026-10-30 14:31: "les axes ne changent pas")
TENNIS_MARKETS = ("vainqueur", "score_sets", "handicap_jeux", "jeux", "aces")
TENNIS_MARKET_WEIGHT = 0.9  # logit blend; the market alone has the best log-loss, Elo only corrects it a little
TENNIS_MIN_VALUE = 0.03
TENNIS_LAYOFF_DAYS = 60

NOT_KEPT = "Le spécialiste ne le retient pas"
NAMES = {"foot": "V1", "basket": "V2", "tennis": "V3"}  # owner 2026-10-30 14:16: V1 foot, V2 basket, V3 tennis


def _pc(x: float) -> str:
    return f"{round(x * 100)} %"


def _pts(x: float) -> str:
    return f"{x:.1f}".replace(".0", "").replace(".", ",") + " points"


def _n(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


# ---------------------------------------------------------------- football

def xg_luck(history: dict, team: str, before) -> float | None:
    """Benham / Tippett: goals scored minus xG, minus (goals conceded minus xG against), on the team's last
    FOOT_LUCK_MATCHES league games with xG before `before` (a datetime). Positive = luckier than its play."""
    rows = []
    for ms in history.values():
        for x in ms:
            if x.kickoff < before and x.xg_h is not None and x.xg_a is not None and team in (x.home, x.away):
                home = x.home == team
                gf, ga, xf, xa = (x.hg, x.ag, x.xg_h, x.xg_a) if home else (x.ag, x.hg, x.xg_a, x.xg_h)
                rows.append((x.kickoff, (gf - xf) - (ga - xa)))
    rows = sorted(rows)[-FOOT_LUCK_MATCHES:]
    return round(sum(v for _, v in rows), 2) if len(rows) == FOOT_LUCK_MATCHES else None


def _backs(key) -> str | None:
    """Which side a result bet backs: home, away, or None (draw, 12, goal markets)."""
    if key.market in ("1x2", "dnb"):
        return key.selection if key.selection in ("home", "away") else None
    if key.market == "double_chance":
        return {"1X": "home", "X2": "away"}.get(key.selection)
    return None


def key_players(players: list[dict], team_id: int | None) -> list[str]:
    """The side's WEAK_KEY_COUNT key players (API-Football short names), from /players of this season, every
    competition: regular starters (started REGULAR_SHARE of the team's matches) ranked by share of goals + assists,
    then by average rating."""
    starts, apps, ga, rating, names = {}, {}, {}, {}, {}
    for item in players or []:
        pid = (item.get("player") or {}).get("id")
        if pid is None:
            continue
        names[pid] = (item.get("player") or {}).get("name") or "?"
        for st in item.get("statistics") or []:
            if team_id is not None and (st.get("team") or {}).get("id") not in (None, team_id):
                continue
            g, gl = st.get("games") or {}, st.get("goals") or {}
            n = g.get("appearences") or 0
            starts[pid] = starts.get(pid, 0) + (g.get("lineups") or 0)
            apps[pid] = apps.get(pid, 0) + n
            ga[pid] = ga.get(pid, 0) + (gl.get("total") or 0) + (gl.get("assists") or 0)
            try:
                r = float(g.get("rating")) if g.get("rating") else None
            except (TypeError, ValueError):
                r = None
            if r and n:
                a, w = rating.get(pid, (0.0, 0))
                rating[pid] = (a + r * n, w + n)
    played = max(apps.values(), default=0)
    if not played:
        return []
    regs = [pid for pid, n in starts.items() if n / played >= REGULAR_SHARE]
    total = sum(ga.values()) or 1
    score = lambda pid: ga.get(pid, 0) / total * 10 + (rating[pid][0] / rating[pid][1] if pid in rating else 6.5)
    return [names[pid] for pid in sorted(regs, key=lambda pid: -score(pid))[:WEAK_KEY_COUNT]]


POSTES = {"Goalkeeper": "gardien", "Defender": "défense", "Midfielder": "milieu", "Attacker": "attaque"}


def regular_roles(players: list[dict], team_id: int | None) -> dict:
    """{"att": regular starting attackers, "gk": regular starting goalkeepers} (API-Football short names),
    plus "postes": {name: "gardien" | "défense" | "milieu" | "attaque"} of the whole squad (display only)."""
    starts, apps, pos, names, mins, rat = {}, {}, {}, {}, {}, {}
    for item in players or []:
        pid = (item.get("player") or {}).get("id")
        if pid is None:
            continue
        names[pid] = (item.get("player") or {}).get("name") or "?"
        for st in item.get("statistics") or []:
            if team_id is not None and (st.get("team") or {}).get("id") not in (None, team_id):
                continue
            g = st.get("games") or {}
            starts[pid] = starts.get(pid, 0) + (g.get("lineups") or 0)
            apps[pid] = apps.get(pid, 0) + (g.get("appearences") or 0)
            mins[pid] = mins.get(pid, 0) + (g.get("minutes") or 0)
            if g.get("position") and pid not in pos:
                pos[pid] = g["position"]
            try:
                r, n = float(g.get("rating")), g.get("appearences") or 0
            except (TypeError, ValueError):
                r, n = None, 0
            if r and n:
                a_, w_ = rat.get(pid, (0.0, 0))
                rat[pid] = (a_ + r * n, w_ + n)
    played = max(apps.values(), default=0)
    regs = [pid for pid, n in starts.items() if played and n / played >= REGULAR_SHARE]
    return {"att": [names[p] for p in regs if pos.get(p) == "Attacker"],
            "gk": [names[p] for p in regs if pos.get(p) == "Goalkeeper"],
            "postes": {names[p]: POSTES[v] for p, v in pos.items() if v in POSTES},
            # display and bench count: line, season rating (None under 3 games) and minutes of every squad player
            "fiche": {names[p]: {"poste": POSTES.get(pos.get(p)), "min": mins.get(p, 0),
                                 "note": round(rat[p][0] / rat[p][1], 2) if p in rat and rat[p][1] >= 3 else None}
                      for p in names}}


BENCH_GAP = 0.4  # a benched starter counts when his replacement is rated this much lower (red on the pitch)


def bench_that_matters(banc: list, xi: set, keys: list, fiche: dict | None) -> list:
    """Owner 2026-11-27 12:49Z: of the usual starters left on the bench, only those who matter count towards the
    rotation thresholds: a key player, or one whose replacement in the XI (a newcomer of the same line, best rated
    first) is clearly weaker (BENCH_GAP or no rating). One replaced by a player of his level does not count.
    Without the squad data, every benched usual starter counts."""
    if not fiche:
        return list(banc)
    usual = set(sorted(fiche, key=lambda n: -(fiche[n].get("min") or 0))[:11])
    note = lambda n: (fiche.get(n) or {}).get("note")
    newcomers = sorted((n for n in xi if n not in usual), key=lambda n: -(note(n) or 0))
    out = []
    for b in sorted(banc, key=lambda n: -(note(n) or 0)):
        line = (fiche.get(b) or {}).get("poste")
        rep = next((n for n in newcomers if (fiche.get(n) or {}).get("poste") == line), None) or \
            (newcomers[0] if newcomers else None)
        if rep:
            newcomers.remove(rep)
        weaker = note(b) is not None and (rep is None or note(rep) is None or note(b) - note(rep) >= BENCH_GAP)
        if b in (keys or []) or weaker:
            out.append(b)
    return out


def weakened(m: dict, keys: dict | None = None, roles: dict | None = None) -> dict | None:
    """Both sides' state once the official line-ups are on the page (compositions), else None.
    keys: {"domicile": [key player names], "exterieur": [...]} (key_players), when known."""
    comp = m.get("compositions") or {}
    if not comp or "attente" in comp:
        return None
    out = {}
    for side in ("domicile", "exterieur"):
        b = comp.get(side) or {}
        xi = set(b.get("titulaires") or [])
        if len(xi) < 11:
            return None
        hors = list(b.get("absents_notables") or [])
        banc = list(b.get("remplacants_notables") or [])
        cles = [k for k in ((keys or {}).get(side) or []) if k not in xi]
        r = (roles or {}).get(side) or {}
        counted = bench_that_matters(banc + hors, xi, (keys or {}).get(side) or [], r.get("fiche"))
        level = ("tres_diminuee" if len(cles) >= WEAK_KEY_MISSING or len(hors) >= WEAK_OUT or len(counted) >= B_TEAM_BENCH
                 else "a_la_baisse" if len(banc) >= ROTATION_BENCH or len(hors) + len(banc) >= DOWN_MISSING else None)
        postes = r.get("postes") or {}
        out[side] = {"postes": {n: postes[n] for n in dict.fromkeys(cles + hors + banc) if n in postes},
                     "niveau": level, "cles_absents": cles, "hors_groupe": hors, "banc": banc, "banc_compte": counted,
                     "attaquants_absents": [a for a in r.get("att") or [] if a not in xi],
                     # a keeper is missing only when none of the regular ones starts (two can share the post)
                     "gardien_absent": [] if any(g in xi for g in r.get("gk") or []) else list(r.get("gk") or [])}
    return out


def goals_shift(weak: dict | None) -> tuple[float, list[str]]:
    """Chance added to "plus de" options (taken off "moins de") from the line-ups, with its plain reasons."""
    if not weak:
        return 0.0, []
    shift, why = 0.0, []
    if all(len((weak.get(s_) or {}).get("attaquants_absents") or []) >= GOALS_ATT_MISSING
           for s_ in ("domicile", "exterieur")):
        shift -= GOALS_SHIFT
        why.append("les deux équipes jouent sans plusieurs attaquants habituels : je m'attends à moins de buts.")
    gk = [g for s_ in ("domicile", "exterieur") for g in (weak.get(s_) or {}).get("gardien_absent") or []]
    if gk:
        shift += GOALS_SHIFT
        why.append(f"gardien habituel absent ({', '.join(gk)}) : je m'attends à un peu plus de buts, des deux côtés.")
    return shift, why


def weakened_phrase(team: str, w: dict) -> str | None:
    counted = w.get("banc_compte", w.get("banc")) or []
    if w.get("niveau") == "tres_diminuee" and len(counted) >= B_TEAM_BENCH:
        return (f"{team} aligne une équipe 2 : {len(counted)} titulaires absents ou laissés au banc "
                f"({', '.join(counted[:4])}{'…' if len(counted) > 4 else ''}) : pas de victoire sèche, au mieux victoire ou nul.")
    if w.get("niveau") == "tres_diminuee":
        who = w["cles_absents"] if len(w["cles_absents"]) >= WEAK_KEY_MISSING else w["hors_groupe"]
        return (f"{team} est très diminuée (sans {', '.join(who[:4])}) : pas de victoire sèche, au mieux victoire "
                f"ou nul, et je vois son adversaire un peu à la hausse.")
    if w.get("niveau") == "a_la_baisse":
        bits = []
        if w["hors_groupe"]:
            bits.append(f"sans {', '.join(w['hors_groupe'][:4])}")
        if w["banc"]:
            bits.append(f"avec {', '.join(w['banc'][:4])} sur le banc (choix du coach)")
        return (f"{team} joue {' et '.join(bits)} : quelques joueurs au repos, la cote le compte déjà, "
                f"je ne la baisse pas.")
    return None


def ucl_side(m: dict, weak: dict | None, cal: dict | None) -> str | None:
    """The favourite ("domicile" / "exterieur") that plays its usual XI 2 to 4 days before a Champions League match
    (cal[side] = days until it), or None. Needs the official line-ups; league games only."""
    if not weak or not cal:
        return None
    p = m.get("proba_1x2") or {}
    if not isinstance(p.get("domicile"), (int, float)) or not isinstance(p.get("exterieur"), (int, float)):
        return None
    fav = "domicile" if p["domicile"] >= p["exterieur"] else "exterieur"
    if cal.get(fav) and len((weak.get(fav) or {}).get("banc") or []) <= UCL_BENCH_MAX:
        return fav
    return None


def foot_options(m: dict, luck: tuple | None = None, weak: dict | None = None, cal: dict | None = None) -> list[dict]:
    from sportai.engine.football.markets import is_quarter
    from sportai.services.revue import _keys, famille

    keys = _keys(m.get("domicile") or "", m.get("exterieur") or "")
    shift = goals_shift(weak)[0]
    keeper_out = any((weak or {}).get(s_, {}).get("gardien_absent") for s_ in ("domicile", "exterieur"))
    before_ucl = ucl_side(m, weak, cal)
    b_team = any(((weak or {}).get(s_) or {}).get("niveau") == "tres_diminuee" for s_ in ("domicile", "exterieur"))
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
        side, lucky = _backs(key), False
        w = ((weak or {}).get({"home": "domicile", "away": "exterieur"}[side]) or {}) if side else {}
        fam = famille(key.market, key.selection, key.line)
        # Owner 27/11 12:53Z (Arsenal-Dortmund 1-1): an équipe 2 on either side means fewer goals, never more:
        # no over-goals and no « les deux marquent : oui » on that match (pass rather than lose).
        if b_team and (fam in ("plus_" + x for x in GOALS_LINES) or fam == "btts_oui"):
            continue
        # Owner 27/11: full side = straight win; weakened side = adapt to win-or-draw (double chance, draw no bet)
        # rather than drop it, and pass when even that is not good enough.
        if w.get("niveau") == "tres_diminuee":
            if fam == "victoire":
                continue  # its straight win is never backed, not even as a fallback (the Top 5 reads every choice)
            chance -= ROTATION_PENALTY  # its double chance / draw no bet: -2 / -3 pts in the backtest
        if side and before_ucl == {"home": "domicile", "away": "exterieur"}[side] and fam in (
                "victoire", "double_chance", "rembourse_si_nul"):
            chance -= UCL_PENALTY
        if shift and fam.startswith(("plus_", "moins_")) and fam.endswith(GOALS_LINES):
            chance += shift if fam.startswith("plus_") else -shift
        if side:  # the balance: backing the opponent of a very weakened side
            other = (weak or {}).get({"home": "exterieur", "away": "domicile"}[side]) or {}
            if other.get("niveau") == "tres_diminuee":
                chance += GOALS_SHIFT
        if fam == "btts_oui" and keeper_out:
            chance += GOALS_SHIFT
        if side and luck and None not in luck:
            mine, theirs = (luck[0], luck[1]) if side == "home" else (luck[1], luck[0])
            lucky = mine - theirs >= FOOT_LUCK_GAP
        out.append({"pari": r["pari"], "marche": r.get("marche"), "cote_prise": cote, "p_marche": round(pm, 4),
                    "p_modele": round(p, 4), "chance": round(chance, 4), "chanceux": lucky,
                    "ok": chance >= FOOT_MIN_CHANCE and chance * cote >= FOOT_MIN_VALUE and 0 <= p - pm <= FOOT_MAX_ABOVE
                    and not lucky})
    return sorted(out, key=lambda o: -o["chance"])


def foot_choice(m: dict, luck: tuple | None = None, weak: dict | None = None, cal: dict | None = None) -> dict:
    """luck = (home, away) from xg_luck, or None when unknown (the xG filter is then skipped).
    weak = weakened(m, ...) once the line-ups are out, else None. cal = {side: days until its next Champions League
    match, when 2 to 4 days away} for a league game, else None."""
    c = _foot_choice(m, luck, weak, cal)
    if weak:
        c["affaiblissement"] = weak
        notes = [x for x in (weakened_phrase(m.get(s_) or s_, weak[s_]) for s_ in ("domicile", "exterieur")) if x]
        notes += [w_[0].upper() + w_[1:] for w_ in goals_shift(weak)[1]]
        ucl = ucl_side(m, weak, cal)
        if ucl:
            c["calendrier"] = {"cote": ucl, "ldc_dans_jours": cal[ucl]}
            notes.append(f"{m.get(ucl) or ucl} joue la Ligue des champions dans {cal[ucl]} jours avec son équipe type : "
                         f"avant la Ligue des champions, je la vois un peu à la baisse.")
        if notes and c.get("raison"):
            c["raison"] += " " + " ".join(notes)
    return c


def _foot_choice(m: dict, luck: tuple | None, weak: dict | None, cal: dict | None = None) -> dict:
    opts = foot_options(m, luck, weak, cal)
    ok = [o for o in opts if o["ok"]]
    if ok:
        c = {k: v for k, v in ok[0].items() if k not in ("ok", "chanceux")}
        if luck and None not in luck:
            c["chance_xg"] = {"domicile": luck[0], "exterieur": luck[1]}
        c["raison"] = (f"Le marché la voit à {_pc(c['p_marche'])}, le modèle (buts attendus, tirs) à {_pc(c['p_modele'])} : "
                       f"ils sont d'accord et la cote de {_n(c['cote_prise'])} paie plus que la chance.")
        return c
    if not opts:
        return {"pari": None, "raison": "Je passe : aucune position de résultat, de buts ou « les deux marquent » entre 1,50 et 2,30."}
    b = opts[0]
    if b["chanceux"]:
        why = ("l'équipe choisie marque plus que ses buts attendus ces 8 derniers matchs : elle a eu de la chance, "
               "le marché la surestime")
    elif b["p_modele"] - b["p_marche"] > FOOT_MAX_ABOVE:
        why = f"le modèle ({_pc(b['p_modele'])}) est trop au-dessus du marché ({_pc(b['p_marche'])}), il a sans doute raté une info"
    elif b["p_modele"] < b["p_marche"]:
        why = f"le modèle ({_pc(b['p_modele'])}) y croit moins que le marché ({_pc(b['p_marche'])}) : pas de valeur"
    else:
        why = f"chance retenue {_pc(b['chance'])}, il faut {_pc(FOOT_MIN_CHANCE)} et une cote qui paie plus que la chance"
    return {**{k: b[k] for k in ("pari", "marche", "cote_prise", "p_marche", "p_modele", "chance")}, "retenu": False,
            "raison": f"{NOT_KEPT} : {why}."}


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


def basket_choice(bm: dict, day: str | None = None, adj: dict | None = None) -> dict:
    """adj: injuries of the game (basket_absents.game_adjustment), added to the projection; None = not known."""
    from sportai.services.basket import label

    lines = (bm.get("cotes_cloture") or {}).get("lignes") or []
    pts = bm.get("points") or {}
    proj_t, proj_m = pts.get("total_prevu"), pts.get("ecart_prevu")  # ecart = home - away
    if adj:
        doubt = (adj.get("domicile") or {}).get("incertains", []) + (adj.get("exterieur") or {}).get("incertains", [])
        if doubt:
            out = basket_choice(bm, day, {**adj, "domicile": {**adj["domicile"], "incertains": []},
                                          "exterieur": {**adj["exterieur"], "incertains": []}})
            if not out.get("pari"):
                return out
            return {**out, "retenu": False,
                    "raison": f"{NOT_KEPT} : joueur clé incertain ({', '.join(doubt)}), j'attends de savoir s'il joue."}
        if isinstance(proj_m, (int, float)):
            proj_m += adj.get("marge", 0.0)
        if isinstance(proj_t, (int, float)):
            proj_t += adj.get("total", 0.0)
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
        if market == "handicap" and not (r and ODDS[0] <= r["cote"] <= ODDS[1]):
            # the winner axis: the same side to win, when its handicap price is out of range
            w = _best_at(lines, "winner", sel, None)
            if w and ODDS[0] <= w["cote"] <= ODDS[1] and ((sel == "home") == (proj_m > 0)):
                market, r, line = "winner", w, None
        if r and ODDS[0] <= r["cote"] <= ODDS[1]:
            what = "le total" if market == "total" else "l'écart"
            return {"pari": label(market, sel, line, home, away), "marche": market, "selection": sel, "ligne": line,
                    "cote_prise": r["cote"], "p_marche": r.get("proba_marche"), "p_modele": r.get("proba"),
                    "ecart_points": round(gap, 1),
                    "raison": f"Ma projection s'écarte de {_pts(gap)} du chiffre des bookmakers sur {what}."}
    if mt is None and mh is None:
        return {"pari": None, "raison": "Je passe : pas de ligne de total ni de handicap."}
    fallback = _basket_fallback(lines, mt, mh, proj_t, proj_m, home, away)
    gaps = []
    if mt is not None and isinstance(proj_t, (int, float)):
        gaps.append(f"total {_pts(abs(proj_t - mt))}")
    if mh is not None and isinstance(proj_m, (int, float)):
        gaps.append(f"écart {_pts(abs(proj_m + mh))}")
    need = (f"entre {_pts(BASKET_TOTAL_GAP * f)} et {_pts(BASKET_TOTAL_GAP * f * BASKET_MAX_FACTOR)} sur le total, "
            f"entre {_pts(BASKET_SPREAD_GAP * f)} et {_pts(BASKET_SPREAD_GAP * f * BASKET_MAX_FACTOR)} sur l'écart")
    why = (f"écart entre ma projection et les bookmakers : {', '.join(gaps) or 'aucun'} ; il faut {need}"
           f"{' (début de saison)' if early else ''}. Au-delà, c'est souvent un absent que je ne vois pas.")
    if fallback:
        return {**fallback, "retenu": False, "raison": f"{NOT_KEPT} : {why}"}
    return {"pari": None, "raison": f"Je passe : {why}"}


def _basket_fallback(lines, mt, mh, proj_t, proj_m, home, away) -> dict | None:
    """The side V2 leans to when his gap rules do not keep the game: the larger gap (in standard deviations) on the
    main total or handicap, else the projected winner, at odds 1.50-2.30."""
    from sportai.services.basket import label

    cands = []
    if mt is not None and isinstance(proj_t, (int, float)):
        gap = proj_t - mt
        cands.append(("total", "over" if gap > 0 else "under", mt, abs(gap), abs(gap) / 18.0))
    if mh is not None and isinstance(proj_m, (int, float)):
        gap = proj_m + mh
        cands.append(("handicap", "home" if gap > 0 else "away", mh, abs(gap), abs(gap) / 12.0))
    if isinstance(proj_m, (int, float)):
        cands.append(("winner", "home" if proj_m > 0 else "away", None, abs(proj_m), -1))
    for market, sel, line, gap, _ in sorted(cands, key=lambda c: -c[4]):
        r = _best_at(lines, market, sel, line)
        if r and ODDS[0] <= r["cote"] <= ODDS[1]:
            return {"pari": label(market, sel, line, home, away), "marche": market, "selection": sel, "ligne": line,
                    "cote_prise": r["cote"], "p_marche": r.get("proba_marche"), "p_modele": r.get("proba"),
                    "ecart_points": round(gap, 1)}
    return None


def basket_settle(choice: dict, bm: dict) -> None:
    from sportai.services.basket import settle

    if not choice.get("pari") or choice.get("resultat") in SETTLED or bm.get("statut") != "termine" or not bm.get("score"):
        return
    hp, ap = (int(x) for x in str(bm["score"]).replace(" ", "").split("-"))
    choice["resultat"] = settle(choice["marche"], choice["selection"], choice.get("ligne"), hp, ap)


# ---------------------------------------------------------------- tennis (candidates from scripts/backtest_tennis.py)

def power_fair(odds_a: float, odds_b: float) -> float:
    """Chance of side A with the margin removed by the power method (k with (1/a)^k + (1/b)^k = 1): it corrects the
    favourite-longshot bias better than the proportional method (Shin, Buchdahl)."""
    qa, qb, lo, hi = 1 / odds_a, 1 / odds_b, 0.5, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if qa ** k + qb ** k > 1 else (lo, k)
    return qa ** k / (qa ** k + qb ** k)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def tennis_choice(cands: list[dict], layoff_days: float | None, mk: dict | None = None) -> dict | None:
    """The tennis specialist's pick among the priced options of a match (keys of backtest_tennis.candidates), or None:
    on the five tennis axes, chance = logit blend TENNIS_MARKET_WEIGHT market (power method on the median odds) + the rest Elo,
    expected value >= TENNIS_MIN_VALUE at the best odds. The candidate gets "fair" and "conf" replaced by these."""
    if layoff_days is not None and layoff_days > TENNIS_LAYOFF_DAYS:
        return None
    h, a = (mk or {}).get(("vainqueur", "Home")), (mk or {}).get(("vainqueur", "Away"))
    fh = power_fair(statistics.median(h), statistics.median(a)) if h and a else None
    out = []
    for c in cands:
        if c["key"][0] not in TENNIS_MARKETS:
            continue
        f = c["fair"] if c["key"][0] != "vainqueur" or fh is None else (fh if c["key"][1] == "Home" else 1 - fh)
        chance = 1 / (1 + math.exp(-(TENNIS_MARKET_WEIGHT * _logit(f) + (1 - TENNIS_MARKET_WEIGHT) * _logit(c["p"]))))
        if chance * c["best"] - 1 >= TENNIS_MIN_VALUE:
            out.append({**c, "fair": f, "conf": chance})
    return max(out, key=lambda c: c["conf"]) if out else None


def tennis_fallback(cands: list[dict], mk: dict | None = None) -> dict | None:
    """V3's leaning when no option passes its value rule: the most likely option on the five axes (same blend)."""
    h, a = (mk or {}).get(("vainqueur", "Home")), (mk or {}).get(("vainqueur", "Away"))
    fh = power_fair(statistics.median(h), statistics.median(a)) if h and a else None
    out = []
    for c in cands:
        if c["key"][0] not in TENNIS_MARKETS:
            continue
        f = c["fair"] if c["key"][0] != "vainqueur" or fh is None else (fh if c["key"][1] == "Home" else 1 - fh)
        chance = 1 / (1 + math.exp(-(TENNIS_MARKET_WEIGHT * _logit(f) + (1 - TENNIS_MARKET_WEIGHT) * _logit(c["p"]))))
        out.append({**c, "fair": f, "conf": chance})
    return max(out, key=lambda c: c["conf"]) if out else None


def tennis_pass_reason(cands: list[dict], layoff_days: float | None) -> str:
    if layoff_days is not None and layoff_days > TENNIS_LAYOFF_DAYS:
        return f"Je passe : un des joueurs revient après {int(layoff_days)} jours sans match, son Elo n'est plus fiable."
    if not [c for c in cands if c["key"][0] in TENNIS_MARKETS]:
        return "Je passe : aucune cote entre 1,50 et 2,30 sur les axes du tennis."
    return "Je passe : l'Elo ne corrige pas assez le marché pour que la cote rapporte 3 % de plus que la chance."


# ---------------------------------------------------------------- ratio

def ratio(choices: list[dict]) -> dict:
    """Positions settled won / lost (refunds left out) with the average odds and the break-even."""
    done = [c for c in choices if c.get("pari") and c.get("resultat") in ("gagne", "perdu")
            and not c.get("enregistre_apres_coup_envoi") and c.get("retenu", True)]
    n, w = len(done), sum(c["resultat"] == "gagne" for c in done)
    if not n:
        return {"positions": 0, "gagnees": 0}
    avg = sum(c["cote_prise"] for c in done) / n
    return {"positions": n, "gagnees": w, "ratio": round(w / n, 3), "cote_moyenne": round(avg, 2),
            "point_mort": round(sum(1 / c["cote_prise"] for c in done) / n, 3)}


def ratio_tous(choices: list[dict]) -> dict:
    """Every choice, kept or not (for comparing with the model and the M), late ones still left out."""
    return ratio([{**c, "retenu": True} for c in choices])


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
