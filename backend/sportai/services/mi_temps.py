"""Half-time bets of the odds feed priced by the half-time model (engine/football/halves.py, owner 2026-10-27:
"toutes les cotes doivent être étudiées"). Stored on the run's fixture as "mi_temps_cotes"; the bets then leave
the "non analysé" list. The bookmaker margin is removed on complete groups of one bookmaker (median over the
bookmakers), otherwise the market's chance is 1 / odds, margin included."""

from __future__ import annotations

from statistics import median

from sportai.engine.football import halves


def price_halves(item: dict, home: str, away: str, expected_goals, competition: str) -> list[dict]:
    lam, mu = float(expected_goals[0]), float(expected_goals[1])
    j = halves.joint(lam, mu, halves.FIRST_HALF_SHARE.get(competition, halves.DEFAULT_SHARE))
    best: dict[tuple, dict] = {}
    by_book: dict[tuple, dict[str, float]] = {}  # (bookmaker, market, line) -> selection -> odds
    for bk in item.get("bookmakers") or []:
        book = bk.get("name") or str(bk.get("id"))
        for bet in bk.get("bets") or []:
            for v in bet.get("values") or []:
                mapped = halves.map_value(bet.get("name") or "", v.get("value"))
                if mapped is None:
                    continue
                try:
                    odd = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if odd <= 1.0:
                    continue
                by_book.setdefault((book, mapped[0], mapped[2]), {})[mapped[1]] = odd
                b = best.get(mapped)
                if b is None or odd > b["cote"]:
                    best[mapped] = {"cote": odd, "bookmaker": book}
    rows = []
    for (market, sel, line), b in best.items():
        fair = []
        sides = halves.group(market)
        for (book, m, ln), odds in by_book.items():
            if m != market or ln != line or sel not in odds:
                continue
            if sides and all(s in odds for s in sides):
                fair.append((1 / odds[sel]) / sum(1 / odds[s] for s in sides))
        pm = median(fair) if fair else 1 / b["cote"]
        try:
            o = halves.outcome(j, market, sel, line)
        except ValueError:
            continue
        rows.append({"marche": market, "selection": sel, "ligne": line,
                     "pari": halves.label(market, sel, line, home, away), "cote": b["cote"],
                     "bookmaker": b["bookmaker"], "proba": round(o.win, 4), "proba_rembourse": round(o.push, 4),
                     "proba_marche": round(pm, 4), "proba_marche_brute": not fair,
                     "valeur": round(o.win * b["cote"] + o.push - 1, 4),
                     "valide": halves.validated(market, sel, line)})
    return sorted(rows, key=lambda r: -r["valeur"])
