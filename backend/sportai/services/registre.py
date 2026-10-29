"""The positions registry and the mathematical report ("agent mathématicien", owner 2026-10-28).

Every position, once frozen (45 min before kick-off), gets a record written once: when it was seen frozen, the odds
taken, the model's, the market's and the retained chance, the rules in force and the code version. After kick-off the
closing odds of the same bet (the match document's last pre-match odds) and the result are added, never touching the
frozen fields; a later change of the day's position on that match is logged under "modifications", never applied.
The rebuilt history (pronostic docs with "recalcule") is imported apart, flagged "recalcule", without decision time.

The report measures, separately for the real and the rebuilt records: hit rate with its 95 % interval, flat-stake
return, expected value at the market's chance, Brier score of each chance, closing line value, worst run and deepest
drawdown, per confidence level, market and competition. The money figures are internal: never on the site.

Pure functions over the page's documents.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime

from sportai.services import pronostic as P

SETTLED = ("gagne", "perdu", "rembourse")
FROZEN = ("jour", "sport", "agent", "decision", "passage", "match_id", "match", "competition", "coup_envoi", "marche",
          "famille", "pari", "cote_prise", "bookmaker", "p_modele", "p_marche", "p_retenue", "niveau", "valeur",
          "version_regles", "version_code")
WATCHED = {"pari": "pari", "cote": "cote_prise", "confiance": "p_retenue"}  # position field -> frozen field


def rules() -> dict:
    """The selection rules in force, as the registry stamps them on each record."""
    return {"plage_cotes": list(P.PICK_ODDS), "ecart_max": P.PICK_MAX_GAP, "chance_min": P.PICK_MIN_CHANCE,
            "chance_min_mi_temps": P.HALF_MIN_CHANCE, "poids_modele": P.PICK_MODEL_WEIGHT, "seuil_eleve": P.PICK_HIGH}


def rules_version(r: dict | None = None) -> str:
    return hashlib.sha1(json.dumps(r or rules(), sort_keys=True).encode()).hexdigest()[:10]


def record_id(day: str, match_id: str, pari: str) -> str:
    return f"{day}|{match_id}|{pari}"


def _aware(s: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else None


def closing_odds(match: dict, pari: str) -> tuple[float | None, float | None]:
    """The match document's odds and market chance for that bet, as left by the last pre-match pass."""
    rows = ((match.get("tous_marches") or {}).get("analyses") or []) + ((match.get("buts") or {}).get("marches") or [])
    for r in rows:
        if r.get("pari") == pari and isinstance(r.get("cote"), (int, float)):
            return r["cote"], r.get("proba_marche")
    return None, None


def _priced_after(m: dict, f: dict, ko: datetime) -> bool:
    """Whether a pre-match pass priced the match after the record's decision (else its odds are the decision's own)."""
    dec = _aware(f.get("decision"))
    seen = [_aware(t) for k, t in (m.get("passages") or {}).items() if k != "direct"]
    return bool(dec) and any(t and dec < t <= ko for t in seen)


def update(registre: dict, pron: dict, matchs: dict[str, dict], now: datetime, code_version: str,
           sport: str = "foot") -> dict:
    """The day's registry document after one pass: new frozen positions recorded, closing odds and results added."""
    day = pron.get("jour")
    reg = {"jour": day, "sport": sport, "fiches": {k: dict(v) for k, v in (registre.get("fiches") or {}).items()},
           "cibles": dict(registre.get("cibles") or {}),
           "avis": {k: dict(v) for k, v in (registre.get("avis") or {}).items()}}
    if pron.get("recalcule"):
        return reg  # rebuilt history: see import_rebuilt
    r = rules()
    for p in (pron.get("paris_retenus") or {}).values():
        if not p.get("pari") or not p.get("fige"):
            continue
        rid = record_id(day, p.get("match_id"), p["pari"])
        same_match = [f for f in reg["fiches"].values()
                      if f["match_id"] == p.get("match_id") and not f.get("live_a_blanc") and not f.get("choix_m")
                      and not f.get("remplacee")]
        if rid not in reg["fiches"] and same_match:  # the match already has a frozen position: log, never replace
            first = same_match[0]
            log = {"heure": now.isoformat(), "champ": "pari", "avant": first["pari"], "apres": p["pari"]}
            if log["apres"] not in [m["apres"] for m in first.setdefault("modifications", [])]:
                first["modifications"].append(log)
            continue
        if rid not in reg["fiches"]:
            reg["fiches"][rid] = {
                "id": rid, "jour": day, "sport": sport, "agent": "analyse foot (position retenue)",
                "decision": now.isoformat(), "passage": pron.get("passage"), "match_id": p.get("match_id"),
                "match": p.get("match"), "competition": p.get("competition"), "coup_envoi": p.get("coup_envoi"),
                "marche": p.get("marche"), "famille": p.get("famille"), "pari": p["pari"], "cote_prise": p.get("cote"),
                "bookmaker": p.get("bookmaker"), "p_modele": p.get("proba"), "p_marche": p.get("proba_marche"),
                "p_retenue": p.get("confiance"), "niveau": p.get("niveau"), "valeur": p.get("valeur"),
                "version_regles": rules_version(r), "version_code": code_version, "recalcule": False}
            ko = _aware(p.get("coup_envoi"))
            if ko and now >= ko:  # first seen after kick-off: not a decision taken in time, kept apart
                reg["fiches"][rid]["enregistre_apres_coup_envoi"] = True
        f = reg["fiches"][rid]
        if p.get("non_retenu"):  # a choice on every match, but one the model does not retain: out of its ratio
            f["non_retenu"] = True
        for src, dst in WATCHED.items():
            if src != "pari" and p.get(src) is not None and p.get(src) != f.get(dst):
                log = {"heure": now.isoformat(), "champ": dst, "avant": f.get(dst), "apres": p.get(src)}
                mods = f.setdefault("modifications", [])
                if not mods or (mods[-1]["champ"], mods[-1]["apres"]) != (dst, log["apres"]):
                    mods.append(log)
        if p.get("resultat") in SETTLED:
            f["resultat"] = p["resultat"]
    for f in reg["fiches"].values():
        if f.get("live_a_blanc") or f.get("choix_m"):
            continue
        m = matchs.get(f["match_id"]) or {}
        ko = _aware(f.get("coup_envoi"))
        if "cote_cloture" not in f and ko and now >= ko and m:
            cote, pm = closing_odds(m, f["pari"])
            if cote and not _priced_after(m, f, ko):  # odds no newer than the decision: no closing line to compare
                f["cote_cloture"] = None
            elif cote:
                f["cote_cloture"], f["p_marche_cloture"] = cote, pm
                f["clv"] = round(f["cote_prise"] / cote - 1, 4) if f.get("cote_prise") else None
        if f.get("resultat") in SETTLED and not f.get("live_a_blanc"):
            f["gain_unite"] = _gain(f)
    reg = m_records(live_records(reg, matchs, code_version), matchs, now, code_version)
    return avis_records(reg, pron, matchs, now)


