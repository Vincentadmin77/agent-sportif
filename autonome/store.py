"""La base du site, en fichiers : remplace la base de l'artifact Claude (outil ArtifactData).

Disposition identique à ce que les scripts lisent déjà avec --page :
    BASE/<collection>/<doc_id>.json      (le document seul, sans enveloppe)
    BASE/_versions.json                   (numéro de version de chaque document, +1 à chaque écriture)
    BASE/_journal.log                     (une ligne par écriture : heure, document, étape)

Règles reprises des consignes données aux routines Claude (jamais assouplies ici) :
- une position figée (« fige »: true) n'est jamais réécrite : si une passe la change, celle de la base est remise ;
- un passage avant-match n'écrit jamais le document matchs d'un match commencé, ni un jour passé ;
- plus aucun document joueur/* (joueur pro supprimé le 26/10) ;
- écriture atomique (fichier temporaire puis renommage) et verrou, car plusieurs étapes tournent en même temps.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

INTERDITES = {"joueur"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Champs qui font « le choix » d'une position : figés pour toujours. Les autres (resultat, statut, score, direct…)
# continuent d'évoluer après le gel, sinon une position figée ne recevrait jamais son résultat.
CHOIX = ("pari", "position", "marche", "cote", "match", "match_id", "decision", "choix_m", "confiance", "niveau")


def _cle(d: dict) -> Any:
    for k in ("match_id", "cle", "id"):
        if d.get(k) not in (None, ""):
            return (k, str(d[k]))
    return ("match", d.get("match"), d.get("pari"))


def garder_figees(ancien: Any, nouveau: Any) -> tuple[Any, int]:
    """Garantit qu'aucune position figée (dict avec fige == True) de `ancien` n'est changée ni perdue dans `nouveau`.

    - même position retrouvée (même clé ou même endroit) : ses champs de choix (CHOIX) sont remis à l'identique,
      les champs de résultat gardent la nouvelle valeur ;
    - position figée disparue : elle est remise telle quelle.
    Renvoie (document corrigé, nombre de corrections)."""
    corr = 0

    def fige(a: dict, n: Any) -> dict:
        nonlocal corr
        if not isinstance(n, dict):
            corr += 1
            return copy.deepcopy(a)
        out = dict(n)
        for k in CHOIX:
            if k in a and n.get(k) != a[k]:
                out[k] = copy.deepcopy(a[k])
                corr += 1
        out["fige"] = True
        return out

    def walk(a: Any, n: Any) -> Any:
        nonlocal corr
        if isinstance(a, dict) and a.get("fige") is True:
            return fige(a, n)
        if isinstance(a, dict) and isinstance(n, dict):
            out = dict(n)
            for k, va in a.items():
                if k in n:
                    out[k] = walk(va, n[k])
                elif _contient_figee(va):
                    out[k] = copy.deepcopy(va)
                    corr += 1
            return out
        if isinstance(a, list) and isinstance(n, list):
            if not any(_contient_figee(x) for x in a):
                return n
            idx = {_cle(x): i for i, x in enumerate(n) if isinstance(x, dict)}
            out = list(n)
            for j, x in enumerate(a):
                if not _contient_figee(x):
                    continue
                if isinstance(x, dict) and _cle(x) in idx:
                    i = idx[_cle(x)]
                    out[i] = walk(x, n[i])
                elif not isinstance(x, dict) and j < len(n):
                    out[j] = walk(x, n[j])
                else:
                    out.append(copy.deepcopy(x))
                    corr += 1
            return out
        return n

    return walk(ancien, nouveau), corr


def _contient_figee(v: Any) -> bool:
    if isinstance(v, dict):
        return v.get("fige") is True or any(_contient_figee(x) for x in v.values())
    if isinstance(v, list):
        return any(_contient_figee(x) for x in v)
    return False


class Store:
    def __init__(self, base: str | os.PathLike):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    # --- verrou global (un seul écrivain à la fois, comme les if_version de la base Claude) ---
    @contextmanager
    def verrou(self) -> Iterator[None]:
        with open(self.base / "_verrou", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def chemin(self, collection: str, doc_id: str) -> Path:
        return self.base / collection / f"{doc_id}.json"

    def get(self, collection: str, doc_id: str) -> dict | None:
        p = self.chemin(collection, doc_id)
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def lister(self, collection: str) -> dict[str, dict]:
        d = self.base / collection
        if not d.is_dir():
            return {}
        out = {}
        for p in sorted(d.glob("*.json")):
            try:
                out[p.stem] = json.loads(p.read_text())
            except ValueError:
                continue
        return out

    def collections(self) -> list[str]:
        return sorted(p.name for p in self.base.iterdir() if p.is_dir() and not p.name.startswith("_"))

    def versions(self) -> dict[str, int]:
        try:
            return json.loads((self.base / "_versions.json").read_text())
        except (OSError, ValueError):
            return {}

    def _ecrire_fichier(self, p: Path, texte: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            f.write(texte)
        os.replace(tmp, p)

    def set(self, collection: str, doc_id: str, data: dict, etape: str = "", proteger: bool = True) -> dict:
        """Écrit un document ; renvoie {ecrit, version, figees_remises}. N'écrit rien si le contenu ne change pas."""
        if collection.split("/")[0] in INTERDITES:
            return {"ecrit": False, "raison": "collection interdite"}
        with self.verrou():
            ancien = self.get(collection, doc_id)
            remis = 0
            if proteger and ancien is not None:
                data, remis = garder_figees(ancien, data)
            if ancien == data:
                return {"ecrit": False, "raison": "identique", "figees_remises": remis}
            self._ecrire_fichier(self.chemin(collection, doc_id), json.dumps(data, ensure_ascii=False))
            v = self.versions()
            cle = f"{collection}/{doc_id}"
            v[cle] = v.get(cle, 0) + 1
            self._ecrire_fichier(self.base / "_versions.json", json.dumps(v, sort_keys=True))
            with open(self.base / "_journal.log", "a") as j:
                j.write(f"{_now()} {etape or '-'} {cle} v{v[cle]}" + (f" figees_remises={remis}" if remis else "") + "\n")
            return {"ecrit": True, "version": v[cle], "figees_remises": remis}

    def supprimer(self, collection: str, doc_id: str, etape: str = "") -> bool:
        """Réservé aux actions décidées par Vincent (ex. retirer une liste) : jamais appelé par les passages."""
        with self.verrou():
            p = self.chemin(collection, doc_id)
            if not p.exists():
                return False
            p.unlink()
            with open(self.base / "_journal.log", "a") as j:
                j.write(f"{_now()} {etape or '-'} {collection}/{doc_id} SUPPRIME\n")
            return True

    def ecrire_sortie(self, out: str | os.PathLike, etape: str,
                      garder: Callable[[str, str, dict], bool] | None = None) -> list[str]:
        """Recopie dans la base les documents produits par un script (dossier OUT au format --out).

        OUT/<collection>/<doc>.json, et OUT/etat.json -> etat/agent. `garder(collection, doc_id, data)` peut écarter
        un document (ex. matchs d'un match commencé pendant un avant-match)."""
        out = Path(out)
        ecrits = []
        if not out.is_dir():
            return ecrits
        for p in sorted(out.glob("*/*.json")):
            coll, doc = p.parent.name, p.stem
            if coll in INTERDITES:
                continue
            try:
                data = json.loads(p.read_text())
            except ValueError:
                continue
            if not isinstance(data, dict) or (garder and not garder(coll, doc, data)):
                continue
            if self.set(coll, doc, data, etape).get("ecrit"):
                ecrits.append(f"{coll}/{doc}")
        etat = out / "etat.json"
        if etat.exists() and (not garder or garder("etat", "agent", {})):
            try:
                if self.set("etat", "agent", json.loads(etat.read_text()), etape).get("ecrit"):
                    ecrits.append("etat/agent")
            except ValueError:
                pass
        return ecrits
