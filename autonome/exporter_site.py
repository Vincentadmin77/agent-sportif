"""Fabrique le site statique à partir des fichiers publiés de l'artifact (page, images, noyau, qui-sommes-nous).

    python -m autonome.exporter_site SOURCE CIBLE

SOURCE : dossier des fichiers de l'artifact (Artifact read, paths=[...] ; ou une copie gardée dans le dépôt).
CIBLE  : dossier servi par l'hébergeur (IONOS) ; data/ y est rempli par le serveur (etapes.publier).

La page n'est pas réécrite : on enlève seulement l'enveloppe ajoutée par claude.ai au moment de la publication et on
charge site/claude-autonome.js en premier, qui donne à la page les mêmes outils (base, visiteur, téléchargements)
mais lus dans les fichiers du serveur.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parents[1]
SHIM = ICI / "site" / "claude-autonome.js"
BALISE = '<script src="claude-autonome.js"></script>'


def nettoyer(html: str) -> str:
    # enveloppe de claude.ai : une première ligne <!doctype …><body> suivie de la vraie page
    m = re.match(r"\s*<!doctype html><html><head>.*?</head><body>\s*(?=<!doctype)", html, re.S | re.I)
    if m:
        html = html[m.end():]
    if BALISE not in html:
        html = re.sub(r"<head>", "<head>" + BALISE, html, count=1, flags=re.I)
    return html


def exporter(source: Path, cible: Path) -> list[str]:
    cible.mkdir(parents=True, exist_ok=True)
    faits = []
    for p in sorted(source.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(source)
        dst = cible / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".html":
            texte = p.read_text()
            dst.write_text(nettoyer(texte) if "window.claude" in texte else texte)
        else:
            shutil.copy(p, dst)
        faits.append(str(rel))
    shutil.copy(SHIM, cible / SHIM.name)
    (cible / "data").mkdir(exist_ok=True)
    return faits


if __name__ == "__main__":
    f = exporter(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"{len(f)} fichiers copiés dans {sys.argv[2]}")