def import_rebuilt(pron: dict, sport: str = "foot") -> dict:
    """A rebuilt day (positions chosen afterwards with later rules): kept apart, flagged, no decision time."""
    from sportai.services.bilan import day_positions

    day = pron.get("jour")
    fiches = {}
    for p in day_positions(pron):
        rid = record_id(day, p.get("match_id"), p.get("pari"))
        fiches[rid] = {"id": rid, "jour": day, "sport": sport, "agent": p.get("origine"), "decision": None,
                       "match_id": p.get("match_id"), "match": p.get("match"), "competition": p.get("competition"),
                       "coup_envoi": p.get("coup_envoi"), "marche": p.get("marche"), "pari": p.get("pari"),
                       "cote_prise": p.get("cote"), "p_modele": p.get("proba"), "p_marche": p.get("proba_marche"),
                       "p_retenue": p.get("confiance"), "niveau": p.get("niveau"), "recalcule": True,
                       "resultat": p.get("resultat")}
        if p.get("resultat") in SETTLED:
            fiches[rid]["gain_unite"] = _gain(fiches[rid])
    return {"jour": day, "sport": sport, "recalcule": True, "fiches": fiches}


def _gain(f: dict) -> float:
    return {"gagne": round(f["cote_prise"] - 1, 4), "perdu": -1.0}.get(f["resultat"], 0.0)


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    p = w / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(c - h, 4), round(c + h, 4)


def _brier(rows: list[dict], key: str) -> float | None:
    rows = [r for r in rows if isinstance(r.get(key), (int, float))]
    return round(sum((r[key] - (r["resultat"] == "gagne")) ** 2 for r in rows) / len(rows), 4) if rows else None


def measures(rows: list[dict]) -> dict:
    """The indicators of a set of records (the settled ones; refunds count in the return, not in the hit rate)."""
    s = [r for r in rows if r.get("resultat") in ("gagne", "perdu")]
    staked = [r for r in rows if r.get("resultat") in SETTLED]
    n, w = len(s), sum(r["resultat"] == "gagne" for r in s)
    out = {"positions": len(rows), "reglees": n, "remboursees": len(staked) - n, "validees": w}
    if not n:
        return out
    out["reussite"] = round(w / n, 4)
    out["intervalle_95"] = wilson(w, n)
    out["cote_moyenne"] = round(sum(r["cote_prise"] for r in s) / n, 3)
    pm = [r for r in s if isinstance(r.get("p_marche"), (int, float))]
    if pm:
        out["chance_marche_moyenne"] = round(sum(r["p_marche"] for r in pm) / len(pm), 4)
        out["valeur_esperee_marche"] = round(sum(r["p_marche"] * r["cote_prise"] - 1 for r in pm) / len(pm), 4)
    out["rendement_mise_egale"] = round(sum(r["gain_unite"] for r in staked) / len(staked), 4)
    out["brier"] = {k: _brier(s, k) for k in ("p_modele", "p_marche", "p_retenue")}
    clv = [r["clv"] for r in s if isinstance(r.get("clv"), (int, float))]
    if clv:
        out["clv_moyenne"], out["clv_positive"] = round(sum(clv) / len(clv), 4), round(sum(c > 0 for c in clv) / len(clv), 4)
    cum = peak = dd = 0.0
    run = worst = 0
    for r in sorted(staked, key=lambda r: str(r.get("coup_envoi"))):
        cum += r["gain_unite"]
        peak, dd = max(peak, cum), min(dd, cum - peak)
        run = run + 1 if r["resultat"] == "perdu" else 0 if r["resultat"] == "gagne" else run
        worst = max(worst, run)
    out["creux_max_unites"], out["pire_serie"] = round(dd, 2), worst
    return out


