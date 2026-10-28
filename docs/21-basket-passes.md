# Basket (NBA, WNBA) : ce qui tourne et quand (27/10/2026)

Codes de championnat : `NBA` et `WNBA`, à côté des codes foot (`agent_pass.py`, `BASKET_COMPETITIONS`).
Source : API-Basketball (`v1.basketball.api-sports.io`, même clé que le foot `API_FOOTBALL_KEY`), connecteur
`backend/sportai/connectors/api_basketball.py`. NBA = ligue 12, saison « 2026-2027 » ; WNBA = ligue 13
(« NBA W » dans le flux), saison 2026. The Odds API n'est jamais appelée pour le basket.

## Journée
La journée basket va de 04:00 à 04:00 (Paris) : un match qui commence entre 00:00 et 04:00 compte pour la veille
(`journee(moment, "basket")`). L'analyse de 8 h du jour J couvre donc les matchs américains de la nuit J → J+1.
Le foot garde sa journée 08:00-02:00.

## Étapes (même routine que le foot, `agent_pass.py plan` les programme)
| Quand (Paris) | Étape | Commande |
|---|---|---|
| 07:50 | analyse du matin, tous les matchs du jour | `prematch --matin ... NBA WNBA` |
| 07:50 | planning | `plan` (ou `plan --competitions ... NBA`) : 2 requêtes par championnat |
| coup d'envoi − 3 h | passage H−3 | `prematch --page P --out O NBA` |
| coup d'envoi − 45 min | passage H−45 (pari du match figé) | `prematch --page P --out O NBA` |
| coup d'envoi + 0, 30, 60, 90, 120, 150 min | direct léger : score, quart-temps, scores par quart | `live --page P --out O NBA` |
| dernier coup d'envoi + 2 h 30 (≈ 05:00-06:30) | résultats et règlement de la journée basket | `scores --final --page P --out O NBA` |

Pas de passage H−15 pour le basket. Un passage basket analyse tous les matchs du jour pas encore commencés (1 à
2 requêtes pour la liste, 1 par match pour les cotes). Les étapes peuvent mêler foot et basket : chaque sport passe
par son propre fournisseur.

## Collections de la page (séparées du foot)
Le basket n'écrit jamais dans `matchs/`, `pronostic/` ni `stats/` (le pronostic et le bilan foot n'ont plus aucun
pari basket). Il lit `--page/basket_matchs` et `--page/basket_pronostic` (même arborescence) et écrit seulement :
- `out/basket_matchs/<id du match dans le flux>.json` : sport, competition, code_ligue, jour (soirée basket),
  coup_envoi (ISO UTC), domicile, exterieur, statut (a_venir / en_direct / termine), score (`"112-105"` ou null),
  score_estime, proba_1x2, cotes_1x2 (cote médiane du vainqueur), points {total_prevu, ecart_prevu},
  contexte {phrases}, passages {analyse, h3, m45, direct}. Pas de `q1` (non chiffré). En direct et au final :
  score_quarts et direct en plus ; un match reporté ou annulé garde son statut avec `reporte: true`.
- `out/basket_pronostic/<jour>.json` : jour, sport, maj, passage, paris_retenus {<id>: pari retenu ou
  {pari: null, raison}}, selections et combines (vides depuis le 28/10), note.
Un passage basket seul écrit aussi `etat.json` et `manifest.json` (`matchs: []`, `basket_matchs`,
`basket_pronostic`) ; un passage mixte ajoute ces deux listes au manifeste du foot.

## Modèle et règles (les mêmes que le foot)
- Modèle de points par équipe (`sportai/engine/basketball/ratings.py`, docs/20-nba-modele.md), réajusté à chaque
  passage sur la saison en cours et les deux précédentes (cache partagé `data/api-basketball/games_<nba|wnba>_<saison>.json.gz`,
  saison en cours rechargée toutes les 6 h).
- Validé : vainqueur NBA (prolongation comprise). « Non validé » : total de points, handicaps, points d'une équipe,
  et tous les marchés WNBA (pas de backtest WNBA).
- Une seule cote retenue par match, en simple (règles du foot depuis le 28/10 : cote médiane de 1,50 à 2,30, modèle
  et marché d'accord), figée au passage H−45 (ou au coup d'envoi) puis reprise telle quelle de `basket_pronostic`.
  Marchés les plus fiables d'abord (`TIERS`) : vainqueur et handicap, puis 1er quart-temps, puis totaux de points ;
  un marché moins fiable n'est retenu que s'il est au moins 10 points plus sûr (`COUNT_MARGIN`). Plus de Top
  confiance ni de combiné (`SELECTIONS_PER_DAY = 0` côté foot, code gardé).
- Positions joueurs NBA (`services/basket_players.py`, depuis le 28/10) : points, paniers à 3 points, rebonds,
  points + passes, points + rebonds + passes et paliers de points, ajoutées aux candidates de chaque match NBA
  (« non validé », cote 1,50-2,30, valeur ≥ 3 %, écart ≤ 10 points avec les bookmakers, 5 au plus par match, rang 2
  comme les totaux). Les feuilles de match manquantes sont téléchargées à chaque passage (`fetch_boxes`, 40 au plus,
  cache `players_nba_<saison>.json.gz`) et servent au règlement : joueur absent = remboursé. `valide` n'est
  vrai que pour le vainqueur NBA. Hors bilan officiel.
- Règlement : `scores --final` et `live` mettent à jour statut et score de `basket_matchs` et écrivent `resultat`
  (gagne / perdu / rembourse) sur chaque pari retenu de `basket_pronostic`.
- Aucun nom de bookmaker ni de fournisseur, aucun montant, aucun conseil en direct.
