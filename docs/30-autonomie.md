# Analytrack autonome : le site et les calculs sans Claude

Préparé le 15/11/2026 (fil « Rendre le site autonome sans Claude »), à la demande de Vincent. **Rien n'est en ligne** :
le déploiement attend l'avis de l'avocat et la création de la société. Aucune règle, aucun calcul, aucun seuil n'a
changé : le serveur lance les mêmes scripts du dépôt, avec les mêmes options, aux mêmes heures.

## 1. Ce qui dépendait de Claude (inventaire du 15/11/2026)

| Rôle | Où ça tournait | Remplacé par |
|---|---|---|
| Base du site (matchs, pronostic, top5, registre…) | base de l'artifact claude.ai (outil ArtifactData) | dossier de fichiers `BASE/<collection>/<doc>.json` (`autonome/store.py`) |
| Page du site | artifact claude.ai `6qUcoCoWxoAamzK8C7fVmP` | mêmes fichiers servis par nginx + `site/claude-autonome.js` |
| 07:50 analyse du matin + planning (h3, m45, m15, direct, final) | tâche « analyse du matin et planning » + ~10 tâches ponctuelles par jour (fil Première analyse réelle) | `etapes.matin` puis le planning lu dans `BASE/_plan/<jour>` |
| Boucle du direct foot (100 s) | `cyc.sh` + `livefilt.py` relancés par Claude | `etapes.cycle_direct_foot` dans un fil d'exécution |
| Direct basket (toutes les 30 min) | étapes « direct » des tâches ponctuelles | `etapes.direct_basket` |
| Registre (après chaque pronostic) | Claude, à la main de chaque routine | `etapes.registre` (automatique après chaque écriture) |
| Tennis : 08:05, gels 12:40 / 16:40, boucle du direct | 3 tâches + `direct_loop.sh`, `freeze.sh`, `registre.sh` (scratchpad du fil tennis) | `etapes.tennis_matin`, `etapes.tennis_gel` (même contrôle que freeze.sh), boucle supervisée |
| Le M : sélection test, Sur-mesure, Top 5, Version finale (:50) | tâche horaire du fil Mathématicien + `run.sh`, `pair.sh` (scratchpad) | `etapes.passage_m` + boucles `top5_live.py --boucle 55` |
| Le M en direct basket de nuit (:25, 21 h-5 h) | tâche du fil Mathématicien | boucle supervisée `basket_direct.py boucle` |
| Spécialistes (:51) | tâche horaire du fil Spécialistes | `etapes.specialistes` |
| Voyants « pouls » | écrits par Claude | `etapes.pouls` après chaque étape |
| Alertes téléphone du M (ntfy) | scripts du dépôt | inchangé (scripts du dépôt) |
| Messages dans les fils, bilans du lundi et du mois | Claude | **restent à Claude** (lecture des données, voir §4) : ils ne font pas tourner le site |
| Bouton « analyse à la demande » | `fire_trigger` vers Claude | retiré du site autonome |

Toutes les branches de travail ont été réunies dans `claude/project-thread-ky3kjz` (mnjit9 + houjqf + ogj4vv ;
dcs901 y est déjà) ; les outils du scratchpad sont sauvegardés dans
`/mnt/project-files/agent-sportif/routines/outils/{tennis,m}/`.

## 2. Ce qui tourne sur le serveur

- `autonome/orchestre.py` : le chef d'orchestre (service systemd `analytrack`). Horaires dans son en-tête.
  Ce qui a été lancé est noté dans `BASE/_orchestre/<jour>` : un redémarrage ne relance rien deux fois ; une étape
  manquée de moins de 30 min est rattrapée, l'analyse du matin jusqu'à midi.
- `autonome/store.py` : la base en fichiers. Garde-fou : une position figée (`fige: true`) garde son choix (pari,
  position, cote, marché, match) quoi que renvoie une passe ; seuls ses champs de résultat évoluent ; elle ne peut pas
  disparaître. Plus aucun `joueur/*`. Journal de chaque écriture dans `BASE/_journal.log`.
- `autonome/etapes.py` : une fonction par étape ; `publier` exporte la base vers `SITE/data/<collection>.json`
  (au plus une fois par minute ; jamais `rapports`, `prevente`, `carnet_compte`).