def report(registres: list[dict], min_group: int = 5) -> dict:
    """Real records and rebuilt ones measured apart, overall and per confidence level, market and competition."""
    fiches = [f for reg in registres for f in (reg.get("fiches") or {}).values()]
    out = {}
    official = [f for f in fiches if not (f.get("recalcule") or f.get("live_a_blanc") or f.get("choix_m")
                                          or f.get("remplacee"))]
    for label, rows in (("reel", [f for f in official if not f.get("non_retenu")]),
                        ("non_retenu", [f for f in official if f.get("non_retenu")]),
                        ("le_m_a_blanc", [f for f in fiches if f.get("choix_m") and f.get("pari")
                                          and not f.get("enregistre_apres_coup_envoi")]),
                        ("le_m_nba_55", [{**f["choix_55"], "sport": "basket", "competition": f.get("competition")}
                                         for f in fiches if f.get("choix_55")
                                         and not f.get("enregistre_apres_coup_envoi")]),
                        ("recalcule", [f for f in fiches if f.get("recalcule")]),
                        ("direct_a_blanc", [f for f in fiches if f.get("live_a_blanc")])):
        part = {"global": measures(rows)}
        for key in ("niveau", "marche", "competition", "sport"):
            groups = defaultdict(list)
            for f in rows:
                groups[f.get(key) or "?"].append(f)
            part[key] = {g: measures(v) for g, v in sorted(groups.items(), key=lambda x: -len(x[1]))
                         if len([r for r in v if r.get("resultat") in ("gagne", "perdu")]) >= min_group}
        out[label] = part
    return out


# --- Live, à blanc (owner 2026-10-28 13:53: "nous devons savoir à quelle cote cette position devient valable ... il
# faut pouvoir prendre la position en live, ou pas ; si l'attaquant a une note de 5,5 ... on laisse").
# Before the match: every bet the model and the market both see very likely (blend >= TARGET_CHANCE) but priced under
# the 1.50 floor gets the odds from which it would be worth taking (cote_valable). Live: each cycle, a priced live row
# of such a target is noted once when its odds are within the range, its live chance x odds clears the margin, the
# live checks passed (statut "opportunite": not against the run of the match) and, for a scorer, he is in his match.
# Noted only (doc "live_a_blanc", then the registry), never shown nor counted as a position. Thresholds untested.
TARGET_CHANCE = 0.70
SCORER_TARGET_CHANCE = 0.40
MARGIN = 0.03
LIVE_ODDS = (1.50, 2.30)
LIVE_MAX_MINUTE = 80
MIN_RATING = 6.5


def _blend(r: dict) -> float | None:
    p, pm = r.get("proba"), r.get("proba_marche")
    if not isinstance(p, (int, float)):
        return None
    return (p + pm) / 2 if isinstance(pm, (int, float)) else p


def targets(doc: dict) -> dict[str, dict]:
    """The match's very likely bets too short to take before kick-off, with the odds from which each is worth it."""
    from sportai.engine.football.markets import is_quarter
    from sportai.services.revue import _keys, famille

    out = {}
    keys = _keys(doc.get("domicile") or "", doc.get("exterieur") or "")
    rows = ((doc.get("tous_marches") or {}).get("analyses") or []) + ((doc.get("buts") or {}).get("marches") or [])
    for r in rows:
        b, cote, key = _blend(r), r.get("cote"), keys.get(r.get("pari"))
        if key is None or is_quarter(key.line) or famille(key.market, key.selection, key.line) not in M_FAMILIES:
            continue  # result and goal markets only, as the M's own choices
        if b and b >= TARGET_CHANCE and isinstance(cote, (int, float)) and cote < LIVE_ODDS[0]:
            out.setdefault(r["pari"], {"pari": r["pari"], "p_avant": round(b, 4), "cote_avant": cote,
                                       "cote_valable": round(max(LIVE_ODDS[0], (1 + MARGIN) / b), 2)})
    for r in (doc.get("buteurs") or {}).get("cotes") or []:
        b, cote = _blend(r), r.get("cote")
        if r.get("joueur") and b and b >= SCORER_TARGET_CHANCE and isinstance(cote, (int, float)):
            from sportai.services.dashboard import scorer_label

            label = scorer_label(r["joueur"])
            out.setdefault(label, {"pari": label, "p_avant": round(b, 4), "cote_avant": cote,
                                   "cote_valable": round(max(LIVE_ODDS[0], (1 + MARGIN) / b), 2)})
    return out


