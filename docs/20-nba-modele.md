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

## Joueurs (27/10/2026)

Statistiques par joueur et par match de 2023-24 à aujourd'hui (4 265 matchs, `scripts/fetch_nba_players.py`).
Modèle `sportai/engine/basketball/players.py` : minutes attendues × production par minute (récente, ramenée vers
la moyenne de la ligue), loi binomiale négative. Test 2024-25 à 2026-27 (51 467 lignes joueur), ligne = moyenne
des 10 derniers matchs, `docs/backtest-nba-joueurs.json` :

| Marché | Brier modèle | Référence 0,25 |
|---|---|---|
| Points | 0,242 | 0,250 |
| 3 points (référence taux réel 0,240) | 0,218 | |
| Rebonds | 0,242 | |
| Passes | 0,238 | |
| Points + passes | 0,242 | |
| Points + rebonds + passes | 0,241 | |

Petit avantage partout, mais biais connu : seuls les joueurs ayant joué 10 minutes ou plus sont comptés.
Aucune ligne historique de bookmaker : tout reste « non validé ». Pas de liste des blessés dans la source.
Libellés de cotes API-Basketball à confirmer : « Player Triples » = 3 points, « Player Runs » ≈ rebonds (?).

## Stars (28/10/2026)

Demande du propriétaire : travailler les stars. Trois changements, choisis sur 2023-24 seulement et testés sur
2024-25 à 2026-27, sur les joueurs à 18 points attendus ou plus (8 555 matchs-joueur, ligne = moyenne des 10 derniers) :
- un marqueur (15 points ou plus sur la durée) est ramené vers son propre niveau long terme, plus vers la moyenne de la ligue ;
- adversaire : chaque stat multipliée par (ce que l'adversaire concède / moyenne) ^ 0,5 (`TeamDefense`) ;
- dispersion plus serrée pour les stars (`STAR_DISPERSION`).

| Stars | Brier avant | après |
|---|---|---|
| Points | 0,2420 | 0,2393 |
| Points + rebonds + passes | 0,2445 | 0,2406 |
| Points + passes | 0,2442 | 0,2404 |
| Rebonds | 0,2381 | 0,2371 |
| 3 points | 0,2303 | 0,2293 |

Tous joueurs : inchangé ou mieux (points 0,2420 → 0,2417). Essai rejeté : bonus quand un coéquipier régulier a manqué le
match précédent (absence lue sur la feuille de match) : nettement pire (Brier points 0,241 → 0,246 avec 0,5), car une
absence d'un soir n'annonce pas l'absence suivante. Sans liste des blessés, pas de bonus « coéquipier absent ».
