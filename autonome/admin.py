"""Actions d'administration sur le serveur (Claude ou Vincent, par SSH) : voir, corriger, retirer. Tout est journalisé.

    python -m autonome.admin etat                         # voyants pouls, dernières étapes, pannes du jour
    python -m autonome.admin lire top5 2026-11-16         # un document
    python -m autonome.admin ratios 2026-11-09 2026-11-15 # réussite réelle par sport et par acteur (Top 5)
    python -m autonome.admin retirer version_finale 2026-11-16 --motif "décision de Vincent du 16/11"
    python -m autonome.admin ecrire reglages championnats fichier.json --motif "..."

Une écriture faite ici ignore la protection des positions figées : à n'utiliser que sur décision de Vincent,
avec son motif (comme mathematicien.py realigner aujourd'hui).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from .store import Store

BASE = Path(os.getenv("ANALYTRACK_BASE", "/srv/analytrack/base"))


def ratios(st: Store, debut: str, fin: str) -> dict:
    out: dict = {}
    j = date.fromisoformat(debut)
    while str(j) <= fin:
        doc = st.get("top5", str(j)) or {}
        for sport in ("foot", "basket", "tennis"):
            for acteur, v in (doc.get(sport) or {}).items():
                if not isinstance(v, dict):
                    continue
                r = out.setdefault(sport, {}).setdefault(acteur, {"gagnees": 0, "jouees": 0})
                for p in v.get("positions") or []:
                    if p.get("resultat") in ("gagne", "perdu"):
                        r["jouees"] += 1
                        r["gagnees"] += p["resultat"] == "gagne"
        j += timedelta(days=1)
    for s in out.values():
        for r in s.values():
            r["reussite"] = f"{100 * r['gagnees'] / r['jouees']:.0f} %" if r["jouees"] else "-"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("etat", "lire", "ratios", "retirer", "ecrire"))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--motif", default="")
    a = ap.parse_args()
    st = Store(BASE)
    if a.cmd == "etat":
        journal = (BASE / "_journal.log").read_text().splitlines()[-15:] if (BASE / "_journal.log").exists() else []
        print(json.dumps({"pouls": st.lister("pouls"), "dernieres_ecritures": journal}, ensure_ascii=False, indent=1))
    elif a.cmd == "lire":
        print(json.dumps(st.get(a.args[0], a.args[1]), ensure_ascii=False, indent=1))
    elif a.cmd == "ratios":
        print(json.dumps(ratios(st, a.args[0], a.args[1]), ensure_ascii=False, indent=1))
    elif a.cmd in ("retirer", "ecrire"):
        if not a.motif:
            raise SystemExit("--motif obligatoire (qui a décidé, quand)")
        if a.cmd == "retirer":
            print(st.supprimer(a.args[0], a.args[1], f"admin: {a.motif}"))
        else:
            print(st.set(a.args[0], a.args[1], json.loads(Path(a.args[2]).read_text()), f"admin: {a.motif}",
                         proteger=False))


if __name__ == "__main__":
    main()