def _in_his_match(row: dict) -> bool:
    f = (row.get("buteur") or {}).get("forme")
    if not f:
        return False  # no live rating nor shots: cannot tell he is in his match, left
    return f["note"] >= MIN_RATING if f.get("note") is not None else f.get("tirs", 0) >= 1


def capture_live(doc: dict, rows: list[dict], minute: float, now: datetime) -> list[dict]:
    """The doc's "live_a_blanc" list after one live cycle: each target noted once, the first time it is worth it."""
    noted = list(doc.get("live_a_blanc") or [])
    if minute > LIVE_MAX_MINUTE:
        return noted
    seen, tg = {n["pari"] for n in noted}, targets(doc)
    for r in rows:
        t = tg.get(r.get("pari"))
        cote, p = r.get("cote"), r.get("proba")
        if t is None or r["pari"] in seen or r.get("statut") != "opportunite" or not isinstance(cote, (int, float)):
            continue
        if not LIVE_ODDS[0] <= cote <= LIVE_ODDS[1] or p * cote < 1 + MARGIN or cote < t["cote_valable"]:
            continue
        if r.get("buteur") is not None and not _in_his_match(r):
            continue
        n = {**t, "heure": now.isoformat(), "minute": int(minute), "score": doc.get("score"), "cote": cote,
             "p_direct": p, "p_marche_direct": r.get("proba_marche")}
        if r.get("buteur"):
            n["buteur"] = {k: r["buteur"].get(k) for k in ("id", "nom", "nom_api", "cote_equipe", "apres_minute", "forme")}
        noted.append(n)
        seen.add(r["pari"])
    return noted


def _settle_live(n: dict, m: dict) -> str | None:
    if m.get("statut") != "termine":
        return None
    if n.get("buteur"):
        from sportai.services.buteurs import settle_scorer

        return settle_scorer({"buteur": n["buteur"]}, m)
    from sportai.engine.football.markets import settle_score
    from sportai.services.revue import _keys, _score

    sc, key = _score(m), _keys(m.get("domicile") or "", m.get("exterieur") or "").get(n["pari"])
    res = settle_score(key, *sc) if sc and key else None
    return res if res in SETTLED else None


def live_records(reg: dict, matchs: dict[str, dict], code_version: str) -> dict:
    """The day's live à blanc notes into the registry (flag live_a_blanc), settled once the match is over."""
    for mid, m in matchs.items():
        if m.get("jour") != reg.get("jour"):
            continue
        for n in m.get("live_a_blanc") or []:
            rid = f"{reg['jour']}|{mid}|direct|{n['pari']}"
            f = reg["fiches"].setdefault(rid, {
                "id": rid, "jour": reg["jour"], "sport": reg.get("sport", "foot"), "agent": "mathématicien (direct à blanc)",
                "decision": n["heure"], "minute": n["minute"], "score_au_moment": n.get("score"), "match_id": mid,
                "match": f"{m.get('domicile')} - {m.get('exterieur')}", "competition": m.get("competition"),
                "coup_envoi": m.get("coup_envoi"), "pari": n["pari"], "cote_prise": n["cote"],
                "p_modele": n["p_direct"], "p_marche": n.get("p_marche_direct"), "p_avant": n["p_avant"],
                "cote_avant": n["cote_avant"], "cote_valable": n["cote_valable"], "version_regles": rules_version(),
                "version_code": code_version, "recalcule": False, "live_a_blanc": True})
            if "resultat" not in f:
                res = _settle_live(n, m)
                if res:
                    f["resultat"], f["gain_unite"] = res, _gain({**f, "resultat": res})
    return reg


# --- The M's own choices, à blanc (owner 2026-10-28 14:04: "un onglet comme le joueur pro ... ses choix avant le
# match et en live pour voir ce qu'il voit et aurait fait"). The rule replayed on 5 seasons (docs/22): one position a
# match, on result and goal markets, the selection the de-vigged market sees most likely, odds 1.50-2.30, market chance
# >= M_MIN_MARKET. Chosen once, at the first registry run from the 45 min pass on (line-ups out), before kick-off;
# kept apart (choix_m), never counted with the official positions. Admin tab only.
M_MIN_MARKET = 0.60
M_ODDS = (1.50, 2.30)
M_FAMILIES = frozenset({"victoire", "nul", "double_chance", "rembourse_si_nul", "btts_oui", "plus_1_5", "plus_2_5",
                        "plus_3_5", "moins_1_5", "moins_2_5", "moins_3_5", "handicap_asiatique", "equipe_plus_0_5",
                        "equipe_plus_1_5"})


def m_options(m: dict) -> list[tuple[float, dict, str]]:
    """Every result and goal bet of the match at odds 1.50-2.30 with the market's chance, most likely first."""
    from sportai.engine.football.markets import is_quarter
    from sportai.services.revue import _keys, famille

    keys = _keys(m.get("domicile") or "", m.get("exterieur") or "")
    out = []
    rows = ((m.get("tous_marches") or {}).get("analyses") or []) + ((m.get("buts") or {}).get("marches") or [])
    for r in rows:
        key, cote, pm = keys.get(r.get("pari")), r.get("cote"), r.get("proba_marche")
        if key is None or not isinstance(cote, (int, float)) or not isinstance(pm, (int, float)) or is_quarter(key.line):
            continue
        fam = famille(key.market, key.selection, key.line)
        if fam in M_FAMILIES and M_ODDS[0] <= cote <= M_ODDS[1]:
            out.append((pm, r, fam))
    return sorted(out, key=lambda x: -x[0])


