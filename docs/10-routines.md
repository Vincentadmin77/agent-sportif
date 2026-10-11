# Routines automatiques (page de résultats)

Les passages de l'agent sont lancés par des routines Claude qui réveillent la session du fil
« Première analyse réelle ». La page de résultats est l'artifact
https://claude.ai/artifact/6qUcoCoWxoAamzK8C7fVmP (base `matchs`, document `etat/agent`).

## Rythme (règle du propriétaire)
- Uniquement les matchs du jour (heure de Paris).
- **Onglet « Mon pronostic »** : chaque `prematch` (et `scores --final`) écrit aussi `OUT/pronostic/<jour>.json`, à écrire
  dans la collection `pronostic` (doc_id = le jour, version gardée par `mirror_sync.py ... pronostic/<jour>=<version>`).
  Les 3 meilleures cotes du jour et trois tickets (`sportai/services/pronostic.py`), règles de Vincent du 11/10 :
  safe = cote totale > 1,50 avec la plus forte probabilité ; semi safe = 2 paris au plus, cote > 1,90 ; fun = 3 paris au
  plus, cote > 2,50 (à chaque fois la combinaison la plus probable, différente des tickets précédents). Ticket vide
  si rien de correct ; un ticket est figé au passage 45 min avant son premier match (`fige`), les trouvailles suivantes vont dans `bonus` (listes par type). `agent_pass.py pronostic --page P --out O`
  le refait sans appel d'API. Il écrit aussi `OUT/stats/bilan.json` (collection `stats`, doc `bilan`) : onglet « Résultats et
  statistiques » (pronostics validés / non validés, taux global et par type) et bankroll (`sportai/services/bilan.py`).
  Bankroll : réglage `reglages/bankroll` (départ, objectif 100 000, mise par ticket : safe 3 %, semi safe 2 %, fun 1 % de
  la bankroll du début de journée) ; le `--page` donné au passage doit contenir `matchs`, `pronostic`, `stats` et
  `reglages` et `joueur` (ArtifactData `list` de chaque collection avec le même `out_dir`), sinon les tickets figés et les mises sont perdus.
- **Agent joueur** (`sportai/services/joueur.py`, fil « Agent joueur pro ») : à chaque écriture de « Mon pronostic », il décide
  quels tickets jouer et combien miser (réglage `reglages/joueur`). Il écrit `OUT/joueur/<jour>.json` et `OUT/joueur/etat.json`,
  à écrire dans la collection `joueur` (doc_id = nom du fichier). Les mises des tickets et la bankroll de stats/bilan viennent de lui.
- **Score final estimé** : chaque match reçoit `score_estime` (score le plus probable du modèle Dixon-Coles et sa probabilité),
  marqué `resultat` « reussi » ou « rate » quand le match se termine (direct ou scores).
- **Modèle de buts du jour** : avant l'analyse du matin, `agent_pass.py goals-refresh` (≈ 6 requêtes API-Football, ~1 min).
  Il réajuste le modèle de buts enrichi (docs/11-backtest-buts.md) et l'enregistre dans `state/goals_model.json`. Sans fichier
  du jour, les passages calculent les buts avec Dixon-Coles seul, et rien n'est validé. Plus/moins de 1,5 et 3,5 buts calculés par
  ce modèle sont « validés (fragile) » ; « les deux équipes marquent » et 2,5 restent non validés.
- **Analyse du matin à 8 h** : tous les matchs du jour d'un coup (`agent_pass.py prematch --matin ...`, 1 crédit par championnat,
  sans `--goals`), pour que la page soit remplie à l'ouverture. Chaque match reçoit un `scenario` : buts attendus de chaque
  équipe, chance que chacune marque au moins un but (avantage du terrain du championnat compris).
- Passage **H−3** (3 h avant le coup d'envoi) puis **H−45** (45 min avant : composition sortie, cotes qui bougent).
  À H−45, chaque sélection indique le mouvement du marché depuis H−3.
- **Direct** (API-Football PRO, 7 500 requêtes par jour) : des étapes toutes les 15 min à partir du coup d'envoi (re)lancent
  une boucle toutes les 2 min jusqu'à la fin des matchs (une boucle en arrière-plan meurt si la session s'endort) : `backend/scripts/live_cycle.sh` (avant la 1re boucle : ArtifactData `list` de `matchs` avec
  `out_dir=/tmp/claude-0/mirror` et versions dans `/tmp/claude-0/versions.json`), puis le `batch` imprimé, puis mise à jour des
  versions, puis Bash en arrière-plan `sleep 100; live_cycle.sh`. Chaque document reçoit minute, score, cartons rouges, tirs,
  possession, xG, probabilités recalculées (champ `direct`) et le règlement des sélections à la fin. 1 + 2 requêtes par match.