- `site/claude-autonome.js` : donne à la page les mêmes outils que claude.ai (base, visiteur, téléchargements), mais
  lus dans `data/`. La page n'est pas réécrite (`autonome/exporter_site.py` ajoute seulement cette balise).
- Sauvegarde toutes les heures (`deploy/autonome/sauvegarde.sh`) : archive datée gardée 30 jours, et copie dans la
  branche privée `donnees` du dépôt GitHub.
- Mise à jour du code chaque nuit à 06:30 (`mise-a-jour.sh`) : seulement la branche validée, tests réussis, sinon
  l'ancienne version reste.
- Panne d'une étape : journal (`journalctl -u analytrack`), voyant `pouls` à faux, et notification ntfy sur le sujet
  admin (`NTFY_ADMIN_TOPIC`), au plus une par heure et par étape.

## 3. Vérifications faites le 15/11/2026 (copie de la base du jour)

- Registre du 15/11 recalculé par le serveur sur la copie de la base : identique à celui de la page, à part les heures
  de mise à jour et le numéro de version du code.
- Version finale du 15/11 : 22 positions sur 22 identiques. Top 5 du 15/11 : 45 positions, mêmes positions figées ; les
  4 seules différences sont des positions tennis non figées du spécialiste, mises à jour par son passage de 17:51,
  après le Top 5 de 17:50 (normal).
- Site exporté ouvert dans un navigateur sans Claude : chiffres et listes affichés depuis les fichiers (507 positions,
  67 %), aucune erreur.
- Tests : `python -m pytest autonome/tests`.
- Non testé ici (appels d'API payants ou quota) : les passages avant-match, direct et final contre les vraies API. Ils
  seront testés sur le serveur en parallèle de Claude pendant au moins 3 jours avant la bascule (§5).

## 4. Garder la main pour Claude (exigence de Vincent du 15/11)

- **Voir** : la branche `donnees` du dépôt privé (toutes les heures : base complète, journal des 24 h, voyants) ;
  Claude y lit ratios, positions et pannes comme aujourd'hui, et fait les bilans du lundi et du mois.
- **Agir** : accès SSH au serveur avec une clé dédiée (gardée dans les secrets de l'environnement Claude, jamais dans le
  chat) ; `python -m autonome.admin etat | lire | ratios | retirer | ecrire --motif "…"`, chaque action journalisée.
- **Changer le code** : comme aujourd'hui, par GitHub ; le serveur prend la nouvelle version la nuit (ou à la demande).
- **Abonnés plus tard** (inscriptions, résiliations, questions, mails) : même serveur, un service à part
  (`/api/…`, déjà prévu par `claude-autonome.js` pour le carnet et la prévente), même accès pour Claude.

## 5. Mise en place (le jour où Vincent dit go, après l'avocat et la société)

1. Vincent commande un VPS chez IONOS (Debian 12, 2 vCores, 4 Go de RAM, 80 Go ou plus) et un nom de domaine.
2. `bash deploy/autonome/installer.sh` ; remplir `/srv/analytrack/cles.env` (mêmes clés qu'aujourd'hui).
3. Copier `/mnt/project-files/agent-sportif/{data,state,tennis}` vers `/srv/analytrack/fichiers/`.
4. Claude exporte la base de l'artifact (ArtifactData list de chaque collection, toutes les pages) ;
   `python -m autonome.importer_base DUMP /srv/analytrack/base`.
5. `python -m autonome.exporter_site site/source /srv/analytrack/site` (avec la dernière version de la page).
6. **Rodage en parallèle, au moins 3 jours** : le serveur tourne avec ses propres clés de test ou un quota réservé ;
   chaque soir Claude compare ses documents à ceux de la page. Pendant ce temps Claude reste la référence.
7. Bascule : arrêt des tâches Claude, dernier import, `systemctl start analytrack`, puis le domaine pointé vers le
   serveur et `certbot --nginx` (HTTPS). Mise en ligne publique seulement sur « ok pour mettre en ligne » de Vincent.

Attention quotas pendant le rodage : API-Football 7 500 requêtes/jour partagées, The Odds API payant. Deux systèmes
qui tournent en même temps consomment deux fois : prévoir un rodage limité (ex. le serveur sans `--goals` et sans m15)
ou une deuxième clé de test.
