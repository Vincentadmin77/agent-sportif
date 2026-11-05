"""Why the M took its choice, in plain words, for the M's cards (owner 2026-11-05 16:35Z: « on veut savoir pourquoi
ce choix, surtout quand c'est du sec », a straight win of a player or a team).

Built only from what the day's documents already hold: the M's rule (the most likely outcome for the bookmakers
among the odds 1.50-2.30, at least 60 %), the market's and the model's chances, the M's light, and each sport's own
facts (tennis: form, head to head, surface; football: last 10 results, table, goals series, rotation; basketball:
expected margin and total, back to back). Site wording: no money, no « pari », no bookmaker names.
"""
from __future__ import annotations

import re


def _pc(x) -> str:
    return f"{round(x * 100)} %" if isinstance(x, (int, float)) else "?"


def _odds(x) -> str:
    return f"{x:.2f}".replace(".", ",") if isinstance(x, (int, float)) else "?"


def _market_line(pm, mod, cote) -> str:
    head = f"Le marché : {_pc(pm)} de chances (cote {_odds(cote)})."
    if not isinstance(mod, (int, float)) or not isinstance(pm, (int, float)):
        return head
    gap = round((mod - pm) * 100)
    if abs(gap) <= 3:
        return f"{head} Le modèle : {_pc(mod)}, il est d'accord avec le marché."
    if gap > 0:
        return f"{head} Le modèle : {_pc(mod)}, il y croit encore plus ({gap} points de plus)."
    return f"{head} Le modèle : {_pc(mod)}, plus prudent ({-gap} points de moins), mais pas contre."


def _why_rule(live: dict | None, feu: str | None = None) -> str:
    lit = {"vert": ", et le feu du M est au vert", "orange": ", avec un feu orange (le M reste prudent)"}.get(feu or "", "")
    if live:
        return (f"Pourquoi : en direct, à {live.get('score') or '?'}, c'est l'issue que le marché voyait la plus "
                f"probable parmi les cotes de 1,50 à 2,30 (il en faut au moins 60 %){lit}.")
    return ("Pourquoi : parmi toutes les cotes de 1,50 à 2,30 du match, c'est l'issue que le marché voit la plus "
            f"probable (il en faut au moins 60 %){lit}.")


def _official(off: dict | None, pari: str) -> str:
    o = (off or {}).get("pari")
    if not o:
        return ""
    if o == pari:
        return "Le modèle a pris la même position."
    return f"Le modèle, lui, a pris « {o} »."


def _join(parts: list[str]) -> str:
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- tennis
def _player_side(pari: str, j1: str, j2: str) -> int | None:
    """1 or 2 when the position backs a player (win, sets, handicap), None for a games total."""
    for side, name in ((1, j1), (2, j2)):
        if name and pari.startswith(name):
            return side
    return None


def _tennis_facts(tm: dict, side: int) -> tuple[list[str], list[str]]:
    e = tm.get("enquete") or {}
    me, other = (tm.get("joueur_1"), tm.get("joueur_2")) if side == 1 else (tm.get("joueur_2"), tm.get("joueur_1"))
    fm, fo = e.get(f"forme_j{side}") or {}, e.get(f"forme_j{3 - side}") or {}
    sm, so = e.get(f"surface_j{side}") or {}, e.get(f"surface_j{3 - side}") or {}
    strong, weak = [], []
    if fm.get("joues"):
        w = fm.get("victoires", 0)
        (strong if w >= 3 else weak).append(f"{me} a gagné {w} de ses {fm['joues']} derniers matchs")
    if fo.get("joues"):
        w = fo.get("victoires", 0)
        if w >= 4:
            weak.append(f"{other} est en forme ({w} victoires sur {fo['joues']})")
        elif w <= 2:
            strong.append(f"{other} n'a gagné que {w} de ses {fo['joues']} derniers matchs")
    a, b = (sm.get("sur_la_surface") or {}).get("pourcentage"), (so.get("sur_la_surface") or {}).get("pourcentage")
    surf = {"dur": "dur", "hard": "dur", "terre": "terre battue", "clay": "terre battue", "gazon": "gazon",
            "grass": "gazon"}.get(str(sm.get("surface") or tm.get("surface") or "").lower(), sm.get("surface"))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        line = f"sur {surf}, {me} gagne {a} % de ses matchs, {other} {b} %"
        (strong if a > b + 2 else weak if b > a + 2 else strong).append(line)
    h = e.get("face_a_face") or {}
    if h.get("rencontres"):
        mine = h.get("victoires_j1", 0) if side == 1 else h.get("victoires_j2", 0)
        n = h["rencontres"]
        line = f"face à face : {mine} victoire{'s' if mine > 1 else ''} sur {n}"
        (strong if mine * 2 > n else weak if mine * 2 < n else strong).append(line)
    return strong, weak


def tennis(choice: dict, tm: dict, off: dict | None = None, live: dict | None = None) -> str | None:
    pari = choice.get("pari")
    if not pari:
        return None
    side = _player_side(pari, tm.get("joueur_1") or "", tm.get("joueur_2") or "")
    parts = [_why_rule(live, choice.get("feu")), _market_line(choice.get("proba_marche"), choice.get("proba"), choice.get("cote"))]
    if side:
        strong, weak = _tennis_facts(tm, side)
        if strong:
            parts.append("Points forts : " + " ; ".join(strong) + ".")
        if weak:
            parts.append("À surveiller : " + " ; ".join(weak) + ".")
    elif isinstance(tm.get("jeux_attendus"), (int, float)):
        parts.append(f"Le modèle attend environ {str(tm['jeux_attendus']).replace('.', ',')} jeux dans le match.")
    parts.append(_official(off, pari))
    return _join(parts)


