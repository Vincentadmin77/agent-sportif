"""Les étapes de la journée, sans Claude : chacune lance les mêmes scripts, avec les mêmes options, que les tâches
planifiées Claude du 15/11/2026 (voir docs/30-autonomie.md, tableau « qui faisait quoi »), puis écrit le résultat dans
la base en fichiers (store.py) au lieu de la base de l'artifact.

Aucune règle, aucun calcul, aucun seuil n'est défini ici : tout vient des scripts du dépôt (modèle gelé).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .store import Store

PARIS = ZoneInfo("Europe/Paris")
REPO = Path(os.getenv("ANALYTRACK_REPO", Path(__file__).resolve().parents[1]))
BACKEND = REPO / "backend"
SCRIPTS = BACKEND / "scripts"
# Les scripts gardent leurs chemins d'origine (caches, état, fichiers tennis) : sur le serveur,
# /mnt/project-files/agent-sportif est un lien vers le dossier des fichiers (deploy/installer.sh).
FICHIERS = Path(os.getenv("ANALYTRACK_FICHIERS", "/mnt/project-files/agent-sportif"))
TRAVAIL = Path(os.getenv("ANALYTRACK_TRAVAIL", "/srv/analytrack/travail"))
PY = sys.executable

FOOT_AUTORISES = ["FR1", "EN1", "ES1", "DE1", "IT1", "BR1", "UCL", "UEL", "TR1", "ENC", "PT1", "FRC", "UNL"]
TOUJOURS_FOOT = ["FRC", "UNL"]  # Vincent 09/11 : toujours ajoutés
BASKET = ["NBA", "WNBA"]  # Vincent 27/10 : toujours ajoutés
LIGUES_TEST = "61,39,140,78,135,71,2,3,203,94,45,5,66"  # sélection test (13/11 : + Ligue des Nations, Coupe de France)
# Vincent 26/11 : un seul acteur par sport. Foot = le spécialiste (V1), basket = le Sur-mesure Analytrack (sélection
# test basket), tennis = le Mathématicien. Les autres listes sont arrêtées ; les scripts le font d'eux-mêmes à partir
# du 26/11, ces réglages évitent en plus les appels inutiles. Le passé n'est jamais réécrit.
UN_ACTEUR_DU = "2026-11-26"
SPECIALISTE_TENNIS_ARRETE_DU = "2026-11-27"  # le 26/11 se termine normalement
SEUIL_CREDITS = 100


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def jour_foot(t: datetime | None = None) -> str:
    """Journée foot : 8 h à 2 h du matin (Paris), comme journee() du code."""
    return str(((t or maintenant()).astimezone(PARIS) - timedelta(hours=2)).date())


def jour_paris(t: datetime | None = None, bascule_h: int = 0) -> str:
    """Date de Paris ; avant `bascule_h` heures, c'est la veille (M : 6 h, spécialistes : 4 h)."""
    loc = (t or maintenant()).astimezone(PARIS)
    return str((loc - timedelta(hours=bascule_h)).date())


def veille(j: str) -> str:
    return str(date.fromisoformat(j) - timedelta(days=1))


class Echec(RuntimeError):
    pass


