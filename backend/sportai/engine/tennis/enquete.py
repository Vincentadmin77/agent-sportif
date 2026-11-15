"""Tennis investigator report ("rapport de l'enquêteur"), owner request 2026-11-05.

For one match, from the results history only (matches played before the day):
- forme: each player's last 5 matches (won or lost, opponent, surface, date), all levels including Challenger;
- face_a_face: head-to-head count and the last meeting (date, tournament, surface, winner, score);
- surface: each player's wins and losses on the match surface over the last 12 months, and on all surfaces;
- note_surface: one plain line on how the surface weighs in the model (50 % general rating, 50 % surface rating).

It is information shown on the page; it never changes the choice.
"""

from __future__ import annotations

from datetime import date, timedelta

SURFACE_FR = {"hard": "dur", "clay": "terre battue", "grass": "gazon"}


def _done(m) -> bool:
    return m.status == "Finished" and m.a_won is not None and bool(m.sets)


def _score(m, for_a: bool) -> str:
    return " ".join(f"{x}-{y}" if for_a else f"{y}-{x}" for x, y in m.sets)


class Enqueteur:
    """Index of finished matches per player, built once per run."""

    def __init__(self, history, before: str):
        self.by_player: dict[str, list] = {}
        for m in history:
            if m.day >= before or not _done(m):
                continue
            self.by_player.setdefault(m.a, []).append(m)
            self.by_player.setdefault(m.b, []).append(m)
        for ms in self.by_player.values():
            ms.sort(key=lambda m: (m.day, m.time or ""))
        self.before = before

    def _forme(self, pid: str) -> dict:
        last = self.by_player.get(pid, [])[-5:][::-1]
        out = []
        for m in last:
            is_a = m.a == pid
            won = m.a_won if is_a else not m.a_won
            out.append({"date": m.day, "adversaire": m.name_b if is_a else m.name_a, "tournoi": m.tournament,
                        "surface": SURFACE_FR.get(m.surface, m.surface), "gagne": bool(won),
                        "score": _score(m, is_a)})
        return {"matchs": out, "victoires": sum(x["gagne"] for x in out), "joues": len(out),
                "resume": "".join("V" if x["gagne"] else "D" for x in out)}

    def _surface(self, pid: str, surface: str) -> dict:
        since = str(date.fromisoformat(self.before) - timedelta(days=365))
        ms = [m for m in self.by_player.get(pid, []) if m.day >= since]
        def wl(xs):
            w = sum(1 for m in xs if (m.a_won if m.a == pid else not m.a_won))
            return {"victoires": w, "defaites": len(xs) - w,
                    "pourcentage": round(100 * w / len(xs)) if xs else None}
        return {"surface": SURFACE_FR.get(surface, surface), "sur_la_surface": wl([m for m in ms if m.surface == surface]),
                "toutes_surfaces": wl(ms)}

    def _h2h(self, a: str, b: str) -> dict:
        ms = [m for m in self.by_player.get(a, []) if {m.a, m.b} == {a, b}]
        wa = sum(1 for m in ms if (m.a_won if m.a == a else not m.a_won))
        last = None
        if ms:
            m = ms[-1]
            a_first = m.a == a
            won_a = m.a_won if a_first else not m.a_won
            last = {"date": m.day, "tournoi": m.tournament, "surface": SURFACE_FR.get(m.surface, m.surface),
                    "vainqueur": m.name_a if m.a_won else m.name_b, "score": _score(m, a_first), "j1_gagne": bool(won_a)}
        return {"rencontres": len(ms), "victoires_j1": wa, "victoires_j2": len(ms) - wa, "derniere": last}

    def rapport(self, m) -> dict:
        s = SURFACE_FR.get(m.surface, m.surface)
        return {
            "forme_j1": self._forme(m.a), "forme_j2": self._forme(m.b),
            "surface_j1": self._surface(m.a, m.surface), "surface_j2": self._surface(m.b, m.surface),
            "face_a_face": self._h2h(m.a, m.b),
            "note_surface": (f"Le terrain compte : le modèle note chaque joueur à moitié sur son niveau général et à "
                             f"moitié sur son niveau sur {s}. Les aces sont aussi estimés selon la surface."),
            "depuis": "résultats depuis janvier 2023, Challenger compris",
        }