def _pass_reason(best: dict | None) -> str:
    if not best:
        return "Je passe : aucune position de résultat ou de buts entre 1,50 et 2,30."
    return (f"Je passe : la meilleure option, {best['pari']} à {best['cote_prise']:.2f}".replace(".", ",")
            + f", n'est vue qu'à {_pc(best['p_marche'])} par les bookmakers (il faut 60 %).")


def m_choice(m: dict) -> dict | None:
    """The M's position on a match document, or None when no bet of the rule exists."""
    opts = m_options(m)
    if not opts or opts[0][0] < M_MIN_MARKET:
        return None
    pm, r, fam = opts[0]
    return {"pari": r["pari"], "marche": r.get("marche"), "famille": fam, "cote_prise": r["cote"], "p_marche": pm,
            "p_modele": r.get("proba"), "bookmaker": r.get("bookmaker")}


def _m_pass(m: dict) -> dict:
    opts = m_options(m)
    best = None if not opts else {"pari": opts[0][1]["pari"], "cote_prise": opts[0][1]["cote"], "p_marche": opts[0][0]}
    return {"pari": None, "raison": _pass_reason(best), "meilleure_option": best}


def m_records(reg: dict, matchs: dict[str, dict], now: datetime, code_version: str) -> dict:
    for mid, m in matchs.items():
        if m.get("jour") != reg.get("jour"):
            continue
        rid = f"{reg['jour']}|{mid}|M"
        ko = _aware(m.get("coup_envoi"))
        prev = reg["fiches"].get(rid)
        # chosen at every run before kick-off (a choice on every match), frozen once the 45 min pass is in
        if ko and now < ko and not (prev or {}).get("fige"):
            c = m_choice(m)
            name = f"{m.get('domicile')} - {m.get('exterieur')}"
            reg.setdefault("cibles", {})[mid] = [{**t, "match": name} for t in
                                                 sorted(targets(m).values(), key=lambda t: -t["p_avant"])[:5]]
            reg["fiches"][rid] = {
                "id": rid, "jour": reg["jour"], "sport": reg.get("sport", "foot"), "agent": "le M (à blanc)",
                "decision": now.isoformat(), "match_id": mid, "match": f"{m.get('domicile')} - {m.get('exterieur')}",
                "competition": m.get("competition"), "coup_envoi": m.get("coup_envoi"), "recalcule": False,
                "choix_m": True, "version_regles": f"M>={M_MIN_MARKET}", "version_code": code_version,
                "fige": bool((m.get("passages") or {}).get("m45")), **(c or _m_pass(m))}
        elif ko and prev is None:  # first seen after kick-off (a match added late): shown, kept out of the ratios
            reg["fiches"][rid] = {
                "id": rid, "jour": reg["jour"], "sport": reg.get("sport", "foot"), "agent": "le M (à blanc)",
                "decision": now.isoformat(), "match_id": mid, "match": f"{m.get('domicile')} - {m.get('exterieur')}",
                "competition": m.get("competition"), "coup_envoi": m.get("coup_envoi"), "recalcule": False,
                "choix_m": True, "version_regles": f"M>={M_MIN_MARKET}", "version_code": code_version, "fige": True,
                "enregistre_apres_coup_envoi": True, **(m_choice(m) or _m_pass(m))}
        f = reg["fiches"].get(rid)
        if f and f.get("pari") and "resultat" not in f and m.get("statut") == "termine":
            from sportai.engine.football.markets import settle_score
            from sportai.services.revue import _keys, _score

            sc, key = _score(m), _keys(m.get("domicile") or "", m.get("exterieur") or "").get(f["pari"])
            res = settle_score(key, *sc) if sc and key else None
            if res in SETTLED:
                f["resultat"], f["gain_unite"] = res, _gain({**f, "resultat": res})
    return reg


# --- "Avis par match" (owner 2026-10-28 14:35: "une page d'échange en admin pour que les agents échangent sur leurs
# choix et leurs désaccords ... que chacun puisse donner son avis"). For each match of the day, side by side, the real
# opinions only (no staged dialogue): the official position with the model's and the market's chance, the
# Enquêteur's context alerts, the M's visa on the official position and the M's own choice; after the match, who was
# right. Updated at every registry run until kick-off, then frozen; the outcome is added once the match is over.
VISA_RED_UNDER = 0.50  # market chance under this: a coin flip at best
VISA_GREEN_FROM = 0.60
VISA_MODEL_OVER = 0.06  # model this far above the market: 50 % in 128 positions and on 5 seasons
VISA_MODEL_UNDER = 0.10  # model this far below: it does not believe its own position


