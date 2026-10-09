# Rapport de faisabilité technique — Plateforme d'analyse sportive (MVP Football)

Date : 9 octobre 2026. Ce rapport distingue ce qui a été **vérifié** (source citée ou test effectué) de ce qui est **déduit**.

## 1. Environnement de développement (vérifié)

| Élément | État constaté |
|---|---|
| Dépôt Git | Aucun dépôt rattaché au projet. Le code est écrit dans `/mnt/project-files/agent-sportif/` (persistant) en attendant un dépôt GitHub. |
| Langages | Python 3.13, Node 22, npm 10 disponibles. |
| Base de données | Serveur PostgreSQL 16 installé localement : les tests peuvent tourner contre un vrai Postgres. |
| Réseau | PyPI, npm et `raw.githubusercontent.com` accessibles. **Bloqués par la politique réseau de l'environnement (HTTP 403 du proxy)** : `api.sofascore.com`, `api.the-odds-api.com`, `v3.football.api-sports.io`, `football-data.co.uk`. |
| Conséquence | Aucun fournisseur de cotes ou de statistiques ne peut être appelé en réel depuis cet environnement. Les connecteurs sont écrits et testés contre des réponses simulées au format documenté ; le premier appel réel se fera sur votre serveur (ou après ouverture du réseau de l'environnement). |

## 2. Sources de données

### SofaScore — intégration directe écartée
- SofaScore indique ne pas pouvoir partager ses données sous forme d'API en raison de ses accords avec ses fournisseurs, et ne pas fournir de données aux bookmakers ([centre d'aide SofaScore](https://sofascore.helpscoutdocs.com/article/129-sports-data-api-availability)).
- Les « API SofaScore » trouvées en ligne (Apify, etc.) sont du scraping de l'API interne non documentée. **Non retenu** : pas de droit de réutilisation commerciale, risque juridique et technique (blocages, changements sans préavis).
- Seule voie légitime : un partenariat média (widgets) — sans intérêt pour un moteur statistique.

### Winamax — pas d'API publique de cotes
- Aucune API ou flux de cotes officiellement documenté n'a été trouvé. Le scraping du site serait contraire à l'esprit des CGU et n'est **pas retenu**.
- Voie légitime vérifiée : **The Odds API** liste Winamax (`winamax_fr`) dans sa région `fr`, avec Betclic, Unibet, PMU et NetBet ([liste des bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)). Les cotes Winamax sont donc accessibles via un fournisseur tiers, sans contournement.
- Un accès partenaire direct avec Winamax reste possible commercialement (à demander à Winamax), mais n'est pas nécessaire au lancement.

### Fournisseurs retenus (comparatif)

| Fournisseur | Rôle | Coût vérifié | Points forts | Limites / à vérifier |
|---|---|---|---|---|
| **The Odds API** | Cotes pré-match + live, multi-bookmakers FR dont Winamax | Gratuit 500 crédits/mois ; 30 $/mois (20 000) ; 59 $ (100 000) ; 119 $ (5 M). Historique sur offres payantes ([tarifs](https://the-odds-api.com/)) | Couvre Winamax, Betclic, Unibet, PMU ; stockage illimité autorisé | CGU : affichage commercial autorisé « si nos données ne sont pas le produit principal vendu » ; revente du flux brut interdite ([CGU](https://the-odds-api.com/terms-and-conditions.html)). Notre produit vend une analyse, pas un flux, mais **confirmation écrite recommandée**. Marchés limités (1X2, totaux, handicaps). |
| **API-Football (api-sports)** | Matchs, compositions, événements live, statistiques, classements, H2H | Gratuit 100 req/jour ; 19 $/mois (7 500/j) ; 29 $ (75 000/j) ; 39 $ (150 000/j) ; toutes compétitions et endpoints ([tarifs](https://www.api-football.com/pricing)) | Excellent rapport couverture/prix, live inclus | Pas de xG riche ; conditions de réutilisation commerciale à confirmer par écrit. |
| **Sportmonks** | Alternative premium, xG et indice de pression | Starter 29 €/mois (5 ligues), Growth 99 € (30 ligues), Pro 249 € ; add-on xG 24 €/mois, add-on cotes 15 €/mois ([tarifs](https://www.sportmonks.com/football-api/plans-pricing)) | xG équipe/joueur, live, documentation solide | Plus cher ; conditions commerciales à vérifier. |
| **openfootball (GitHub)** | Résultats historiques pour backtests | Gratuit, domaine public (CC0) | Accessible depuis cet environnement | Résultats seulement (pas de cotes, pas de xG). |
| **football-data.co.uk** | Résultats + cotes de clôture historiques | Gratuit | Indispensable pour comparer le modèle au marché | Bloqué ici ; usage commercial à clarifier. |

**Recommandation de lancement :** API-Football Pro/Ultra (statistiques + live) + The Odds API 30–59 $ (cotes FR dont Winamax). Budget données de départ : **environ 50 à 90 $/mois**. Passage à Sportmonks + add-on xG si les xG s'avèrent apporter un gain mesuré hors échantillon.

## 3. Coûts et autorisations nécessaires

| Poste | Estimation | Décision requise de votre part |
|---|---|---|
| Données (stats + cotes) | 50–90 $/mois au départ | Ouvrir les comptes et me fournir les clés (jamais dans le chat : variables d'environnement du serveur). |
| Hébergement (API + worker + Postgres managé + frontend) | 25–60 €/mois au départ (ex. Render/Railway/Fly + Neon/Supabase + Vercel) | Choix de l'hébergeur. |
| API Claude (agents d'analyse) | Variable ; plafonné par budget configurable | Clé API Anthropic. |
| Shopify | Abonnement Shopify + app d'abonnements | Boutique existante ou à créer. |
| Juridique | Consultation avocat recommandée avant lancement | Voir § 6. |

## 4. Architecture recommandée

Principe : **peu de technologies, modules bien séparés**.

- **Moteur statistique, connecteurs, API, tâches de fond : Python (FastAPI)**. Un seul langage pour les calculs et le backend évite de dupliquer la logique de cotes/probabilités entre deux langages.
- **Frontend : Next.js + TypeScript**, consomme l'API. Les droits sont vérifiés **uniquement côté API**.
- **PostgreSQL** : données, historique immuable, audit, file de tâches (`SELECT … FOR UPDATE SKIP LOCKED`) et cache des réponses fournisseurs. Pas de Redis au départ : un composant de moins à opérer ; ajout si la charge live l'exige.
- **Worker** séparé (même code Python) : collecte planifiée, surveillance live côté serveur, alertes. Ne dépend jamais d'une conversation Claude ouverte.
- **Adaptateurs fournisseurs** derrière des interfaces (`FixturesProvider`, `OddsProvider`) : remplacer un fournisseur ne touche pas au moteur.
- **Mode simulation strictement séparé** : chaque ligne de données porte un `mode` (`simulation` / `real`) ; la publication refuse tout ce qui n'est pas `real`.
- **LLM ≠ modèle** : Claude interprète, recherche et synthétise ; les probabilités viennent exclusivement du moteur statistique déterministe et versionné.
- **MCP** : un serveur MCP exposant le moteur statistique et l'historique pour l'usage par Claude (développement, analyse assistée). Le backend appelle le moteur en direct (plus simple et plus fiable qu'un aller-retour MCP).

```
frontend (Next.js) ──HTTP──> api (FastAPI) ──> engine (Python, déterministe)
                                  │                 ▲
                                  ├──> Postgres <───┤
worker (planif., live, alertes) ──┘                 │
connecteurs (API-Football, The Odds API, simulation)┘
mcp-server (outils moteur/historique pour Claude)
orchestrateur d'agents (Claude API, budgets) ──> appelle engine + connecteurs
```

## 5. Premières fonctionnalités (ordre de réalisation)

1. Moteur statistique football : Dixon-Coles (Poisson corrigé pour faibles scores) avec pondération temporelle, matrice des scores, marchés 1X2 / double chance / remboursé si nul / plus-moins / les deux équipes marquent.
2. Calcul de valeur : cote juste, EV, retrait de la marge (proportionnel et méthode de Shin), **incertitude par bootstrap** et EV « prudente » au quantile bas.
3. Moteur de sélection : seuils configurables, 0 à 4 sélections, une seule sélection par match (anti-corrélation), statut « aucune opportunité ».
4. Évaluation : Brier, log-loss, calibration par tranche, backtest chronologique hors échantillon sur données réelles (openfootball).
5. Modèle live conditionnel (score, minute, cartons rouges) + garde-fou de fraîcheur des données.
6. API, base de données, comptes, rôles et droits, historique immuable, workflow de validation admin.
7. Frontend public + espace utilisateur + panneau admin.
8. Webhooks Shopify (signature HMAC, idempotence, annulations/remboursements).
9. Serveur MCP du moteur. Puis orchestrateur d'agents Claude (phase 2).

## 6. Risques majeurs

1. **Réglementaire (le plus important).** En France, la DGCCRF et l'ANJ ont rappelé en juin 2026 que suggérer qu'un service augmente les chances de gain peut constituer une pratique commerciale trompeuse ; la promotion par des influenceurs d'abonnements à des pronostics est interdite depuis la loi du 9 juin 2023 ; des sociétés de pronostics ont été condamnées à des amendes (80 000 € pour l'une) ([economie.gouv.fr](https://www.economie.gouv.fr/node/3240792)). Conséquences pour le produit : aucune promesse de gain, présentation honnête des performances (échantillon, période, limites), pas de marketing par influenceurs, liens uniquement vers opérateurs agréés ANJ. **Consultation d'un avocat indispensable avant la commercialisation.**
2. **Absence d'avantage réel.** Les marchés football majeurs sont efficients ; un modèle Poisson seul bat rarement les cotes de clôture. Le système doit être prêt à publier zéro sélection, et le backtest contre les cotes de marché décidera si le produit a une valeur.
3. **Droits sur les données.** Usage commercial à confirmer par écrit auprès de chaque fournisseur (en particulier The Odds API : « primary product »).
4. **Latence live.** Les flux grand public ont 10 à 60 s de retard ; les cotes live bougent plus vite. Le garde-fou de fraîcheur bloquera beaucoup d'alertes : c'est voulu.
5. **Petits échantillons.** Un taux de réussite sur quelques dizaines de sélections ne prouve rien ; l'interface affiche intervalles de confiance et tailles d'échantillon.
6. **Coût LLM.** Budgets par analyse et plafonds d'appels obligatoires dans l'orchestrateur.
7. **Environnement.** Les API sportives sont bloquées ici : les connecteurs réels ne seront validés qu'avec de vraies clés sur un environnement ouvert.
