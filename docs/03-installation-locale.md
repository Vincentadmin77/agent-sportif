# Installation locale et tests

## Prérequis
Python 3.11+, Node 22+, PostgreSQL 16 (ou Docker, voir `docker-compose.yml`).

## Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # tests unitaires et API (SQLite par défaut)
TEST_DATABASE_URL="postgresql+psycopg://user:pass@localhost/sportai_test" pytest tests/test_api.py   # contre PostgreSQL, y compris le déclencheur d'immutabilité
```

Base et premier administrateur :
```bash
export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/sportai"
export DATA_MODE=simulation COOKIE_SECURE=false
alembic upgrade head
python -m sportai.manage init
ADMIN_PASSWORD='un-mot-de-passe-long' python -m sportai.manage create-admin vous@exemple.fr
uvicorn sportai.api.main:app --port 8000        # API
python -m sportai.worker                         # tâches de fond (autre terminal)
```

## Frontend
```bash
cd frontend
npm ci
BACKEND_URL=http://127.0.0.1:8000 npm run build   # l'adresse de l'API est figée au build
npm start                                          # http://localhost:3000
```

## Test de bout en bout (navigateur)
Avec l'API et le site lancés :
```bash
cd frontend
ADMIN_EMAIL=vous@exemple.fr ADMIN_PASSWORD='...' node e2e/smoke.mjs /tmp
```
Vérifie : inscription, refus de l'administration à un non-admin (côté serveur), accès restreint selon la formule, analyse simulée signalée et non publiable, analyse réelle sans clés marquée `provider_unavailable`, aucune donnée simulée visible côté abonné.

## Backtest
```bash
cd backend && python scripts/backtest.py --out ../docs/backtest-openfootball.json
```

## Serveur MCP (Claude Code)
```bash
cd backend && claude mcp add sportai-engine -- python -m sportai.mcp.engine_server
```
