# Basket (NBA, WNBA) : ce qui tourne et quand (27/10/2026)

Codes de championnat : `NBA` et `WNBA`, à côté des codes foot (`agent_pass.py`, `BASKET_COMPETITIONS`).
Source : API-Basketball (`v1.basketball.api-sports.io`, même clé que le foot `API_FOOTBALL_KEY`), connecteur
`backend/sportai/connectors/api_basketball.py`. NBA = ligue 12, saison « 2026-2027 » ; WNBA = ligue 13
(« NBA W » dans le flux), saison 2026. The Odds API n'est jamais appelée pour le basket.

## Journée
La journée basket va de 04:00 à 04:00 (Paris) : un match qui commence entre 00:00 et 04:00 compte pour la veille
(`journee(moment, "basket")`). L'analyse de 8 h du jour J couvre donc les matchs américains de la nuit J → J+1.
Le foot garde sa journée 08:00-02:00. doc_id = `<jour>-<domicile>-<exterieur>` comme le foot, avec `"sport": "basket"`
(les fiches foot portent `"sport": "foot"`).

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
par son propre fournisseur. Un passage basket seul écrit `matchs/*.json`, `pronostic/<jour basket>.json` et
`stats/bilan.json`, comme le foot.

## Modèle et règles (les mêmes que le foot)
- Modèle de points par équipe (`sportai/engine/basketball/ratings.py`, docs/20-nba-modele.md), réajusté à chaque
  passage sur la saison en cours et les deux précédentes (cache partagé `data/api-basketball/games_<nba|wnba>_<saison>.json.gz`,
  saison en cours rechargée toutes les 6 h).
- Validé : vainqueur NBA (prolongation comprise). « Non validé » : total de points, handicaps, points d'une équipe,
  et tous les marchés WNBA (pas de backtest WNBA). Un pari non validé ne peut jamais être la Top confiance.
- Une seule cote retenue par match, cote minimale 1,60 partout, Top confiance du jour seulement parmi les paris
  « Confiance élevée » (validés pour le basket), combinés de 2 comme le foot. Les paris basket entrent dans le même
  `pronostic/<jour>` et le même bilan que le foot.
- Fiche match : probabilités de victoire, points attendus, scénario, score estimé, tous les marchés chiffrés,
  `paris_basket` (les paris proposés à « Mon pronostic »). Aucun nom de bookmaker ni de fournisseur, aucun montant,
  aucun conseil en direct.
