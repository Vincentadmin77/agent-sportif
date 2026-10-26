# Paiements : Stripe et PayPal

**Principe :** le client crée d'abord son compte sur le site, puis paie son abonnement sur Stripe Checkout (carte) ou PayPal. Stripe et PayPal encaissent ; la plateforme décide des accès à partir de leurs webhooks authentifiés. Aucune donnée bancaire ne transite par la plateforme. Shopify n'est plus utilisé (décision du 26/10/2026).

**Avant tout encaissement :** obtenir l'accord écrit de Stripe et de PayPal (les pronostics sportifs sont une activité soumise à autorisation chez les deux), avec une entreprise immatriculée. Voir le dossier de conformité envoyé aux prestataires.

## Formules et prix
Les trois formules (mensuel 9,90 €, 3 mois 24,90 €, 12 mois 79 €) ouvrent la même formule de la plateforme (`football_premium`). Chaque prix chez le prestataire est relié à cette formule avec sa durée en jours :
```bash
python -m sportai.manage set-plan-prices football_premium price_MENSUEL:31,price_TRIMESTRE:92,price_ANNUEL:366,P-MENSUEL:31,P-TRIMESTRE:92,P-ANNUEL:366
```
`price_…` = identifiants de prix Stripe, `P-…` = identifiants de plans PayPal.

## Mise en place
**Stripe**
1. Créer un produit avec trois prix récurrents (1 mois, 3 mois, 12 mois).
2. Le site crée une session Stripe Checkout en mode abonnement, avec l'e-mail du compte (`customer_email`).
3. Ajouter un endpoint de webhook vers `https://VOTRE-API/webhooks/stripe` avec les événements `invoice.paid`, `customer.subscription.deleted`, `charge.refunded`, et copier son secret de signature dans `STRIPE_WEBHOOK_SECRET`.
4. Activer le portail client Stripe pour que l'abonné résilie lui-même.

**PayPal**
1. Créer trois plans d'abonnement (1, 3, 12 mois) dans le compte Professionnel.
2. Créer une application REST : `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`. En test, `PAYPAL_API_BASE=https://api-m.sandbox.paypal.com`.
3. Ajouter un webhook vers `https://VOTRE-API/webhooks/paypal` avec les événements `BILLING.SUBSCRIPTION.ACTIVATED`, `PAYMENT.SALE.COMPLETED`, `BILLING.SUBSCRIPTION.CANCELLED`, `BILLING.SUBSCRIPTION.EXPIRED`, `BILLING.SUBSCRIPTION.SUSPENDED`, `PAYMENT.SALE.REFUNDED`, et copier son identifiant dans `PAYPAL_WEBHOOK_ID`.

Un paiement reçu avant la création du compte (même e-mail) est appliqué automatiquement à l'inscription.

## Règles appliquées (`services/payments.py`)
| Événement | Effet |
|---|---|
| Stripe `invoice.paid` avec un prix de formule | Droit actif jusqu'à la fin de la période facturée + 3 jours de grâce ; un renouvellement prolonge le droit existant |
| Stripe `customer.subscription.deleted` | Pas de renouvellement : l'accès court jusqu'à la fin de la période payée |
| Stripe `charge.refunded` | Droit révoqué immédiatement (`refunded`) |
| PayPal `BILLING.SUBSCRIPTION.ACTIVATED` avec un plan de formule | Droit actif jusqu'à la prochaine échéance + 3 jours de grâce |
| PayPal `PAYMENT.SALE.COMPLETED` | Prolonge le droit de la durée de la formule |
| PayPal `BILLING.SUBSCRIPTION.CANCELLED / EXPIRED / SUSPENDED` | Pas de renouvellement : l'accès court jusqu'à la fin de la période payée |
| PayPal `PAYMENT.SALE.REFUNDED` | Droit révoqué immédiatement (`refunded`) |
| Signature invalide (Stripe : en-tête `Stripe-Signature`, 5 min de tolérance ; PayPal : API `verify-webhook-signature`) | 401, rien n'est enregistré |
| Même identifiant d'événement reçu deux fois | Ignoré (`duplicate`) |

Tous les événements sont conservés (*Administration › Paiements*) avec leur fournisseur et leur statut (`processed`, `ignored`, `awaiting_account`, `error`).

## Tester avant production
Testé ici : signature Stripe, refus d'un événement rejoué, idempotence, attente de compte, attribution, fin de renouvellement, remboursement, pour Stripe et PayPal (charges utiles construites ; la vérification PayPal est simulée). **Non testé** : un vrai parcours en mode test Stripe et en sandbox PayPal. À faire avant l'ouverture : abonnement, renouvellement, résiliation, remboursement, en vérifiant *Administration › Paiements* et les droits de l'utilisateur.
