# SportAI — plateforme d'analyse sportive (MVP Football)

Plateforme SaaS d'analyse football : un moteur statistique indépendant estime les probabilités, les compare aux cotes réellement observées et retient **0 à 4 sélections** par période, uniquement si l'avantage résiste aux tests d'incertitude. Validation humaine avant publication, historique immuable, surveillance live côté serveur, abonnements payés directement par Stripe ou PayPal.

> Aucune analyse ne garantit un résultat. Le mode simulation est strictement séparé du mode réel : une donnée simulée ne peut jamais être publiée.

## Contenu

| Dossier | Rôle |
|---|---|
| `backend/sportai/engine` | Moteur statistique : Dixon-Coles, marchés, modèle live, calcul de valeur, évaluation |
| `backend/sportai/selection` | Filtre et classement des opportunités (0 à 4, une par match) |
| `backend/sportai/connectors` | Adaptateurs : API-Football, The Odds API (dont Winamax), openfootball, simulation |
| `backend/sportai/services` | Pipeline avant-match, live et alertes, publication, comptes, paiements Stripe et PayPal |
| `backend/sportai/api` | API HTTP (FastAPI), droits vérifiés côté serveur |
| `backend/sportai/worker.py` | Tâches de fond : analyses planifiées, surveillance live |
| `backend/sportai/mcp` | Serveur MCP du moteur pour Claude |
| `backend/sportai/agents` | Agent Claude de contrôle qualité (peut seulement garder, signaler ou rejeter) |
| `backend/migrations` | Migrations Alembic (PostgreSQL) |
| `frontend/` | Site public, espace abonné, administration (Next.js) |
| `docs/` | Rapport de faisabilité, architecture, procédures, état des fonctionnalités |

## Démarrage rapide (local)

Voir [docs/03-installation-locale.md](docs/03-installation-locale.md). En bref :

```bash
cd backend && pip install -e ".[dev]" && pytest          # 51 tests
export DATABASE_URL=postgresql+psycopg://...  DATA_MODE=simulation COOKIE_SECURE=false
alembic upgrade head && python -m sportai.manage init
ADMIN_PASSWORD='...' python -m sportai.manage create-admin vous@exemple.fr
uvicorn sportai.api.main:app --port 8000
cd ../frontend && npm ci && npm run build && npm start   # http://localhost:3000
```

## Documentation

1. [Rapport de faisabilité](docs/01-rapport-faisabilite.md)
2. [Architecture et décisions](docs/02-architecture.md)
3. [Installation locale et tests](docs/03-installation-locale.md)
4. [Backtest du modèle](docs/04-backtest.md)
5. [Connexion aux API de données](docs/05-connexion-api.md)
6. [Paiements Stripe et PayPal](docs/06-paiements.md)
7. [Guide administrateur](docs/07-guide-admin.md)
8. [Déploiement](docs/08-deploiement.md)
9. [État des fonctionnalités](docs/09-etat-fonctionnalites.md)
