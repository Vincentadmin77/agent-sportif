"""Le chef d'orchestre : remplace les 19 tâches planifiées Claude par un seul programme qui tourne sur le serveur.

    python -m autonome.orchestre                  # tourne sans fin (service systemd analytrack)
    python -m autonome.orchestre --une-fois       # regarde l'heure, lance ce qui est dû, s'arrête (tests)
    python -m autonome.orchestre --etape matin    # lance une étape à la main (matin, m, specialistes, tennis-matin,
                                                  #   gel-1240, gel-1640, publier)

Horaires (heure de Paris), repris tels quels des tâches Claude du 15/11/2026 :
- 07:50 analyse du matin foot + basket, puis le planning du jour (agent_pass.py plan) : h3, m45, m15, direct, final ;
- direct foot : un cycle toutes les 100 s tant qu'un match foot du jour est en cours ou commence dans les 2 h ;
- 08:05 tennis du matin, puis la boucle du direct tennis ; 12:40 et 16:40 gels tennis ;
- :50 de 9 h à 23 h et de 0 h à 5 h : passage du M (sélection test, Sur-mesure, Top 5, Version finale),
  puis les boucles temps réel du Top 5 et de la Version finale ;
- :51 chaque heure : spécialistes ;
- :25 de 21 h à 5 h : le M en direct basket (boucle de nuit) ;
- après chaque écriture : export du site (au plus une fois par minute) ; chaque nuit : sauvegarde.

Ce qui a été fait est noté dans BASE/_orchestre/<jour>.json : un redémarrage ne relance pas deux fois la même étape.
Tout est écrit dans le journal (stdout -> journalctl -u analytrack) ; une étape en panne n'arrête jamais les autres.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import etapes as E
from .store import Store

BASE = Path(os.getenv("ANALYTRACK_BASE", "/srv/analytrack/base"))
SITE = Path(os.getenv("ANALYTRACK_SITE", "/srv/analytrack/site"))
RATTRAPAGE = timedelta(minutes=30)  # une étape manquée de moins de 30 min (redémarrage) est rattrapée

store = Store(BASE)
verrou_foot = threading.Lock()  # une passe foot/basket à la fois (comme une seule session « Première analyse réelle »)
verrou_m = threading.Lock()
a_publier = threading.Event()
arret = threading.Event()


def log(msg: str) -> None:
    print(f"{datetime.now(E.PARIS):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


_dernieres_alertes: dict[str, float] = {}


def alerte_admin(sujet: str, texte: str) -> None:
    """Panne : une notification sur le téléphone de Vincent (ntfy, sujet NTFY_ADMIN_TOPIC), au plus une par heure et
    par étape. Jamais bloquant ; sans sujet configuré, rien n'est envoyé (le journal et le voyant pouls suffisent)."""
    t = os.getenv("NTFY_ADMIN_TOPIC")
    if not t or time.time() - _dernieres_alertes.get(sujet, 0) < 3600:
        return
    _dernieres_alertes[sujet] = time.time()
    try:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{t}", data=texte[:300].encode(),
                                                      headers={"Title": f"Analytrack : panne {sujet}"}), timeout=15)
    except Exception:  # noqa: BLE001
        pass


def fait(cle: str, jour: str) -> bool:
    return cle in (store.get("_orchestre", jour) or {}).get("fait", {})


def noter(cle: str, jour: str, resultat: str) -> None:
    d = store.get("_orchestre", jour) or {"fait": {}}
    d["fait"][cle] = f"{E.maintenant().isoformat(timespec='seconds')} {resultat}"[:300]
    store.set("_orchestre", jour, d, "orchestre", proteger=False)


def executer(nom: str, fn, *args, verrou: threading.Lock | None = None, pouls_doc: tuple | None = None):
    """Lance une étape dans son propre fil d'exécution ; une panne est notée, jamais propagée."""
    def corps():
        t0 = time.time()
        try:
            if verrou:
                verrou.acquire()
            r = fn(*args)
            log(f"OK {nom} ({time.time() - t0:.0f} s) {str(r)[:300]}")
            if r:
                a_publier.set()
            if pouls_doc:
                E.pouls(store, *pouls_doc, ok=True)
        except Exception as e:  # noqa: BLE001
            log(f"PANNE {nom}: {e}\n{traceback.format_exc()[-800:]}")
            alerte_admin(nom.split()[0], str(e))
            if pouls_doc:
                E.pouls(store, *pouls_doc, ok=False, erreur=str(e))
        finally:
            if verrou and verrou.locked():
                verrou.release()
    th = threading.Thread(target=corps, name=nom, daemon=True)
    th.start()
    return th


# ------------------------------------------------------------------ boucles supervisées (processus de longue durée)