def _pc(x: float) -> str:
    return f"{round(x * 100)} %"


def visa(p: dict | None) -> dict | None:
    """The M's light on a position {proba, proba_marche}: vert / orange / rouge and why."""
    if not p or not p.get("pari"):
        return None
    mod, mkt = p.get("proba"), p.get("proba_marche")
    if not isinstance(mkt, (int, float)):
        return {"feu": "orange", "raison": "Pas de chance des bookmakers pour juger."}
    if mkt < VISA_RED_UNDER:
        return {"feu": "rouge", "raison": f"Les bookmakers ne la voient qu'à {_pc(mkt)}."}
    if isinstance(mod, (int, float)) and mod - mkt >= VISA_MODEL_OVER:
        return {"feu": "rouge", "raison": f"Notre modèle ({_pc(mod)}) est bien plus optimiste que les bookmakers ({_pc(mkt)}) : "
                                          "ce profil ne passe qu'une fois sur deux."}
    if isinstance(mod, (int, float)) and mkt - mod >= VISA_MODEL_UNDER:
        return {"feu": "rouge", "raison": f"Notre modèle ({_pc(mod)}) n'y croit pas, les bookmakers {_pc(mkt)}."}
    if mkt >= VISA_GREEN_FROM:
        return {"feu": "vert", "raison": f"Les bookmakers la voient à {_pc(mkt)}, le modèle est d'accord."}
    return {"feu": "orange", "raison": f"Les bookmakers la voient à {_pc(mkt)} : un peu plus d'une chance sur deux."}


def enqueteur(m: dict) -> list[str]:
    """The context alerts of the match document (absences, referee, rotation, weather, runs), as written by the passes."""
    c = m.get("contexte") or {}
    out = list((c.get("absences_effet") or {}).get("phrases") or [])
    ref = c.get("arbitre") or {}
    if ref.get("profil") not in (None, "inconnu", "moyen") and ref.get("phrase"):
        out.append(ref["phrase"])
    for side in ("domicile", "exterieur"):
        nxt = (c.get("prochain_match") or {}).get(side) or {}
        if nxt.get("risque_rotation") in ("élevé", "moyen") and nxt.get("phrase"):
            out.append(nxt["phrase"])
        out += ((c.get("series") or {}).get(side) or {}).get("phrases") or []
    if (c.get("meteo") or {}).get("effet") and c["meteo"].get("phrase"):
        out.append(c["meteo"]["phrase"])
    return out


def _side(p: dict | None, keys: tuple = ("pari", "cote", "proba", "proba_marche", "niveau", "resultat", "non_retenu")
          ) -> dict | None:
    return {k: p.get(k) for k in keys} if p and p.get("pari") else None


def avis_records(reg: dict, pron: dict, matchs: dict[str, dict], now: datetime, sport: str = "foot") -> dict:
    av = reg.setdefault("avis", {})
    for mid, m in matchs.items():
        if m.get("jour") != reg.get("jour"):
            continue
        ko = _aware(m.get("coup_envoi"))
        off = (pron.get("paris_retenus") or {}).get(mid)
        a = av.get(mid) or {"match": f"{m.get('domicile')} - {m.get('exterieur')}", "competition": m.get("competition"),
                            "coup_envoi": m.get("coup_envoi"), "sport": sport}
        if ko and now < ko:  # opinions can still change until kick-off
            a["officielle"] = _side(off)
            a["visa_m"] = visa(off)
            a["enqueteur"] = enqueteur(m) if sport == "foot" else []
            a["maj"] = now.isoformat()
            a.pop("sans_position", None)
            if sport == "foot" and not (off or {}).get("pari"):
                a["sans_position"] = (off or {}).get("raison") or "Aucune position retenue sur ce match."
        elif "maj" not in a:  # first seen after kick-off (a match added late): filled once, flagged
            a.update(officielle=_side(off), visa_m=visa(off), enqueteur=enqueteur(m) if sport == "foot" else [],
                     maj=now.isoformat(), tardif=True)
            if sport == "foot" and not (off or {}).get("pari"):
                a["sans_position"] = (off or {}).get("raison") or "Aucune position retenue sur ce match."
        elif ko and now >= ko:
            _resync_frozen(a, off)
        mf = reg["fiches"].get(f"{reg['jour']}|{mid}|M")
        if mf is not None:
            a["choix_m"] = _m_side(mf)
        res_off = (off or {}).get("resultat") if (off or {}).get("resultat") in SETTLED else None
        if res_off and a.get("officielle"):
            a["officielle"]["resultat"] = res_off
        if m.get("statut") == "termine" and "bilan" not in a and (res_off or (a.get("choix_m") or {}).get("resultat")):
            a["bilan"] = _who_was_right(a)
        av[mid] = a
    return reg