def lancer(args: list[str], timeout: int = 1800, env: dict | None = None, cwd: Path = BACKEND) -> str:
    """Lance un script ; renvoie sa sortie ; lève Echec avec la fin de l'erreur si le code retour n'est pas 0."""
    e = {**os.environ, **(env or {})}
    r = subprocess.run([PY, *args] if args[0].endswith(".py") else args, cwd=cwd, env=e, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode:
        raise Echec(f"{Path(args[0]).name} {' '.join(args[1:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def dossier(nom: str) -> Path:
    TRAVAIL.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{nom}-", dir=TRAVAIL))


def vue(store: Store, collections: dict[str, callable | None]) -> Path:
    """Copie de travail d'une partie de la base (ce que Claude lisait avec ArtifactData list/query + out_dir).

    collections : {nom: filtre(data) -> bool, ou None pour tout}."""
    d = dossier("page")
    for coll, filtre in collections.items():
        src = store.base / coll
        if not src.is_dir():
            continue
        (d / coll).mkdir(parents=True, exist_ok=True)
        for p in src.glob("*.json"):
            if filtre is None:
                shutil.copy(p, d / coll / p.name)
            else:
                try:
                    if filtre(json.loads(p.read_text())):
                        shutil.copy(p, d / coll / p.name)
                except ValueError:
                    pass
    return d


def fichier_doc(store: Store, coll: str, doc: str) -> Path | None:
    """Le document de la base comme fichier (ou None s'il n'existe pas)."""
    p = store.chemin(coll, doc)
    return p if p.exists() else None


def credits_odds() -> int | None:
    """Crédits The Odds API restants (dernière valeur notée par les scripts dans state/odds_credits.txt)."""
    try:
        for ligne in reversed((FICHIERS / "state" / "odds_credits.txt").read_text().splitlines()):
            n = ligne.split()[0]
            if n.isdigit():
                return int(n)
    except (OSError, IndexError):
        pass
    return None


def pouls(store: Store, doc: str, etape: str, agent: str, matchs: int, ok: bool, erreur: str | None = None) -> None:
    """Voyant de santé du noyau (collection pouls), même format que les routines Claude."""
    store.set("pouls", doc, {"maj": maintenant().strftime("%Y-%m-%dT%H:%M:%SZ"), "etape": etape, "agent": agent,
                             "matchs": matchs, "ok": ok, "erreur": (erreur or None) and str(erreur)[:120]},
              "pouls", proteger=False)


# ---------------------------------------------------------------- foot + basket (fil « Première analyse réelle »)

def championnats(store: Store) -> list[str]:
    actifs = (store.get("reglages", "championnats") or {}).get("actifs") or []
    foot = [c for c in actifs if c in FOOT_AUTORISES] or list(FOOT_AUTORISES)
    for c in TOUJOURS_FOOT:
        if c not in foot:
            foot.append(c)
    return foot + BASKET


def _commence(data: dict, t: datetime) -> bool:
    ko = data.get("coup_envoi")
    try:
        return bool(ko) and datetime.fromisoformat(str(ko).replace("Z", "+00:00")) <= t
    except ValueError:
        return False


def garder_avant_match(jour: str, t: datetime):
    """Un avant-match n'écrit jamais le document matchs d'un match commencé, ni un jour passé ; jamais joueur/*."""
    def garder(coll: str, doc: str, data: dict) -> bool:
        if coll in ("matchs", "basket_matchs"):
            if _commence(data, t):
                return False
            j = data.get("jour") or doc[:10]
            return not (len(j) == 10 and j[:4].isdigit() and j < jour and coll == "matchs")
        if coll in ("pronostic", "basket_pronostic"):
            return doc >= veille(jour)  # le jour même (et la veille pour un match NBA tardif)
        return True
    return garder


def registre(store: Store, jour: str, etape: str) -> list[str]:
    """Étape REGISTRE (docs/22-agent-mathematicien.md), après chaque écriture de pronostic ou basket_pronostic."""
    page = vue(store, {"registre": None, "pronostic": None, "basket_pronostic": None, "tennis": None,
                       "matchs": lambda d: str(d.get("jour", "")) >= veille(jour), "basket_matchs": None})
    out = dossier("regout")
    lancer([str(SCRIPTS / "mathematicien.py"), "registre", "--page", str(page), "--out", str(out), "--jour", jour])
    f = out / "registre" / f"{jour}.json"
    ecrit = []
    if f.exists() and store.set("registre", jour, json.loads(f.read_text()), etape).get("ecrit"):
        ecrit.append(f"registre/{jour}")
    shutil.rmtree(page, True), shutil.rmtree(out, True)
    return ecrit


def passe_avant_match(store: Store, comps: list[str], etape: str, goals: bool = False, matin: bool = False) -> list[str]:
    t = maintenant()
    jour = jour_foot(t)
    out = dossier(etape)
    args = [str(SCRIPTS / "agent_pass.py"), "prematch", *comps, "--page", str(store.base), "--out", str(out)]
    if goals and (credits_odds() or 10**9) >= SEUIL_CREDITS:
        args.append("--goals")
    if matin:
        args.append("--matin")
    lancer(args, timeout=3000)
    ecrits = store.ecrire_sortie(out, etape, garder_avant_match(jour, t))
    shutil.rmtree(out, True)
    if any(e.startswith(("pronostic/", "basket_pronostic/")) for e in ecrits) or matin:
        ecrits += registre(store, jour, etape)
    return ecrits


def matin(store: Store) -> dict:
    """07:50 Paris : modèle de buts du jour, analyse du matin de tous les sports, puis le planning du jour."""
    comps = championnats(store)
    notes = {}
    try:
        lancer([str(SCRIPTS / "agent_pass.py"), "goals-refresh"], timeout=1200)
    except Echec as e:  # comme avant : les passages retombent sur Dixon-Coles
        notes["goals_refresh"] = str(e)
    notes["ecrits"] = passe_avant_match(store, comps, "matin", matin=True)
    plan = json.loads(lancer([str(SCRIPTS / "agent_pass.py"), "plan", "--competitions", *comps], timeout=600))
    store.set("_plan", jour_foot(), plan, "matin", proteger=False)
    notes["etapes_planifiees"] = len(plan.get("steps", []))
    return notes


def direct_basket(store: Store, comps: list[str]) -> list[str]:
    """Étape « direct » basket : un seul passage live, écrit seulement basket_matchs et basket_pronostic."""
    out = dossier("direct-basket")
    lancer([str(SCRIPTS / "agent_pass.py"), "live", *comps, "--page", str(store.base), "--out", str(out)], timeout=600)
    ecrits = store.ecrire_sortie(out, "direct-basket", lambda c, d, x: c in ("basket_matchs", "basket_pronostic"))
    shutil.rmtree(out, True)
    if any(e.startswith("basket_pronostic/") for e in ecrits):
        ecrits += registre(store, jour_foot(), "direct-basket")
    return ecrits


def cycle_direct_foot(store: Store, comps: list[str]) -> list[str]:
    """Un cycle de la boucle du direct foot (routines/outils/cyc.sh + livefilt.py)."""
    t = maintenant()
    aujourdhui = str(t.date())
    out = dossier("direct-foot")
    lancer([str(SCRIPTS / "agent_pass.py"), "live", *comps, "--page", str(store.base), "--out", str(out)], timeout=150,
           env={"SPORTAI_HTTP_CACHE": str(TRAVAIL / "httpcache")})

    def garder(coll: str, doc: str, data: dict) -> bool:
        if coll in ("etat", "joueur", "stats", "basket_matchs", "basket_pronostic"):
            return False
        if coll == "matchs":
            if not doc.startswith(aujourdhui):
                return False
            ancien = store.get("matchs", doc) or {}
            sans = lambda d: {k: v for k, v in d.items() if k not in ("maj", "version", "passage")}  # noqa: E731
            return data.get("statut") in ("en_direct", "termine") or sans(data) != sans(ancien)
        return True

    ecrits = store.ecrire_sortie(out, "direct-foot", garder)
    shutil.rmtree(out, True)
    return ecrits


def final(store: Store, comps: list[str]) -> list[str]:
    t = maintenant()
    jour = jour_foot(t)
    out = dossier("final")
    lancer([str(SCRIPTS / "agent_pass.py"), "scores", *comps, "--final", "--page", str(store.base), "--out", str(out)],
           timeout=1800)

    def garder(coll: str, doc: str, data: dict) -> bool:  # documents du jour seulement
        if coll in ("matchs", "basket_matchs"):
            return str(data.get("jour") or doc[:10]) >= veille(jour) or coll == "basket_matchs"
        if coll in ("pronostic", "basket_pronostic"):
            return doc >= veille(jour)
        return coll in ("stats", "etat")

    ecrits = store.ecrire_sortie(out, "final", garder)
    shutil.rmtree(out, True)
    return ecrits + registre(store, jour, "final")


def nb_matchs_du_jour(store: Store, coll: str, jour: str) -> int:
    return sum(1 for d in store.lister(coll).values() if str(d.get("jour", "")) == jour)


# ---------------------------------------------------------------- tennis (fil « Brancher le tennis »)

def fichier_tennis(j: str) -> Path:
    return FICHIERS / "tennis" / f"{j}.json"


def tennis_vers_base(store: Store, j: str, etape: str) -> list[str]:
    f = fichier_tennis(j)
    if not f.exists():
        return []
    ecrits = []
    if store.set("tennis", j, json.loads(f.read_text()), etape).get("ecrit"):
        ecrits.append(f"tennis/{j}")
        page = vue(store, {"registre": None, "pronostic": None, "basket_pronostic": None, "tennis": None})
        out = dossier("regout-tennis")
        lancer([str(SCRIPTS / "mathematicien.py"), "registre-tennis", "--page", str(page), "--out", str(out),
                "--jour", j])
        r = out / "registre" / f"{j}.json"
        if r.exists() and store.set("registre", j, json.loads(r.read_text()), etape).get("ecrit"):
            ecrits.append(f"registre/{j}")
        shutil.rmtree(page, True), shutil.rmtree(out, True)
    return ecrits


def _tennis_non_regle(j: str) -> bool:
    try:
        d = json.loads(fichier_tennis(j).read_text())
    except (OSError, ValueError):
        return False
    return any((m.get(k) or {}).get("position") and not (m.get(k) or {}).get("resultat")
               for m in d.get("matchs", []) for k in ("choix", "m", "m_direct"))


def tennis_matin(store: Store) -> list[str]:
    j = jour_paris()
    y = veille(j)
    ecrits = []
    if _tennis_non_regle(y):  # la veille doit être réglée (status.sh puis un passage)
        try:
            lancer([str(SCRIPTS / "tennis_direct.py"), "--date", y, "--out", str(fichier_tennis(y)), "--once"],
                   timeout=900)
        except Echec:
            pass
        ecrits += tennis_vers_base(store, y, "tennis-matin")
    lancer([str(SCRIPTS / "tennis_jour.py"), "--date", j, "--out", str(fichier_tennis(j))], timeout=1800)
    return ecrits + tennis_vers_base(store, j, "tennis-matin")


def tennis_gel(store: Store, jusqua: str) -> list[str]:
    """Gel de 12:40 (jusqu'à 16:00) et de 16:40 (jusqu'à 23:59) : aucune position d'un match commencé ne doit bouger.

    L'appelant met la boucle du direct en pause avant, et la relance après (orchestre.py)."""
    j = jour_paris()
    f = fichier_tennis(j)
    if not f.exists():
        return []
    sauvegarde = f.with_name(f"{j}.avant-gel-{jusqua.replace(':', '')}.json")
    shutil.copy(f, sauvegarde)
    lancer([str(SCRIPTS / "tennis_jour.py"), "--date", j, "--freeze-until", jusqua, "--out", str(f)], timeout=1800)
    a = {m["id"]: m for m in json.loads(sauvegarde.read_text())["matchs"]}
    b = {m["id"]: m for m in json.loads(f.read_text())["matchs"]}
    for i, ma in a.items():
        commence = str(ma.get("statut_match", "")).strip() not in ("", "Not Started")
        mb = b.get(i)
        for k in ("choix", "m", "m_direct"):
            ca, cb = ma.get(k) or {}, (mb or {}).get(k) or {}
            if commence and ca.get("position") and (ca.get("position"), ca.get("cote")) != (cb.get("position"), cb.get("cote")):
                shutil.copy(sauvegarde, f)  # comme freeze.sh : on ne publie pas un gel qui perd une position
                raise Echec(f"gel tennis annulé : {ma.get('joueur_1')} perdrait sa position {k}")
    return tennis_vers_base(store, j, f"tennis-gel-{jusqua}")


# ---------------------------------------------------------------- le M (fil « Créer l'agent mathématicien »)

def _f(store: Store, coll: str, doc: str, travail: Path) -> str:
    """Chemin du document de la base, ou d'un fichier vide {} s'il n'existe pas (les scripts acceptent un doc absent)."""
    p = fichier_doc(store, coll, doc)
    if p:
        return str(p)
    vide = travail / f"vide-{coll}-{doc}.json"
    vide.write_text("{}")
    return str(vide)


def _depuis(store: Store, coll: str, doc: str, f: Path, etape: str) -> list[str]:
    if f.exists():
        try:
            if store.set(coll, doc, json.loads(f.read_text()), etape).get("ecrit"):
                return [f"{coll}/{doc}"]
        except ValueError:
            pass
    return []


def passage_m(store: Store, t: datetime | None = None) -> list[str]:
    """Passage horaire de :50 (tâche « Sélection test + Top 5 des acteurs »), étapes A, S et B."""
    t = t or maintenant()
    loc = t.astimezone(PARIS)
    h = loc.hour
    D = jour_paris(t, 6)
    Y = veille(D)
    w = dossier("m")
    ecrits: list[str] = []
    jour_construit = 9 <= h <= 23
    # A) sélection test, seulement à 9:50, 13:50, 17:50, 19:50, 22:50
    test = fichier_doc(store, "selection_telegram", D)
    if h in (9, 13, 17, 19, 22):
        f1, f2 = w / "f1.json", w / "f2.json"
        args = [str(SCRIPTS / "selection_telegram.py"), "passage", "--jour", D, "--ligues",
                "" if D >= UN_ACTEUR_DU else LIGUES_TEST,
                "--tennis", _f(store, "tennis", D, w), "--basket", _f(store, "basket_pronostic", D, w), "--out", str(f1)]
        if test:
            args[4:4] = ["--doc", str(test)]
        lancer(args)
        lancer([str(SCRIPTS / "selection_telegram.py"), "regler", "--doc", str(f1), "--out", str(f2)])
        ecrits += _depuis(store, "selection_telegram", D, f2, "m-test")
        _copie(f2, "selection-telegram", D)
    if fichier_doc(store, "selection_telegram", Y):  # la veille, si encore des positions non réglées (NBA tardive)
        fy = w / "fy.json"
        lancer([str(SCRIPTS / "selection_telegram.py"), "regler", "--doc", str(store.chemin("selection_telegram", Y)),
                "--out", str(fy)])
        ecrits += _depuis(store, "selection_telegram", Y, fy, "m-test")
    test = fichier_doc(store, "selection_telegram", D)
    # S) Sur-mesure en test, basket et tennis
    sm = w / "sm.json"
    if jour_construit and D < UN_ACTEUR_DU:
        args = [str(SCRIPTS / "surmesure_basket.py"), "passage", "--jour", D, "--basket",
                _f(store, "basket_pronostic", D, w), "--out", str(sm)]
        if fichier_doc(store, "surmesure_basket", D):
            args[6:6] = ["--doc", str(store.chemin("surmesure_basket", D))]
        lancer(args)
    elif fichier_doc(store, "surmesure_basket", D):
        lancer([str(SCRIPTS / "surmesure_basket.py"), "regler", "--doc", str(store.chemin("surmesure_basket", D)),
                "--out", str(sm)])
    ecrits += _depuis(store, "surmesure_basket", D, sm, "m-surmesure")
    _copie(sm, "surmesure-basket", D)
    if jour_construit and D >= UN_ACTEUR_DU:  # Sur-mesure en test tennis arrêté : on règle seulement le figé
        for jt in (D, Y):
            if fichier_doc(store, "surmesure_tennis", jt):
                st = w / f"st-{jt}.json"
                lancer([str(SCRIPTS / "surmesure_tennis.py"), "regler", "--doc", str(store.chemin("surmesure_tennis", jt)),
                        "--out", str(st)])
                ecrits += _depuis(store, "surmesure_tennis", jt, st, "m-surmesure")
    elif jour_construit:
        st = w / "st.json"
        args = [str(SCRIPTS / "surmesure_tennis.py"), "passage", "--jour", D, "--tennis", _f(store, "tennis", D, w),
                "--out", str(st)]
        if fichier_doc(store, "surmesure_tennis", D):
            args[6:6] = ["--doc", str(store.chemin("surmesure_tennis", D))]
        lancer(args)
        ecrits += _depuis(store, "surmesure_tennis", D, st, "m-surmesure")
        _copie(st, "surmesure-tennis", D)
        if h == 9 and fichier_doc(store, "surmesure_tennis", Y):
            sty = w / "sty.json"
            lancer([str(SCRIPTS / "surmesure_tennis.py"), "regler", "--doc", str(store.chemin("surmesure_tennis", Y)),
                    "--out", str(sty)])
            ecrits += _depuis(store, "surmesure_tennis", Y, sty, "m-surmesure")
    # B) Top 5 par acteur et Version finale (9:50-23:50) ; à 9:50 aussi la veille
    if jour_construit:
        for jour in ([D, Y] if h == 9 else [D]):
            for coll, extra, dossier_copie in (("top5", [], "top5"), ("version_finale", ["--finale"], "version-finale")):
                o = w / f"{coll}-{jour}.json"
                args = [str(SCRIPTS / "top5.py"), *extra, "--jour", jour, "--registre", _f(store, "registre", jour, w),
                        "--specialistes", _f(store, "specialistes", jour, w),
                        "--test", _f(store, "selection_telegram", jour, w), "--out", str(o)]
                if fichier_doc(store, coll, jour):
                    args += ["--prev", str(store.chemin(coll, jour))]
                lancer(args)
                lancer([str(SCRIPTS / "top5_live.py"), "--jour", jour, "--top5", str(o), "--out", str(o)], timeout=250)
                ecrits += _depuis(store, coll, jour, o, "m-top5")
                _copie(o, dossier_copie, jour)
        try:
            n = sum(len(v.get("positions", [])) for sp in ("foot", "basket", "tennis")
                    for v in (store.get("top5", D) or {}).get(sp, {}).values() if isinstance(v, dict))
            pouls(store, "top5", "top5", "mathematicien", n, True)
        except Exception as e:  # noqa: BLE001
            pouls(store, "top5", "top5", "mathematicien", 0, False, e)
    shutil.rmtree(w, True)
    return ecrits


def _copie(f: Path, sous_dossier: str, jour: str) -> None:
    """Copie de sauvegarde dans le dossier des fichiers, comme save.sh."""
    if f.exists():
        d = FICHIERS / sous_dossier
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, d / f"{jour}.json")


def non_regle(doc: dict | None) -> bool:
    """Une liste du M a-t-elle encore des positions sans résultat ?"""
    if not doc:
        return False
    for sp in ("foot", "tennis", "basket"):
        for v in (doc.get(sp) or {}).values():
            if isinstance(v, dict) and any(not p.get("resultat") for p in v.get("positions") or []):
                return True
    return False


# ---------------------------------------------------------------- spécialistes (fil « Créer un agent spécialiste »)

def specialistes(store: Store, t: datetime | None = None) -> list[str]:
    t = t or maintenant()
    J = jour_paris(t, 4)
    ecrits = []

    def une_passe(jour: str) -> None:
        page = vue(store, {"matchs": lambda d: str(d.get("jour")) == jour,
                           "basket_matchs": lambda d: str(d.get("jour")) == jour, "specialistes": None})
        out = dossier("spec")
        lancer([str(SCRIPTS / "specialistes.py"), "--page", str(page), "--out", str(out), "--jour", jour,
                *(["--no-tennis"] if jour >= SPECIALISTE_TENNIS_ARRETE_DU else [])], timeout=1800)
        for f in sorted((out / "specialistes").glob("*.json")):
            if f.stem in (jour, "bilan"):
                ecrits.extend(_depuis(store, "specialistes", f.stem, f, "specialistes"))
        shutil.rmtree(page, True), shutil.rmtree(out, True)

    h = t.astimezone(PARIS).hour
    hier = veille(J)
    dh = store.get("specialistes", hier) or {}
    if 4 <= h < 12 and any(isinstance(c, dict) and c.get("pari") and not c.get("resultat")
                           for sp in ("foot", "tennis") for c in (dh.get(sp) or {}).values()):
        une_passe(hier)
    une_passe(J)
    return ecrits


# ---------------------------------------------------------------- publication et sauvegarde

def publier(store: Store, cible: Path) -> int:
    """Exporte la base pour le site : un fichier par collection publique, cible/data/<collection>.json.

    Les collections privées (rapports, prevente, carnet_compte, _plan) ne sont jamais publiées."""
    prives = {"rapports", "prevente", "carnet_compte", "data"}
    d = cible / "data"
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    index = {}
    for coll in store.collections():
        if coll in prives or coll.startswith("_"):
            continue
        docs = store.lister(coll)
        texte = json.dumps(docs, ensure_ascii=False, separators=(",", ":"))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
        with os.fdopen(fd, "w") as fh:
            fh.write(texte)
        os.chmod(tmp, 0o644)
        os.replace(tmp, d / f"{coll}.json")
        index[coll] = len(docs)
        n += 1
    (d / "index.json").write_text(json.dumps({"maj": maintenant().isoformat(timespec="seconds"),
                                             "collections": index}))
    return n