class Boucle:
    """Un script qui tourne en arrière-plan et s'arrête à chaque nouveauté ; on écrit sa sortie puis on le relance."""

    def __init__(self, nom: str):
        self.nom = nom
        self.proc: subprocess.Popen | None = None
        self.fil: threading.Thread | None = None
        self.stop = threading.Event()

    def actif(self) -> bool:
        return bool(self.fil and self.fil.is_alive())

    def demarrer(self, prochain) -> None:
        """prochain() renvoie (commande, après(rc)) pour le tour suivant, ou None pour s'arrêter."""
        if self.actif():
            return
        self.stop.clear()

        def tourner():
            while not self.stop.is_set() and not arret.is_set():
                tour = prochain()
                if not tour:
                    break
                cmd, apres = tour
                try:
                    self.proc = subprocess.Popen([E.PY, *cmd], cwd=E.BACKEND, stdout=subprocess.PIPE,
                                                 stderr=subprocess.STDOUT, text=True)
                    sortie, _ = self.proc.communicate()
                    rc = self.proc.returncode
                except Exception as e:  # noqa: BLE001
                    sortie, rc = str(e), -1
                if self.stop.is_set():
                    break
                try:
                    r = apres(rc, sortie or "")
                    if r:
                        a_publier.set()
                        log(f"{self.nom}: {r}")
                except Exception as e:  # noqa: BLE001
                    log(f"PANNE {self.nom} (écriture): {e}")
                if rc not in (0, None):
                    log(f"{self.nom}: code {rc} {str(sortie)[-200:]}")
                    time.sleep(10)
            log(f"{self.nom}: arrêtée")

        self.fil = threading.Thread(target=tourner, name=self.nom, daemon=True)
        self.fil.start()

    def arreter(self) -> None:
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.fil:
            self.fil.join(60)


boucle_tennis = Boucle("direct tennis")
boucle_top5 = Boucle("temps réel Top 5")
boucle_vf = Boucle("temps réel Version finale")
boucle_basket_nuit = Boucle("M direct basket de nuit")


def demarrer_tennis() -> None:
    j = E.jour_paris()
    fin = datetime.fromisoformat(j).replace(tzinfo=E.PARIS) + timedelta(days=1, hours=2, minutes=30)

    def prochain():
        if E.maintenant() >= fin or not E.fichier_tennis(j).exists():
            return None
        return ([str(E.SCRIPTS / "tennis_direct.py"), "--date", j, "--out", str(E.fichier_tennis(j))],
                lambda rc, s: E.tennis_vers_base(store, j, "direct-tennis"))
    boucle_tennis.demarrer(prochain)


def demarrer_temps_reel(D: str) -> None:
    """Étape L : top5_live.py --boucle 55 sur Top 5 et Version finale, relancées tant que le jour n'est pas réglé."""
    for boucle, coll in ((boucle_top5, "top5"), (boucle_vf, "version_finale")):
        boucle.arreter()
        w = E.TRAVAIL / f"live-{coll}"
        w.mkdir(parents=True, exist_ok=True)

        def prochain(coll=coll, w=w):
            doc = store.get(coll, D)
            if not E.non_regle(doc):
                return None
            (w / "cur.json").write_text(json.dumps(doc, ensure_ascii=False))

            def apres(rc, s, coll=coll, w=w):
                f = w / "out.json"
                if f.exists() and store.set(coll, D, json.loads(f.read_text()), "m-temps-reel").get("ecrit"):
                    E._copie(f, "top5" if coll == "top5" else "version-finale", D)
                    return f"{coll}/{D} mis à jour"
                return None
            return ([str(E.SCRIPTS / "top5_live.py"), "--jour", D, "--top5", str(w / "cur.json"),
                     "--out", str(w / "out.json"), "--boucle", "55"], apres)
        boucle.demarrer(prochain)


