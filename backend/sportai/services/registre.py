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
    reg = {"jour": day, "sport": sport, "fiches": {k: dict(v) for k, v in (registre.get("fiches") or {}).items()}}
    if pron.get("recalcule"):
        return reg  # rebuilt history: see import_rebuilt
    r = rules()
    for p in (pron.get("paris_retenus") or {}).values():
        if not p.get("pari") or not p.get("fige"):
            continue
        rid = record_id(day, p.get("match_id"), p["pari"])
        same_match = [f for f in reg["fiches"].values() if f["match_id"] == p.get("match_id")]
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
        for src, dst in WATCHED.items():
            if src != "pari" and p.get(src) is not None and p.get(src) != f.get(dst):
                log = {"heure": now.isoformat(), "champ": dst, "avant": f.get(dst), "apres": p.get(src)}
                mods = f.setdefault("modifications", [])
                if not mods or (mods[-1]["champ"], mods[-1]["apres"]) != (dst, log["apres"]):
                    mods.append(log)
        if p.get("resultat") in SETTLED:
            f["resultat"] = p["resultat"]
    for f in reg["fiches"].values():
        m = matchs.get(f["match_id"]) or {}
        ko = _aware(f.get("coup_envoi"))
        if "cote_cloture" not in f and ko and now >= ko and m:
            cote, pm = closing_odds(m, f["pari"])
            if cote and not _priced_after(m, f, ko):  # odds no newer than the decision: no closing line to compare
                f["cote_cloture"] = None
            elif cote:
                f["cote_cloture"], f["p_marche_cloture"] = cote, pm
                f["clv"] = round(f["cote_prise"] / cote - 1, 4) if f.get("cote_prise") else None
        if f.get("resultat") in SETTLED:
            f["gain_unite"] = _gain(f)
    return reg


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
    for label, rows in (("reel", [f for f in fiches if not f.get("recalcule")]),
                        ("recalcule", [f for f in fiches if f.get("recalcule")])):
        part = {"global": measures(rows)}
        for key in ("niveau", "marche", "competition", "sport"):
            groups = defaultdict(list)
            for f in rows:
                groups[f.get(key) or "?"].append(f)
            part[key] = {g: measures(v) for g, v in sorted(groups.items(), key=lambda x: -len(x[1]))
                         if len([r for r in v if r.get("resultat") in ("gagne", "perdu")]) >= min_group}
        out[label] = part
    return out
