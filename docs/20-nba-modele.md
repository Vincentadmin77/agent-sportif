# NBA : modèle et backtest (27/10/2026)

Source : API-Basketball (API-Sports, plan Pro jusqu'au 27/11/2026, même clé que le foot `API_FOOTBALL_KEY`).
NBA = league 12, saison « 2026-2027 » ; WNBA = league 13, saison « 2026 ». Cotes : `/odds?league=12&season=…`
(9 bookmakers internationaux, 85 types de paris, pas Winamax).

Modèle : `backend/sportai/engine/basketball/ratings.py`. Points de chaque équipe = base + avantage du terrain
+ attaque − défense adverse + fatigue (match la veille, repos de 3 jours et plus), régression pondérée dans le
temps (demi-vie 80 jours, ridge 2, choisis sur la seule saison 2023-24). Écart et total de points suivent une loi
normale dont l'écart-type vient des résidus.

Backtest walk-forward (refit tous les 7 jours, matchs passés uniquement), 3 955 matchs de 2023-24 à 2025-26,
`backend/scripts/backtest_nba.py`, résultats `docs/backtest-nba.json` :

| | Modèle | Référence |
|---|---|---|
| Vainqueur, log loss | 0,609 | 0,689 (taux de victoire à domicile) |
| Vainqueur, favori juste | 66 % | |
| Total de points, Brier (ligne = moyenne 30 jours) | 0,232 | 0,250 |
| Écart de points, erreur moyenne | 11,3 pts | 12,8 pts |

Le modèle bat la référence chaque saison. Vainqueur bien calibré (prévu 85 % → réel 85 %).
Total de points trop sûr de lui aux extrêmes (prévu 85 % → réel 73 %) : reste « non validé ».
Pas de cotes historiques : battre une référence n'est pas battre les bookmakers.