# ---------------------------------------------------------------- football
def _team_side(pari: str, home: str, away: str) -> str | None:
    for side, name in (("domicile", home), ("exterieur", away)):
        if name and name in pari:
            return side
    return None


def foot(choice: dict, m: dict, off: dict | None = None) -> str | None:
    pari = choice.get("pari")
    if not pari:
        return None
    home, away = m.get("domicile") or "", m.get("exterieur") or ""
    side = _team_side(pari, home, away)
    ctx = m.get("contexte") or {}
    series = ctx.get("series") or {}
    parts = [_why_rule(None, choice.get("feu")), _market_line(choice.get("proba_marche"), choice.get("proba"), choice.get("cote"))]
    strong, weak, neutral = [], [], []
    if side:
        me, other = (home, away) if side == "domicile" else (away, home)
        s, so = series.get(side) or {}, series.get("exterieur" if side == "domicile" else "domicile") or {}
        if s.get("matchs"):
            line = f"{me} a gagné {s.get('victoires', 0)} de ses {s['matchs']} derniers matchs"
            (strong if s.get("victoires", 0) >= s["matchs"] / 2 else weak).append(line)
        if so.get("matchs") and so.get("defaites", 0) >= so["matchs"] / 2:
            strong.append(f"{other} a perdu {so['defaites']} de ses {so['matchs']} derniers matchs")
        elif so.get("matchs") and so.get("victoires", 0) >= 0.6 * so["matchs"]:
            weak.append(f"{other} a gagné {so['victoires']} de ses {so['matchs']} derniers matchs")
        if side == "domicile":
            strong.append(f"{me} joue à domicile")
        rot = ((ctx.get("prochain_match") or {}).get(side) or {}).get("risque_rotation")
        if rot:
            weak.append(f"risque de rotation chez {me} ({rot})")
    low = pari.lower()
    if "buts" in low or "marquent" in low:
        for k, team in (("domicile", home), ("exterieur", away)):
            s = series.get(k) or {}
            n = s.get("matchs")
            if not n:
                continue
            if "plus de" in low and s.get("plus_2_5") is not None:
                hit, txt = s["plus_2_5"], f"{team} : plus de 2,5 buts dans {s['plus_2_5']} de ses {n} derniers matchs"
            elif "moins de" in low and s.get("plus_2_5") is not None:
                hit, txt = n - s["plus_2_5"], f"{team} : moins de 2,5 buts dans {n - s['plus_2_5']} de ses {n} derniers matchs"
            elif "marquent" in low and s.get("deux_marquent") is not None:
                hit, txt = s["deux_marquent"], f"{team} : les deux équipes ont marqué dans {s['deux_marquent']} de ses {n} derniers matchs"
            else:
                continue
            (strong if hit >= 0.6 * n else weak if hit <= 0.4 * n else neutral).append(txt)
    if (m.get("scenario") or "").strip():
        sc = re.split(r"(?<=\.)\s", m["scenario"].strip())[0]
        parts_sc = sc if len(sc) < 160 else ""
    else:
        parts_sc = ""
    if strong:
        parts.append("Points forts : " + " ; ".join(strong) + ".")
    if weak:
        parts.append("À surveiller : " + " ; ".join(weak) + ".")
    if neutral:
        parts.append("Repères : " + " ; ".join(neutral) + ".")
    if parts_sc:
        parts.append(parts_sc)
    parts.append(_official(off, pari))
    return _join(parts)


# ---------------------------------------------------------------- basketball
def basket(choice: dict, bm: dict, off: dict | None = None) -> str | None:
    pari = choice.get("pari")
    if not pari:
        return None
    home, away = bm.get("domicile") or "", bm.get("exterieur") or ""
    side = _team_side(pari, home, away)
    pts = bm.get("points") or {}
    parts = [_why_rule(None, choice.get("feu")), _market_line(choice.get("proba_marche"), choice.get("proba"), choice.get("cote"))]
    strong = []
    if side and isinstance(pts.get("ecart_prevu"), (int, float)):
        e = pts["ecart_prevu"] if side == "domicile" else -pts["ecart_prevu"]
        me = home if side == "domicile" else away
        if e > 0:
            strong.append(f"le modèle voit {me} gagner d'environ {round(e)} points")
        if side == "domicile":
            strong.append(f"{me} joue à domicile")
    if "points" in pari.lower() and isinstance(pts.get("total_prevu"), (int, float)):
        strong.append(f"le modèle attend environ {round(pts['total_prevu'])} points dans le match")
    if strong:
        parts.append("Points forts : " + " ; ".join(strong) + ".")
    fatigue = [p for p in (bm.get("contexte") or {}).get("phrases") or [] if "veille" in p]
    if fatigue:
        parts.append("À surveiller : " + " ".join(fatigue))
    parts.append(_official(off, pari))
    return _join(parts)
