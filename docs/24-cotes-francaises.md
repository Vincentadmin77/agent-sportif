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
- Les deux équipes marquent : lu match par match au dernier passage (1 crédit par match, seul NetBet le propose). Double chance et remboursé si nul : la région fr de The Odds API ne les donne pas (vérifié le 28/10 sur Parme-Udinese).
- Choix imposé : depuis le 28/10 au soir, chaque match reçoit un choix (pronostic.FORCE_CHOICE), marqué choix_impose quand il ne passe pas tous les contrôles.
- Coût : The Odds API gratuite = 500 crédits par mois ; 63 restants le 28/10 à 17h.
