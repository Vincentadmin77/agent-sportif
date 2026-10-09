# Connexion aux API de données

Les clés se placent dans les variables d'environnement du serveur (jamais dans le code, jamais dans le chat). Puis `DATA_MODE=real`.

## API-Football (matchs, live, événements, statistiques)
1. Créer un compte sur https://dashboard.api-football.com et choisir une offre (gratuite 100 requêtes/jour pour tester ; Pro 19 $/mois ou Ultra 29 $/mois pour la production).
2. Copier la clé dans `API_FOOTBALL_KEY`.
3. Endpoints utilisés : `/fixtures` (matchs à venir, résultats, état live), `/fixtures/events` (cartons rouges), `/fixtures/statistics` (xG quand disponibles).
4. Demander par écrit à API-Football la confirmation que l'usage dans un service d'analyse payant est couvert.

## The Odds API (cotes, dont Winamax)
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
