# Cotes françaises uniquement (2026-10-28)

Vincent, 2026-10-28 : « relance et fixe les analyses sur site français ». Une position n'est retenue que sur une cote
qu'un joueur en France peut prendre, chez un opérateur agréé ANJ.

- Source : The Odds API, région `fr` (clés `winamax_fr`, `betclic_fr`, `unibet_fr`, `pmu_fr`, `netbet_fr`).
  API-Football et API-Basketball n'ont aucun site français (leur Unibet et NetBet sont les versions internationales).
- Foot (services/runs.py) : à chaque passage, 1X2 et totaux de buts français ajoutés (2 crédits par championnat).
  La meilleure cote retenue est la meilleure cote française (prematch._best_prices) ; la chance « marché » sans marge
  reste calculée sur tous les bookmakers. Cartons, corners, tirs, mi-temps, buteurs, double chance, remboursé si nul,
  handicaps et « les deux marquent » n'ont pas de cote française chez nos fournisseurs : écartés (pronostic.legs_from_db).
- Basket NBA (services/basket.py) : vainqueur, handicap et total de points français (3 crédits par passage) ajoutés
  aux cotes du flux ; cote retenue = médiane des sites français. Paris joueurs écartés (pas de cote française).
- `FRENCH_ODDS_ONLY=0` désactive la règle (les tests la désactivent par défaut).
- Coût : The Odds API gratuite = 500 crédits par mois ; 77 restants le 28/10 à 16h.
