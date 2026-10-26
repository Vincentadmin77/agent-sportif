# Guide administrateur

Accès : se connecter avec un compte admin, puis *Mon espace › Administration*.

## Routine quotidienne (mode semi-automatique)
1. **Analyses** : le worker lance une analyse par compétition surveillée toutes les 6 h (`PREMATCH_INTERVAL_MIN`). Vous pouvez en lancer une à la main.
2. Ouvrir le **détail** : matchs, xG du modèle, probabilités 1/N/2, et toutes les opportunités examinées avec la raison de chaque rejet.
3. **Sélections** : les sélections retenues attendent votre validation (`pending_review`).
   - *Contrôle IA* : collez les informations récentes (une par ligne : `source | titre | texte`). L'agent Claude répond `keep`, `flag` ou `reject` avec les faits et leur fiabilité. Il ne modifie aucun chiffre. Nécessite `ANTHROPIC_API_KEY`.
   - Vérifiez vous-même compositions et absences.
   - *Publier* : refusé si la cote observée a plus de 30 minutes (relancez l'analyse) ou si les données sont simulées.
   - *Rejeter* / *Suspendre* : motif obligatoire, tracé.
4. Après le match : *win / lose / push / void* pour régler. Le gain est calculé à la cote publiée.

## Ce qui est impossible, volontairement
- Publier une sélection simulée.
- Modifier la cote, la probabilité ou le marché d'une sélection publiée (bloqué par l'application et par la base de données).
- Supprimer une sélection publiée, même perdante.
- Changer sa propre fonction d'admin.

## Réglages
- **Seuils** : EV minimale (3 %), EV prudente minimale (0 dans la fourchette 1,50–2,00, 2 % en dehors), test de résistance (−2 points), divergence maximale avec le marché (10 points), qualité minimale des données, âge maximal des cotes, plafond de sélections (0 à 4).
- **Marchés** : résultat, double chance et remboursé si nul activés ; totaux et « les deux équipes marquent » désactivés tant que le backtest ne montre pas de gain (voir docs/04-backtest.md).
- **Compétitions surveillées** : Ligue 1 par défaut. Chaque compétition consomme des crédits de cotes.

Modifiez les seuils prudemment et uniquement sur la base de résultats mesurés ; chaque changement est journalisé.

## Autres onglets
- **Utilisateurs** : droits manuels (ex. accès offert), rôle admin.
- **Sources** : clés configurées, dernier succès/erreur par fournisseur, quota The Odds API.
- **Paiements** : webhooks Stripe et PayPal reçus et leur traitement.
- **Performances** : bilan par type (avant-match, live) et nombre de sélections par statut.
- **Journal** : toutes les actions d'administration.
