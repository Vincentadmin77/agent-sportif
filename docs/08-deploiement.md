# Déploiement

**État : non déployé.** Les images Docker et le `docker-compose.yml` sont écrits mais n'ont pas pu être construits ici (pas de démon Docker dans l'environnement de développement).

## Cible recommandée (simple, environ 25 à 60 €/mois au départ)
| Composant | Exemple d'hébergement |
|---|---|
| PostgreSQL managé avec sauvegardes | Neon, Supabase, Render Postgres, Scaleway |
| API (`backend/Dockerfile`) | Render, Railway, Fly.io, Scaleway Serverless Containers |
| Worker (même image, `python -m sportai.worker`), **1 instance toujours active** | idem (service « background worker ») |
| Frontend (`frontend/Dockerfile` ou Vercel) | Vercel ou même hébergeur |

Pour l'hébergement des données personnelles, privilégier une région UE.

## Étapes
1. Créer la base PostgreSQL ; renseigner `DATABASE_URL`.
2. Déployer l'API avec les variables de `.env.example` (`COOKIE_SECURE=true`). La commande de démarrage applique les migrations et initialise les formules.
3. Créer l'administrateur : `ADMIN_PASSWORD=... python -m sportai.manage create-admin vous@exemple.fr` (console de l'hébergeur).
4. Déployer le worker (même image, commande `python -m sportai.worker`).
5. Construire le frontend avec `BACKEND_URL` = URL interne de l'API (figée au build) ; servir sur votre domaine en HTTPS.
6. Démarrer en `DATA_MODE=simulation`, vérifier, puis passer en `real` une fois les clés en place.
7. Brancher les webhooks Stripe sur `https://API/webhooks/stripe` et PayPal sur `https://API/webhooks/paypal` (voir docs/06-paiements.md).

## Surveillance
- `GET /health` pour la sonde de disponibilité.
- Journaux structurés du worker (analyses, alertes, erreurs fournisseurs).
- *Administration › Sources* pour l'état des fournisseurs. À ajouter : alerting (Sentry ou équivalent), sauvegardes testées.

## Avant ouverture commerciale
- Avis d'un avocat (pratiques commerciales, jeux d'argent, CGV, RGPD) ; compléter mentions légales, CGV, confidentialité.
- Confirmation écrite des fournisseurs de données pour l'usage commercial.
- Parcours complet testé en mode test Stripe et en sandbox PayPal.
- Au moins plusieurs semaines en mode réel avec publication interne seulement, pour mesurer le comportement du filtre.
