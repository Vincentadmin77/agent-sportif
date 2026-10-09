# Intégration Shopify

**Principe :** Shopify vend et encaisse l'abonnement ; la plateforme décide des accès à partir des webhooks signés. Aucune donnée bancaire ne transite par la plateforme.

## Solution recommandée
- Produits d'abonnement avec **plans de vente (selling plans)**, gérés par l'app gratuite **Shopify Subscriptions** (ou une app tierce comme Appstle ou Recharge si vous voulez plus d'options). Chaque renouvellement crée une nouvelle commande, reçue comme `orders/paid`.
- Les abonnements sont des produits numériques (pas d'expédition).
- **À vérifier avant lancement** : que la catégorie « analyses / pronostics sportifs » est acceptée par Shopify Payments dans votre pays (les activités liées aux jeux d'argent font l'objet de restrictions dans les conditions de Shopify Payments).

## Mise en place
1. Créer un produit par formule (Football Premium, Alertes live…) avec un **SKU** unique par variante (ex. `FOOT-PREM-M`).
2. Associer les SKU aux formules de la plateforme :
   ```bash
   python -m sportai.manage set-plan-skus football_premium FOOT-PREM-M,FOOT-PREM-Y
   python -m sportai.manage set-plan-skus live_alerts LIVE-M
   ```
3. Créer une app personnalisée dans l'admin Shopify (ou via le Dev Dashboard), copier son **secret client** dans `SHOPIFY_WEBHOOK_SECRET`.
4. Abonner l'app aux webhooks (format JSON) vers `https://VOTRE-API/webhooks/shopify` :
   `orders/paid`, `orders/cancelled`, `refunds/create`, `subscription_contracts/update`.
5. Les clients s'inscrivent sur la plateforme **avec le même e-mail** que leur commande. Une commande payée avant l'inscription est appliquée automatiquement à la création du compte.

## Règles appliquées (`services/shopify.py`)
| Événement | Effet |
|---|---|
| `orders/paid` avec un SKU de formule | Droit actif jusqu'à date de paiement + 31 jours + 3 jours de grâce ; un renouvellement prolonge le droit existant |
| `refunds/create` sur une ligne de formule | Droit révoqué immédiatement (`refunded`) |
| `orders/cancelled` | Droit révoqué (`cancelled`) |
| `subscription_contracts/update` statut cancelled/expired/failed | Pas de renouvellement : l'accès court jusqu'à la fin de la période payée |
| Signature invalide | 401, rien n'est enregistré |
| Même identifiant d'événement reçu deux fois | Ignoré (`duplicate`) |

Tous les événements sont conservés (*Administration › Paiements*) avec leur statut (`processed`, `ignored`, `awaiting_account`, `error`).

## Tester avant production
Testé ici : signature, idempotence, attribution, attente de compte, remboursement (tests automatisés avec des charges utiles construites). **Non testé** : un vrai parcours avec une boutique Shopify. À faire sur une boutique de développement (Partner) en mode test de paiement : commande, renouvellement, résiliation, remboursement, en vérifiant *Administration › Paiements* et les droits de l'utilisateur.

Durée de période : 31 jours codée pour un abonnement mensuel. Pour un abonnement annuel, ajouter la durée par formule (évolution prévue).
