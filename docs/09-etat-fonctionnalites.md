# État précis des fonctionnalités (9 octobre 2026)

Légende : ✅ fait et testé · 🟡 fait, testé partiellement (sans le vrai service) · ⬜ non fait

## Moteur et sélection
| Fonctionnalité | État | Preuve |
|---|---|---|
| Modèle Dixon-Coles (forces, avantage domicile, correction faibles scores, pondération temporelle, gradient analytique) | ✅ | `tests/test_football_model.py` (retrouve les paramètres sur données synthétiques) |
| Incertitude des paramètres (Laplace) et intervalles de probabilité | ✅ | idem |
| Marchés 1X2, double chance, remboursé si nul, totaux 0,5–4,5 (lignes entières avec remboursement), BTTS | ✅ | `tests/test_football_model.py` |
| Valeur : P=1/O, cote juste, EV avec remboursement, retrait de marge (proportionnel, puissance, Shin) | ✅ | `tests/test_value.py` |
| Évaluation : Brier, log-loss, calibration, ROI avec IC bootstrap | ✅ | utilisé par le backtest et les performances |
| Backtest chronologique sur ~10 000 matchs réels | ✅ | `docs/04-backtest.md` |
| Backtest contre les cotes (rentabilité, CLV) | ⬜ | nécessite des cotes historiques payantes |
| Moteur de sélection 0–4, une par match, seuils, test de résistance, divergence marché, fraîcheur | ✅ | `tests/test_selection.py`, `tests/test_pipeline.py` |
| Modèle live (temps restant, cartons rouges, mise à jour par xG réels) | ✅ | `tests/test_live_model.py` |
| Alertes live (favori mené, domination stérile, opportunité, données périmées) avec conditions d'invalidation | ✅ | `tests/test_pipeline.py`, `tests/test_worker.py` |
| Modèles enrichis (xG, Elo, forme pondérée, compositions) | ⬜ | prochaine étape, à valider hors échantillon |

## Données
| Fonctionnalité | État |
|---|---|
| Adaptateur The Odds API (Winamax et 4 autres bookmakers FR) | 🟡 testé sur réponses au format documenté ; jamais appelé en réel |
| Adaptateur API-Football (matchs, résultats, live, cartons, xG) | 🟡 écrit ; aucun appel réel ni test de format enregistré |
| openfootball (historique) | ✅ appelé en réel pour le backtest |
| Mode simulation séparé (noms « SIM », non publiable, refus du mélange réel/simulé) | ✅ |
| Indisponibilité d'un fournisseur → analyse désactivée, pas de fausses données | ✅ |
| Agent / MCP actualités (recherche automatique d'informations) | ⬜ aujourd'hui l'admin colle les informations ; recherche automatique à brancher |

## Application
| Fonctionnalité | État |
|---|---|
| Base PostgreSQL, 3 migrations, déclencheur d'immutabilité des publications | ✅ testé sur PostgreSQL 16 |
| API : comptes, sessions, droits par formule côté serveur, administration complète | ✅ `tests/test_api.py` |
| Historique public complet (perdantes incluses), corrections tracées | ✅ |
| Worker : analyses planifiées, surveillance live, déduplication des alertes, verrou d'instance unique | ✅ en simulation ; 🟡 en réel |
| Site public (14 pages), espace abonné (4 pages), administration (8 onglets) | ✅ build Next.js + test navigateur `e2e/smoke.mjs` |
| Envoi des notifications (e-mail, push) | ⬜ préférences enregistrées, aucun envoi |
| Webhooks Shopify (HMAC, idempotence, attribution, remboursement, annulation) | 🟡 testé avec des charges construites ; pas avec une vraie boutique |
| Agent Claude de contrôle qualité (garder / signaler / rejeter, budget, contenu non fiable) | 🟡 testé avec un faux client ; jamais appelé avec une vraie clé |
| Orchestrateur multi-agents complet (7 agents) | ⬜ seul l'agent qualité existe ; les calculs sont déterministes et n'ont pas besoin d'agent |
| Serveur MCP du moteur (7 outils) | ✅ testé, y compris en vrai aller-retour stdio |
| MCP données, cotes, actualités, historique séparés | ⬜ jugés non nécessaires tant que le backend appelle les connecteurs en direct |
| Images Docker / docker-compose | 🟡 écrits, non construits |
| Déploiement | ⬜ |
| Moteurs basket et tennis | ⬜ volontairement après validation du football |
| Textes juridiques (mentions, CGV, confidentialité) | ⬜ gabarits à compléter avec un avocat |

## Tests
`backend`: 51 tests passent, 1 ignoré en SQLite (le test du déclencheur PostgreSQL, qui passe contre PostgreSQL). `frontend`: compilation TypeScript et build de production OK ; test navigateur de bout en bout OK (8 vérifications).
