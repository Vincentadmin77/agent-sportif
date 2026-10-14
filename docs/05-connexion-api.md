# Connexion aux API de données

Les clés se placent dans les variables d'environnement du serveur (jamais dans le code, jamais dans le chat). Puis `DATA_MODE=real`.

## API-Football (matchs, live, événements, statistiques)
1. Créer un compte sur https://dashboard.api-football.com et choisir une offre (gratuite 100 requêtes/jour pour tester ; Pro 19 $/mois ou Ultra 29 $/mois pour la production).
2. Copier la clé dans `API_FOOTBALL_KEY`.
3. Endpoints utilisés : `/fixtures` (matchs à venir, résultats, état live), `/fixtures/events` (cartons rouges), `/fixtures/statistics` (xG quand disponibles).
4. Demander par écrit à API-Football la confirmation que l'usage dans un service d'analyse payant est couvert.
5. **Cotes (source principale depuis le 14 octobre 2026)** : `/odds?league=&season=&date=` (une requête par page de 10 matchs, donc 1 à 2 par compétition et par passage ; `/odds?fixture=` pour un match que la liste du jour aurait oublié). Environ 10 bookmakers (Bet365, Pinnacle, Betfair, 1xBet, Betano, William Hill...) et jusqu'à 180 types de paris par match ; la meilleure cote de chaque pari est retenue avec son bookmaker. Les paris qui se décident sur le score final (résultat, double chance, remboursé si nul, plus/moins, totaux d'équipe, handicaps asiatiques et européens, résultat + buts, résultat + les deux marquent, clean sheet, gagner sans encaisser, pair/impair, score exact, nombre exact de buts, tranches de buts, nul avec buts) sont chiffrés par le modèle ; les autres (mi-temps, corners, cartons, buteurs, minutes...) sont listés avec leurs cotes, « non analysés ». Le flux rafraîchit les cotes d'avant-match environ toutes les 3 heures (champ `update`) : leur âge limite est de 4 heures au lieu de 15 minutes. Code : `sportai/connectors/api_football_odds.py`.
6. Conventions vérifiées sur une vraie réponse (14/10/2026) : dans « Asian Handicap » et « Handicap Result », le nombre est le handicap de l'équipe à domicile des deux côtés (« Away -1.25 » = extérieur +1,25) ; « more 6 » dans « Exact Goals Number » signifie 6 ou plus.

## The Odds API (cotes, dont Winamax) — source de secours
Appelée seulement quand API-Football n'a aucune cote pour les matchs de la fenêtre (forfait de 500 crédits par mois presque épuisé).
1. Créer un compte sur https://the-odds-api.com. Offre gratuite : 500 crédits/mois (tests). Production : 30 $/mois (20 000 crédits) ou 59 $/mois (100 000).
2. Copier la clé dans `THE_ODDS_API_KEY`. `ODDS_REGIONS=fr` couvre Winamax, Betclic, Unibet, PMU, NetBet ; ou `ODDS_BOOKMAKERS=winamax_fr,betclic_fr` pour cibler.
3. Coût : 1 crédit par marché et par région et par appel. Avec `h2h,totals` sur `fr` : 2 crédits par compétition et par analyse. Le connecteur met en cache 2 minutes (20 s en live) et affiche le quota restant dans *Administration › Sources*.
4. CGU vérifiées le 9 octobre 2026 : affichage commercial autorisé si les données ne sont pas « le produit principal vendu » ; revente du flux interdite. **Faire confirmer par écrit** que des analyses payantes affichant les cotes sont acceptées.

## Vérifier la connexion
```bash
DATA_MODE=real python -m sportai.manage run-prematch FR1
```
Sortie attendue : `completed selections|no_opportunity`. Si `provider_unavailable`, le motif est affiché et visible dans *Administration › Sources*.

Les connecteurs ont été testés contre des réponses au format documenté, **pas contre les vrais services** (bloqués depuis l'environnement de développement). Le premier appel réel peut révéler un écart de format : il serait signalé comme `provider_unavailable` ou `failed`, jamais converti en fausses données.

## SofaScore et Winamax en direct
Non utilisés : SofaScore ne fournit pas d'API à des tiers, Winamax ne publie pas d'API de cotes. Pour un flux Winamax direct, il faudrait un accord partenaire ; il suffirait alors d'écrire un adaptateur `OddsProvider` (voir `connectors/base.py`).

## Correspondance des noms d'équipes
Les fournisseurs n'écrivent pas les noms de la même façon. `connectors/teams.py` normalise (accents, « FC », « Olympique »…) et contient une table `ALIASES`. Un match sans correspondance apparaît comme « no odds observed » dans le détail de l'analyse : ajouter l'alias manquant.
