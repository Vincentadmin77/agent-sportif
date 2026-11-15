"""Bascule : recopie la base de l'artifact Claude dans la base en fichiers du serveur (une seule fois, le jour J).

    python -m autonome.importer_base DUMP BASE

DUMP : dossier rempli par Claude avec ArtifactData list (out_dir=DUMP) de chaque collection, pages suivies jusqu'au
bout (next_cursor). Disposition DUMP/<collection>/<doc>.json : la même que la base en fichiers.
Rien n'est recalculé ni modifié : copie à l'identique, avec un contrôle du nombre de documents par collection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .store import Store

COLLECTIONS = ("matchs", "pronostic", "stats", "reglages", "etat", "basket_matchs", "basket_pronostic", "registre",
               "tennis", "top5", "version_finale", "surmesure_basket", "surmesure_tennis", "selection_telegram",
               "specialistes", "carnet_analytrack", "basket_m_direct", "pouls", "rapports", "logos")


def importer(dump: Path, base: Path) -> dict[str, int]:
    st = Store(base)
    compte = {}
    for coll in COLLECTIONS:
        d = dump / coll
        if not d.is_dir():
            continue
        n = 0
        for p in sorted(d.glob("*.json")):
            data = json.loads(p.read_text())
            st.set(coll, p.stem, data, "import", proteger=False)
            n += 1
        assert len(list((base / coll).glob("*.json"))) >= n, coll
        compte[coll] = n
    return compte


if __name__ == "__main__":
    print(json.dumps(importer(Path(sys.argv[1]), Path(sys.argv[2])), indent=1))
