# Déploiement

## Serveur seul (IONOS VPS M, Ubuntu 24.04), choix de Vincent du 15/10/2026
But : les passages du jour et le direct tournent sur le serveur, sans Claude (le forfait Claude ne sert plus qu'aux
améliorations). **Rien n'est public tant que Vincent n'a pas écrit « ok pour mettre en ligne »** : le pare-feu ne laisse
entrer que SSH ; le site (étape 4) viendra derrière un mot de passe.
- `deploy/install.sh` (une fois, en root) : mises à jour de sécurité automatiques, pare-feu, fail2ban, utilisateur `agent`
  sans droits, code dans `/srv/agent-sportif/code`, clés dans `/etc/agent-sportif.env` (remplies à la main), services.
  Le dépôt est privé : le script crée une clé et demande de l'ajouter en « Deploy key » (lecture seule) sur GitHub.
- `agent-runner` lance `backend/scripts/server_runner.py` : 7 h 50 goals-refresh + analyse du matin + planning, puis
  h3 / m45 (`--goals`) / m15 / final, et le direct toutes les 2 min de l'heure du match à 2 h 30 après ; un match fini règle
  tout de suite tickets, bilan et bankroll. La page est le dossier `/srv/agent-sportif/page` (mêmes collections que la page
  Claude : `matchs`, `pronostic`, `stats`, `joueur`, `reglages`, `etat/agent.json`).
- Les chemins `/mnt/project-files/agent-sportif/...` du code pointent vers `/srv/agent-sportif/files` (lien créé par le
  script) : y copier `state/` et `data/` du dossier du projet avant de démarrer.
- `agent-update.timer` : toutes les 10 min, reprend la branche (`BRANCH`, aujourd'hui `claude/project-thread-mnjit9`) et
  redémarre l'agent ; si la nouvelle version ne démarre pas, retour à la précédente. Claude modifie donc l'agent comme
  aujourd'hui, en poussant sur GitHub.
- `agent-backup.timer` : sauvegarde chaque nuit à 4 h 30 dans `/var/backups/agent-sportif`, 14 jours gardés.
- Bascule : désactiver les routines Claude (analyse du matin et planning) le jour où l'agent du serveur prend le relais.
- Pas encore repris des routines : la règle « crédits The Odds API sous 100 : pas de --goals ni de m15 ».

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
7. Brancher les webhooks Shopify sur `https://API/webhooks/shopify`.

## Surveillance
- `GET /health` pour la sonde de disponibilité.
- Journaux structurés du worker (analyses, alertes, erreurs fournisseurs).
- *Administration › Sources* pour l'état des fournisseurs. À ajouter : alerting (Sentry ou équivalent), sauvegardes testées.

## Avant ouverture commerciale
- Avis d'un avocat (pratiques commerciales, jeux d'argent, CGV, RGPD) ; compléter mentions légales, CGV, confidentialité.
- Confirmation écrite des fournisseurs de données pour l'usage commercial.
- Parcours Shopify complet testé sur une boutique de test.
- Au moins plusieurs semaines en mode réel avec publication interne seulement, pour mesurer le comportement du filtre.