def _m_side(mf: dict) -> dict:
    """The M's own choice in the opinions, with its light (on its choice, or on its best option when it passes)."""
    best = mf.get("meilleure_option") or {}
    lit = visa({"pari": mf.get("pari"), "proba": mf.get("p_modele"), "proba_marche": mf.get("p_marche")}) \
        if mf.get("pari") else visa({"pari": best.get("pari"), "proba_marche": best.get("p_marche")})
    return {"pari": mf.get("pari"), "cote": mf.get("cote_prise"), "proba_marche": mf.get("p_marche"),
            "proba": mf.get("p_modele"), "raison": mf.get("raison"), "resultat": mf.get("resultat"),
            "feu": (lit or {}).get("feu", "rouge" if not mf.get("pari") else None),
            **({"choix_55": {k: mf["choix_55"].get(k) for k in ("pari", "cote_prise", "p_marche", "resultat")}}
               if mf.get("choix_55") else {})}


def _resync_frozen(a: dict, off: dict | None) -> None:
    """After kick-off the opinion is kept as it was, except when the official position that froze 45 min before
    differs from the stored one (a stale copy of the register was used): then the frozen one wins."""
    off = off or {}
    if off.get("fige") and off.get("pari") and (a.get("officielle") or {}).get("pari") != off["pari"]:
        a["officielle"], a["visa_m"] = _side(off), visa(off)
        a["corrige"] = "position officielle remise sur celle figée avant le match"
        a.pop("bilan", None)


def _who_was_right(a: dict) -> str:
    o, c, v = a.get("officielle") or {}, a.get("choix_m") or {}, (a.get("visa_m") or {}).get("feu")
    parts = []
    if o.get("resultat") in ("gagne", "perdu"):
        ok = o["resultat"] == "gagne"
        parts.append(f"Position officielle {'validée' if ok else 'non validée'}"
                     + ({"vert": ", le feu vert du M était juste" if ok else ", malgré le feu vert du M",
                         "rouge": ", le feu rouge du M était juste" if not ok else ", malgré le feu rouge du M"}.get(v, "") or "") + ".")
    if c.get("resultat") in ("gagne", "perdu"):
        parts.append(f"Choix du M {'validé' if c['resultat'] == 'gagne' else 'non validé'}.")
    return " ".join(parts)


NBA_SECOND_TIER = 0.55


def basket_m_options(bm: dict) -> list[dict]:
    """The basketball game's winner / handicap / total lines at odds 1.50-2.30 with the market's chance, most likely
    first (the lines the passes priced on French books: no player bet)."""
    from sportai.services.basket import label

    out = []
    for r in (bm.get("cotes_cloture") or {}).get("lignes") or []:
        cote, pm = r.get("cote"), r.get("proba_marche")
        if r.get("marche") in ("winner", "handicap", "total") and isinstance(cote, (int, float)) \
                and isinstance(pm, (int, float)) and M_ODDS[0] <= cote <= M_ODDS[1]:
            out.append({"pari": label(r["marche"], r["selection"], r.get("ligne"), bm.get("domicile") or "",
                                      bm.get("exterieur") or ""),
                        "marche": r["marche"], "selection": r["selection"], "ligne": r.get("ligne"),
                        "cote_prise": cote, "p_marche": pm, "p_modele": r.get("proba")})
    return sorted(out, key=lambda o: -o["p_marche"])


def basket_m_choice(bm: dict) -> dict:
    """The M's position on a basketball game (same rule as football: market >= 60 %, 1.50-2.30), or why it passes."""
    opts = basket_m_options(bm)
    if opts and opts[0]["p_marche"] >= M_MIN_MARKET:
        return opts[0]
    best = {k: opts[0][k] for k in ("pari", "cote_prise", "p_marche")} if opts else None
    reason = (_pass_reason(best) if best else
              "Je passe : aucune cote de vainqueur, handicap ou total de points entre 1,50 et 2,30.")
    # the French books give one handicap and one total per game, set near 50/50: the 55 % choice is noted apart
    # (owner 2026-10-28 20:56 "il ne voit pas assez de propositions"), to judge that threshold on ~100 games
    extra = {"choix_55": dict(opts[0])} if opts and opts[0]["p_marche"] >= NBA_SECOND_TIER else {}
    return {"pari": None, "raison": reason, "meilleure_option": best, **extra}


def basket_m_records(reg: dict, bmatchs: dict[str, dict], now: datetime, code_version: str) -> dict:
    for mid, bm in bmatchs.items():
        if bm.get("jour") != reg.get("jour"):
            continue
        rid = f"{reg['jour']}|basket-{mid}|M"
        ko, prev = _aware(str(bm.get("coup_envoi") or "").replace("Z", "+00:00")), reg["fiches"].get(rid)
        late = bool(ko and now >= ko)
        if (not late and not (prev or {}).get("fige")) or (late and prev is None):
            reg["fiches"][rid] = {
                "id": rid, "jour": reg["jour"], "sport": "basket", "agent": "le M (à blanc)",
                "decision": now.isoformat(), "match_id": f"basket-{mid}",
                "match": f"{bm.get('domicile')} - {bm.get('exterieur')}", "competition": bm.get("competition"),
                "coup_envoi": bm.get("coup_envoi"), "recalcule": False, "choix_m": True,
                "version_regles": f"M>={M_MIN_MARKET}", "version_code": code_version,
                "fige": late or bool((bm.get("passages") or {}).get("m45")),
                **({"enregistre_apres_coup_envoi": True} if late else {}), **basket_m_choice(bm)}
        f = reg["fiches"][rid]
        if f.get("pari") and "resultat" not in f and bm.get("statut") == "termine" and bm.get("score"):
            from sportai.services.basket import settle

            hp, ap = (int(x) for x in str(bm["score"]).replace(" ", "").split("-"))
            f["resultat"] = settle(f["marche"], f["selection"], f.get("ligne"), hp, ap)
            f["gain_unite"] = _gain(f)
        c = f.get("choix_55")
        if c and "resultat" not in c and bm.get("statut") == "termine" and bm.get("score"):
            from sportai.services.basket import settle

            hp, ap = (int(x) for x in str(bm["score"]).replace(" ", "").split("-"))
            c["resultat"] = settle(c["marche"], c["selection"], c.get("ligne"), hp, ap)
            c["gain_unite"] = _gain(c)
    return reg