def demarrer_basket_nuit() -> None:
    """Le M en direct basket : jour J = date de Paris, la veille avant 6 h ; boucle de 55 min max, relancée à :25."""
    if boucle_basket_nuit.actif():
        return
    J = E.jour_paris(bascule_h=6)
    w = E.TRAVAIL / "basket-nuit"
    w.mkdir(parents=True, exist_ok=True)
    fin = time.time() + 55 * 60

    def prochain():
        reste = (fin - time.time()) / 60
        if reste < 5:
            return None
        cur = w / "cur.json"
        doc = store.get("basket_m_direct", J)
        cur.write_text(json.dumps(doc or {}, ensure_ascii=False))

        def apres(rc, s):
            out = w / "out.json"
            if not out.exists():
                return None
            r = []
            if store.set("basket_m_direct", J, json.loads(out.read_text()), "m-basket-nuit").get("ecrit"):
                r.append(f"basket_m_direct/{J}")
                (E.FICHIERS / "basket-direct").mkdir(parents=True, exist_ok=True)
                shutil.copy(out, E.FICHIERS / "basket-direct" / f"{J}.json")
            reg = store.chemin("registre", J)
            if reg.exists():
                sortie = E.lancer([str(E.SCRIPTS / "basket_direct.py"), "registre", "--jour", J, "--doc", str(out),
                                   "--registre", str(reg), "--out", str(w / "reg.json")])
                if not sortie.strip().startswith("0 ") and store.set(
                        "registre", J, json.loads((w / "reg.json").read_text()), "m-basket-nuit").get("ecrit"):
                    r.append(f"registre/{J}")
            return r
        return ([str(E.SCRIPTS / "basket_direct.py"), "boucle", "--jour", J, "--doc", str(cur), "--out",
                 str(w / "out.json"), "--minutes", f"{reste:.0f}"], apres)
    boucle_basket_nuit.demarrer(prochain)


# ------------------------------------------------------------------ direct foot (boucle de 100 s)

direct_foot: dict = {"fil": None, "comps": set()}