- **Final** : une étape 2 h 15 après le dernier coup d'envoi du jour (scores finaux via The Odds API, 2 crédits par championnat) ; inutile quand l'étape direct de 115 min a vu le match terminé.
- Planning : une routine quotidienne à 7 h 50 (Paris) fait l'analyse du matin, puis lit le calendrier (`agent_pass.py plan`, gratuit) et
  programme une routine ponctuelle par étape. Le calendrier est celui de la saison complète de chaque championnat
  (API-Football, 1 requête par championnat, rafraîchi toutes les 6 h), gardé dans
  `/mnt/project-files/agent-sportif/data/calendrier/<championnat>.json` ; il donne les matchs du jour et leurs heures
  (seuls les matchs du jour sont analysés). Si API-Football ne répond pas, `plan` reprend la liste de The Odds API.

## Préparer une session neuve
Une routine déclenchée depuis la page (ou depuis une autre session) démarre dans une session neuve, sans le dépôt.
```bash
R=/home/user/agent-sportif; B=/mnt/project-files/agent-sportif/agent-sportif.git.bundle
if [ ! -f $R/backend/scripts/agent_pass.py ]; then
  if [ -f $B ]; then git clone -q -b claude/project-thread-mnjit9 $B $R
  else git clone -q -b claude/project-thread-mnjit9 https://github.com/Vincentadmin77/agent-sportif $R; fi
fi
```
Le clone GitHub demande d'abord `add_repo` (Vincentadmin77/agent-sportif) ; la copie `agent-sportif.git.bundle`
du dossier partagé évite cette étape. Après chaque push sur la branche, la régénérer :
`git bundle create /tmp/b.bundle claude/project-thread-mnjit9 main && cp /tmp/b.bundle $B`.

## Exécuter une étape
```bash
cd /home/user/agent-sportif && git pull -q origin claude/project-thread-mnjit9
mkdir -p /tmp/claude-0
python3 -c "import sportai, scipy" 2>/dev/null || (cd backend && NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 pip install -q -e ".[dev]")
rm -rf /tmp/claude-0/page /tmp/claude-0/out
```
1. Lire la page : ArtifactData `list` de `matchs` avec `out_dir=/tmp/claude-0/page` (noter les versions).
2. `cd backend && python3 scripts/agent_pass.py prematch --page /tmp/claude-0/page --out /tmp/claude-0/out FR1 ...`
   (ajouter `--goals` au passage H−45 : « les deux équipes marquent » et nombre de buts, 2 crédits par match,
   affichés avec l'avertissement « marché non validé », jamais sélectionnés), ou `scores --final` pour le final.
   À H−45, l'agent lit aussi les compositions officielles (API-Football) : titulaires, absents notables,
   remplaçants notables (`compositions`) et probabilité de marquer de chaque titulaire (`buteurs.joueurs`, non validé).
3. Écrire `out/matchs/*.json` dans `matchs` (ArtifactData `batch`, `set`, `if_version` = version lue pour un
   document existant) et `out/etat.json` dans `etat/agent`.
   Puis, pour que la boucle du direct ne republie pas l'ancienne version de ces documents :
   `python3 scripts/mirror_sync.py /tmp/claude-0/out <doc_id>=<nouvelle version> ...` (versions rendues par le batch).
   Si une écriture du direct échoue sur la version : relire `matchs` (`list`, `out_dir=/tmp/claude-0/mirror`), refaire
   `versions.json` avec les versions lues, puis relancer le cycle.
4. Ne prévenir Vincent dans le fil que s'il y a une sélection, un résultat de sélection ou une panne.

L'état d'analyse (cotes de H−3 pour la comparaison à H−45) est dans
`/mnt/project-files/agent-sportif/state/agent.db` (variable `STATE_DB`).

## Coût en crédits The Odds API (500 par mois en offre gratuite)
Un passage coûte 1 crédit par championnat (marché résultat seul), un score en direct 1, un score final 2.
Le calendrier (`plan`) est gratuit.

## Choix des championnats et bouton « Analyser maintenant »
- La page écrit `reglages/championnats` = `{"actifs": ["FR1", "ES1", ...]}` (codes FR1 EN1 ES1 DE1 IT1 BR1 UCL).
  UCL : modèle entraîné sur les cinq grands championnats et la coupe d'Europe ensemble (API-Football), clubs vus dans moins
  de 20 matchs écartés ; information seulement, aucune sélection tant qu'un test ne l'a pas validé. Le planning du matin et l'analyse à la
  demande ne traitent que ces championnats (`agent_pass.py plan --competitions ...`). Document absent : tous.
- Le bouton de la page déclenche la routine « analyse à la demande », dont l'id est dans `reglages/routine`.
