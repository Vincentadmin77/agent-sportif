"""A clear position on every match of the day (owner, 2026-10-11): the most likely outcome, and either the
advised bet or the precise reason not to bet. Reads only what the match document already holds."""

from __future__ import annotations


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _market(cotes: dict) -> dict | None:
    """Bookmaker 1X2 odds without the margin (simple normalisation)."""
    try:
        inv = {k: 1 / float(cotes[k]) for k in ("domicile", "nul", "exterieur")}
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def position(doc: dict) -> dict | None:
    home, away = doc.get("domicile"), doc.get("exterieur")
    probs, source = doc.get("proba_1x2"), "modele"
    if not probs:
        probs, source = _market(doc.get("cotes_1x2") or {}), "marche"
    if not probs or not home or not away:
        return None
    side = max(("domicile", "exterieur", "nul"), key=lambda k: probs[k])  # on an exact tie, not the draw
    issue = {"domicile": f"Victoire {home}", "exterieur": f"Victoire {away}", "nul": "Match nul"}[side]
    out = {"issue": issue, "proba": round(probs[side], 4), "source": source}
    words = {"domicile": f"{home} gagne", "exterieur": f"{away} gagne", "nul": "match nul"}[side]
    text = f"Notre position : {words} ({_pct(probs[side])})"
    if side != "nul" and probs[side] < 0.5:  # not a clear favourite: the safer reading covers the draw too
        dc = f"{home if side == 'domicile' else away} ou nul"
        out["double_chance"] = {"pari": dc, "proba": round(probs[side] + probs["nul"], 4)}
        text += f", au minimum {dc} ({_pct(probs[side] + probs['nul'])})"
    text += "."
    sels = doc.get("selections") or []
    if sels:
        s = sels[0]
        odd = s.get("cote_45") or s.get("cote_h3") or s.get("cote")
        out["pari_conseille"] = s.get("pari")
        text += f" Pari conseillé : {s.get('pari')}" + (f" à {odd:.2f}".replace(".", ",") if odd else "") + "."
    else:
        why = next((r for r in doc.get("rejets") or [] if r.startswith(issue)), None)
        if source == "marche":
            why = "le modèle n'a pas assez d'historique pour une des deux équipes, cette lecture vient des cotes"
        out["sans_pari"] = why or "aucune cote ne donne assez d'avantage"
        text += f" Pas de mise conseillée : {out['sans_pari'][0].lower() + out['sans_pari'][1:]}"
        text += "" if text.endswith(".") else "."
    out["texte"] = text
    return out
