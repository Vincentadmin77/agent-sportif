# Architecture et décisions techniques

## Choix retenus

| Décision | Raison |
|---|---|
| **Python (FastAPI) pour le moteur, l'API et le worker** | Un seul langage pour les calculs et le backend : pas de duplication de la logique de probabilités. numpy/scipy pour l'estimation. |
| **Next.js + TypeScript pour le frontend** | Site public et application dans un seul projet. Le navigateur ne parle qu'à son origine ; `/api/*` est relayé vers l'API Python, ce qui garde le cookie de session first-party. |
| **PostgreSQL** | Données, historique, audit, cache. Un déclencheur SQL interdit de modifier la cote ou la probabilité d'une sélection publiée, et de la supprimer. |
| **Pas de Redis ni de file de messages au départ** | Le worker planifie lui-même ses tâches ; un verrou consultatif PostgreSQL garantit un seul ordonnanceur actif. À ajouter si la charge live l'exige. |
| **Adaptateurs fournisseurs** | Le moteur ne voit que des types neutres (`Fixture`, `OddsQuote`, `LiveSnapshot`). Remplacer The Odds API par un flux Winamax partenaire = écrire un adaptateur. |
| **MCP pour Claude, appels directs pour le backend** | Le serveur MCP expose le moteur à Claude (analyse assistée, développement). Le backend appelle le moteur en direct : plus simple, plus fiable. |
| **LLM ≠ modèle** | Les probabilités viennent uniquement du moteur. L'agent Claude interprète les informations et peut seulement garder, signaler ou recommander le rejet. |

## Flux d'une analyse avant-match

1. Le worker (ou l'admin) lance une analyse pour une compétition.
2. Connecteurs : historique des résultats, matchs à venir, cotes multi-bookmakers. Si un fournisseur échoue, l'analyse est enregistrée `provider_unavailable`, sans candidat. Rien n'est inventé.
3. Le modèle Dixon-Coles est ajusté sur les trois dernières saisons (pondération temporelle, demi-vie d'environ un an), puis l'incertitude des paramètres est estimée (approximation de Laplace).
4. Pour chaque marché coté : probabilité centrale, 10e centile, cote juste, EV, EV prudente, EV après baisse de 2 points, probabilité de marché sans marge (Shin, moyenne des bookmakers), qualité des données.
5. Filtres (configurables) puis classement sur l'EV prudente × qualité, une sélection par match, au plus quatre.
6. Tout est enregistré : cotes observées, candidats retenus **et écartés** avec leurs raisons, sélections.
7. En mode réel, les sélections attendent la validation de l'admin (`pending_review`). En simulation, elles sont `simulation_only` et ne peuvent pas être publiées.

## Modèle live

Buts restants ~ Poisson avec le taux avant-match × temps restant, ajusté aux cartons rouges, et mis à jour par les xG réellement observés (mise à jour gamma-Poisson) uniquement si le flux en fournit. Si le flux a plus de 60 s, toute recommandation est bloquée et une alerte « données trop anciennes » est émise. Une alerte commerciale exige EV ≥ 5 % et un avantage qui survit à une baisse de 3 points de probabilité ; un simple mouvement de cote ne déclenche rien.

## Multi-sports

Les comptes, droits (`features` comme `basketball.selections`), abonnements, publication et interface sont communs. Chaque sport aura son propre dossier `engine/<sport>`, ses marchés et ses critères. Les formules Basket et Tennis existent déjà dans la table des plans ; les moteurs ne sont pas commencés, volontairement.

## Sécurité

- Mots de passe Argon2 ; sessions serveur (jeton aléatoire, seul son hachage est stocké) ; cookie HttpOnly, Secure, SameSite=Lax.
- Les requêtes modifiantes exigent `Content-Type: application/json` (bloque les formulaires inter-sites).
- Limitation des tentatives de connexion (par processus ; à mutualiser en multi-instances).
- Droits contrôlés par l'API sur chaque route ; un admin ne peut pas se rétrograder lui-même.
- Webhooks Stripe et PayPal : signature vérifiée (Stripe en temps constant, PayPal par son API), idempotence par fournisseur et identifiant d'événement.
- Contenus web passés à Claude balisés comme non fiables, réponse imposée par un schéma d'outil, budget vérifié avant l'appel.
- Journal d'audit de toutes les actions d'administration.