def basket_avis(reg: dict, bpron: dict, now: datetime, bmatchs: dict[str, dict] | None = None,
                code_version: str = "inconnue") -> dict:
    """The basketball games of the day in the same "avis" list (official position, the M's visa and the M's own
    choice; no Enquêteur yet), keyed basket-<id>."""
    bmatchs = {k: v for k, v in (bmatchs or {}).items() if v.get("jour") == reg.get("jour")}
    basket_m_records(reg, bmatchs, now, code_version)
    av = reg.setdefault("avis", {})
    picks = bpron.get("paris_retenus") or {}
    for mid in sorted(set(picks) | set(bmatchs)):
        p, bm = picks.get(mid) or {}, bmatchs.get(mid) or {}
        key = f"basket-{mid}"
        ko_s = p.get("coup_envoi") or bm.get("coup_envoi")
        ko = _aware(str(ko_s or "").replace("Z", "+00:00"))
        name = p.get("match") or (f"{bm.get('domicile')} - {bm.get('exterieur')}" if bm else mid)
        a = av.get(key) or {"match": name, "competition": p.get("competition") or bm.get("competition"),
                            "coup_envoi": ko_s, "sport": "basket"}
        if (ko and now < ko) or "maj" not in a:
            a["officielle"], a["visa_m"], a["enqueteur"], a["maj"] = _side(p), visa(p), [], now.isoformat()
            a.pop("sans_position", None)
            if not p.get("pari"):
                a["sans_position"] = p.get("raison") or "Aucune position retenue sur ce match."
            if ko and now >= ko:
                a["tardif"] = True
        else:
            _resync_frozen(a, p)
        mf = reg["fiches"].get(f"{reg['jour']}|basket-{mid}|M")
        if mf is not None:
            a["choix_m"] = _m_side(mf)
        if p.get("resultat") in SETTLED and a.get("officielle"):
            a["officielle"]["resultat"] = p["resultat"]
        if (bm.get("statut") == "termine" or p.get("resultat") in SETTLED) and "bilan" not in a \
                and ((a.get("officielle") or {}).get("resultat") or (a.get("choix_m") or {}).get("resultat")):
            a["bilan"] = _who_was_right(a)
        av[key] = a
    return reg


def realign(reg: dict, pron: dict, matchs: dict[str, dict], now: datetime, code_version: str, motif: str,
            bpron: dict | None = None) -> dict:
    """A day whose official list the owner had rewritten afterwards (2026-10-29: back to the best odds of every
    bookmaker): the replaced records stay, flagged remplacee, out of the ratios; the new picks are recorded flagged
    reecrit (and late, since recorded after kick-off); the opinions show the new official picks. Never silent."""
    picks = pron.get("paris_retenus") or {}
    fiches = {k: dict(v) for k, v in (reg.get("fiches") or {}).items()}
    for f in fiches.values():
        if f.get("live_a_blanc") or f.get("choix_m") or f.get("recalcule") or f.get("remplacee"):
            continue
        if (picks.get(f["match_id"]) or {}).get("pari") != f.get("pari"):
            f.update(remplacee=True, remplacee_le=now.isoformat(), motif_remplacement=motif)
    before = set(fiches)
    out = update({**reg, "fiches": fiches}, {**pron, "recalcule": False}, matchs, now, code_version)
    for rid in set(out["fiches"]) - before:
        if not out["fiches"][rid].get("choix_m") and not out["fiches"][rid].get("live_a_blanc"):
            out["fiches"][rid]["reecrit"] = motif
    av = out.setdefault("avis", {})
    sides = [(mid, p) for mid, p in picks.items()] + [(f"basket-{mid}", p) for mid, p in
                                                     ((bpron or {}).get("paris_retenus") or {}).items()]
    for key, p in sides:
        a = av.get(key)
        if not a:
            continue
        a["officielle"], a["visa_m"], a["reecrit"] = _side(p), visa(p), motif
        a.pop("bilan", None)
        a.pop("sans_position", None)
        if (a["officielle"] or {}).get("resultat") in SETTLED or (a.get("choix_m") or {}).get("resultat"):
            a["bilan"] = _who_was_right(a)
    return out