def _foot_a_suivre(jour: str) -> bool:
    t = E.maintenant()
    for d in store.lister("matchs").values():
        if str(d.get("jour")) != jour or d.get("sport") == "basket":
            continue
        if d.get("statut") == "en_direct":
            return True
        try:
            ko = datetime.fromisoformat(str(d.get("coup_envoi")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if d.get("statut") != "termine" and ko - timedelta(hours=2) <= t <= ko + timedelta(hours=3):
            return True
    return False


def demarrer_direct_foot(comps: list[str]) -> None:
    direct_foot["comps"] |= set(comps)
    if direct_foot["fil"] and direct_foot["fil"].is_alive():
        return

    def tourner():
        jour = E.jour_foot()
        while not arret.is_set() and _foot_a_suivre(jour):
            try:
                with verrou_foot:
                    r = E.cycle_direct_foot(store, sorted(direct_foot["comps"]))
                n = sum(1 for d in store.lister("matchs").values()
                        if str(d.get("jour")) == jour and d.get("statut") == "en_direct")
                E.pouls(store, "foot_direct", "direct", "guetteuse", n, True)
                if r:
                    a_publier.set()
            except Exception as e:  # noqa: BLE001
                msg = "quota API-Football atteint" if "quota" in str(e).lower() or "429" in str(e) else str(e)
                E.pouls(store, "foot_direct", "direct", "guetteuse", 0, False, msg)
                log(f"PANNE direct foot: {e}")
            arret.wait(100)
        direct_foot["comps"] = set()
        log("direct foot : plus de match à suivre")

    direct_foot["fil"] = threading.Thread(target=tourner, name="direct foot", daemon=True)
    direct_foot["fil"].start()


# ------------------------------------------------------------------ étapes du planning (agent_pass.py plan)

def etape_planning(st: dict) -> None:
    kind, comps = st["kind"], st["competitions"]
    foot = [c for c in comps if c not in E.BASKET]
    basket = [c for c in comps if c in E.BASKET]
    if kind in ("h3", "m45", "m15"):
        if kind == "m15" and (E.credits_odds() or 10**9) < E.SEUIL_CREDITS:
            log("m15 sauté : crédits The Odds API sous 100")
            return
        pd = ("basket", kind if kind != "m45" else "h45", "enqueteur", E.nb_matchs_du_jour(store, "basket_matchs",
                                                                                         E.jour_foot())) if basket else None
        executer(f"{kind} {' '.join(comps)}", E.passe_avant_match, store, comps, kind, kind == "m45",
                 verrou=verrou_foot, pouls_doc=pd)
    elif kind == "live":
        if foot:
            demarrer_direct_foot(foot)
        if basket:
            executer(f"direct basket {' '.join(basket)}", E.direct_basket, store, basket, verrou=verrou_foot)
    elif kind == "final":
        pd = ("basket", "final", "archiviste", 0) if basket else None
        executer(f"final {' '.join(comps)}", E.final, store, comps, verrou=verrou_foot, pouls_doc=pd)


# ------------------------------------------------------------------ horloge

def taches_fixes(loc: datetime) -> list[tuple[str, callable]]:
    """Les tâches à heure fixe dues à cette minute (heure de Paris)."""
    h, m = loc.hour, loc.minute
    t = []
    if (h, m) == (7, 50):
        t.append(("matin", lambda: executer("analyse du matin", E.matin, store, verrou=verrou_foot,
                                            pouls_doc=("foot", "matin", "enqueteur", 0))))
    if (h, m) == (8, 5):
        def tennis():
            executer("tennis du matin", lambda: (E.tennis_matin(store), demarrer_tennis())[0],
                     pouls_doc=("tennis", "matin", "enqueteur", 0))
        t.append(("tennis-matin", tennis))
    for hh, jusqua in ((12, "16:00"), (16, "23:59")):
        if (h, m) == (hh, 40):
            def gel(jusqua=jusqua):
                def corps():
                    boucle_tennis.arreter()
                    try:
                        return E.tennis_gel(store, jusqua)
                    finally:
                        demarrer_tennis()
                executer(f"gel tennis {jusqua}", corps, pouls_doc=("tennis", f"gel {jusqua}", "mathematicien", 0))
            t.append((f"gel-{hh}40", gel))
    if m == 50 and (h <= 5 or h >= 9):
        def m_passage():
            def corps():
                r = E.passage_m(store)
                demarrer_temps_reel(E.jour_paris(bascule_h=6))
                return r
            executer("passage du M", corps, verrou=verrou_m)
        t.append((f"m-{h:02d}50", m_passage))
    if m == 51:
        t.append((f"spec-{h:02d}51", lambda: executer("spécialistes", E.specialistes, store)))
    if m == 25 and (h >= 21 or h <= 5):
        t.append((f"basket-nuit-{h:02d}25", demarrer_basket_nuit))
    return t


def tic() -> None:
    t = E.maintenant()
    loc = t.astimezone(E.PARIS)
    jour = str(loc.date())
    taches = taches_fixes(loc)
    # Rattrapage après un redémarrage : l'analyse du matin (jusqu'à midi) et le tennis du matin (jusqu'à 12:30)
    hm = (loc.hour, loc.minute)
    if (7, 50) < hm < (12, 0) and not fait("matin", jour):
        taches += [c for c in taches_fixes(loc.replace(hour=7, minute=50)) if c[0] == "matin"]
    if (8, 5) < hm < (12, 30) and not fait("tennis-matin", jour):
        taches += [c for c in taches_fixes(loc.replace(hour=8, minute=5)) if c[0] == "tennis-matin"]
    for cle, lance in taches:
        if not fait(cle, jour):
            noter(cle, jour, "lancé")
            lance()
    plan = store.get("_plan", E.jour_foot(t)) or {}
    for st in plan.get("steps", []):
        at = datetime.fromisoformat(st["at"])
        cle = f"plan {st['at']} {st['kind']} {','.join(st['competitions'])}"
        if at <= t < at + RATTRAPAGE and not fait(cle, E.jour_foot(t)):
            noter(cle, E.jour_foot(t), "lancé")
            etape_planning(st)


def publieur() -> None:
    while not arret.is_set():
        if a_publier.wait(30):
            a_publier.clear()
            try:
                E.publier(store, SITE)
            except Exception as e:  # noqa: BLE001
                log(f"PANNE publication: {e}")
            arret.wait(60)


ETAPES_MAIN = {
    "matin": lambda: E.matin(store),
    "m": lambda: E.passage_m(store),
    "specialistes": lambda: E.specialistes(store),
    "tennis-matin": lambda: E.tennis_matin(store),
    "gel-1240": lambda: E.tennis_gel(store, "16:00"),
    "gel-1640": lambda: E.tennis_gel(store, "23:59"),
    "publier": lambda: E.publier(store, SITE),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--une-fois", action="store_true")
    ap.add_argument("--etape", choices=sorted(ETAPES_MAIN))
    a = ap.parse_args()
    if a.etape:
        print(json.dumps(ETAPES_MAIN[a.etape](), ensure_ascii=False, default=str)[:2000])
        return
    signal.signal(signal.SIGTERM, lambda *_: arret.set())
    log(f"Analytrack autonome : base {BASE}, site {SITE}")
    threading.Thread(target=publieur, name="publication", daemon=True).start()
    # Au démarrage : reprendre les boucles qui devraient tourner (redémarrage du serveur en pleine journée)
    loc = E.maintenant().astimezone(E.PARIS)
    if 8 <= loc.hour or loc.hour < 2:
        if E.fichier_tennis(E.jour_paris()).exists():
            demarrer_tennis()
    D = E.jour_paris(bascule_h=6)
    if E.non_regle(store.get("top5", D)) or E.non_regle(store.get("version_finale", D)):
        demarrer_temps_reel(D)
    while not arret.is_set():
        try:
            tic()
        except Exception as e:  # noqa: BLE001
            log(f"PANNE horloge: {e}")
        if a.une_fois:
            time.sleep(5)
            break
        arret.wait(20)
    for b in (boucle_tennis, boucle_top5, boucle_vf, boucle_basket_nuit):
        b.arreter()


if __name__ == "__main__":
    main()
