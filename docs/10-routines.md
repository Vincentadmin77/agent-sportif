# Routines automatiques (page de résultats)

Les passages de l'agent sont lancés par des routines Claude qui réveillent la session du fil
« Première analyse réelle ». La page de résultats est l'artifact
https://claude.ai/artifact/6qUcoCoWxoAamzK8C7fVmP (base `matchs`, document `etat/agent`).

## Rythme (règle du propriétaire)
- Uniquement les matchs du jour (heure de Paris).
- Passage **H−3** (3 h avant le coup d'envoi) puis **H−45** (45 min avant : composition sortie, cotes qui bougent).
  À H−45, chaque sélection indique le mouvement du marché depuis H−3.
- **Direct** (API-Football PRO, 7 500 requêtes par jour) : étapes 30, 75 et 115 min après chaque coup d'envoi, `agent_pass.py live --page P --out O <championnats>` : minute, score, cartons rouges, tirs, possession, xG, probabilités recalculées en direct (champ `direct` des documents) et réglement des sélections à la fin. Environ 1 + 2 requêtes par match suivi, aucun crédit de cotes.
- **Final** : une étape 2 h 15 après le dernier coup d'envoi du jour (scores finaux via The Odds API, 2 crédits par championnat) ; inutile quand l'étape direct de 115 min a vu le match terminé.
- Planning : une routine quotidienne à 8 h 52 (Paris) lit le calendrier (`agent_pass.py plan`, gratuit) et
  programme une routine ponctuelle par étape.

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
3. Écrire `out/matchs/*.json` dans `matchs` (ArtifactData `batch`, `set`, `if_version` = version lue pour un
   document existant) et `out/etat.json` dans `etat/agent`.
4. Ne prévenir Vincent dans le fil que s'il y a une sélection, un résultat de sélection ou une panne.

L'état d'analyse (cotes de H−3 pour la comparaison à H−45) est dans
`/mnt/project-files/agent-sportif/state/agent.db` (variable `STATE_DB`).

## Coût en crédits The Odds API (500 par mois en offre gratuite)
Un passage coûte 1 crédit par championnat (marché résultat seul), un score en direct 1, un score final 2.
Le calendrier (`plan`) est gratuit.

## Choix des championnats et bouton « Analyser maintenant »
- La page écrit `reglages/championnats` = `{"actifs": ["FR1", "ES1", ...]}` (codes FR1 EN1 ES1 DE1 IT1 BR1 UCL).
  UCL : matchs listés mais pas analysés (pas d'historique entre championnats sur les sources gratuites). Le planning du matin et l'analyse à la
  demande ne traitent que ces championnats (`agent_pass.py plan --competitions ...`). Document absent : tous.
- Le bouton de la page déclenche la routine « analyse à la demande », dont l'id est dans `reglages/routine`.
