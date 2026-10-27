# Leçons tirées des erreurs (règles durables)

Chaque erreur constatée sur les vrais matchs devient ici une règle. Tous les agents doivent la respecter : tickets, joueur pro et agents à venir.

## 14/10/2026 : ticket safe sur Manchester City – PSG (perdu 2-4)

### Ce qui s'est passé

- **Le ticket safe était Manchester City à 2,21.** Le modèle donnait City gagnant à 55 %, les bookmakers à 48 %. Le PSG est aussi la « bête noire » de City.
- **Les meilleures confiances ont été écartées :**
  - Aston Villa était à 72 % pour le modèle et 57 % pour les bookmakers. Villa a gagné 6-0.
  - Liverpool était à 84 % pour le modèle et 69 % pour les bookmakers. Liverpool a gagné 6-3.
- **Pourquoi ces deux matchs ont été écartés :** une règle écartait tout pari où le modèle et les bookmakers s'écartent de plus de 10 points, même quand les deux voient l'équipe favorite.
- **La Ligue des champions était trop bridée :**
  - un seul match de la compétition était permis par ticket ;
  - aucun marché de buts n'était ouvert (« plus de 1,5 but », « les deux équipes marquent »).
- **Résultat :** aucun combiné n'était possible, et le ticket safe s'est rabattu sur City seul.

### Règles depuis le 15/10/2026

1. **Un ticket safe ou semi safe se choisit sur la confiance, pas sur la valeur.**
   - Chaque pari compte pour la plus prudente des deux estimations, celle du modèle ou celle des bookmakers.
   - Un pari que les bookmakers donnent à moins de 50 % n'est jamais une « confiance ».
2. **Un modèle beaucoup plus optimiste que les bookmakers n'écarte plus un pari, si les bookmakers en font aussi le favori.**
   - Le pari est alors jugé sur l'estimation des bookmakers.
   - Les mises du joueur pro sont calculées sur une chance plafonnée à 10 points au-dessus de celle des bookmakers.
3. **Un modèle très en dessous des bookmakers, ou très au-dessus sur un match serré, écarte toujours le pari.** C'est en général le signe d'une information qui manque au modèle.
4. **Un ticket peut réunir plusieurs matchs de Ligue des champions.**
   - Leurs marchés de buts y entrent comme paris « non validé ».
   - Il ne peut y avoir qu'un seul pari « non validé » par ticket.
   - La mise est divisée par deux.
5. **Le joueur pro doit aussi saisir les occasions du direct**, y compris les buteurs (Haaland, Mbappé…).
   - Elles sont jouées comme un essai : mise réduite, bilan suivi à part, et arrêt si ce bilan perd.

## 15/10/2026 : tickets du matin bourrés de « moins de 5,5 buts » à 1,07 et favoris écartés

- **Constat :** les grands favoris de Ligue Europa (Benfica à 1,24, Bournemouth à 1,21) étaient exclus par un plancher de cote à 1,30.
- **Conséquence :** les tickets se remplissaient de paris à 1,07, qui n'ajoutent presque rien à la cote mais portent leur propre risque.
- **Règle :**
  - aucun pari sous 1,15 dans un ticket ;
  - la victoire en coupe d'Europe est permise de 1,15 à 2,50.

## 15/10/2026 : Benfica dans le ticket safe et dans le ticket semi safe

- **Constat :** le même pari (victoire de Benfica) était dans deux tickets officiels.
- **Conséquence :** s'il perd, il fait perdre les deux tickets d'un coup.
- **Règle de base (Vincent) :** un même choix ne va que dans un seul ticket. Le safe se construit en premier, puis le semi safe et le fun prennent d'autres paris. Les tickets bonus non plus ne reprennent aucun pari déjà joué.

## 15/10/2026 : le ticket safe devient un pari simple

- **Règle (Vincent) :** le ticket safe est toujours un pari simple, jamais un combiné, à une cote au-dessus de 1,65.
- Il lui faut au moins 55 % de chances, en prenant le plus prudent du modèle et des bookmakers.
- Tous les marchés peuvent faire le safe, à condition d'une valeur positive et d'une forte probabilité (Vincent). Les buts comptent comme un résultat d'analyse (forme des équipes et des attaquants, match ouvert ou fermé).
- Dans les combinés, les paris sur les buts ne comptent pas dans la limite d'un seul pari en essai par ticket : deux « les deux équipes marquent » font un bon ticket (exemple de Vincent, 14/10).
- Sans pari qui remplisse ces conditions, le safe reste vide et dit pourquoi.

## 27/10/2026 : un tipster Telegram à 4 sur 4 en simples, nous à côté sur Séville - Osasuna

- **Constat (Vincent) :** un tipster a passé 4 simples : Boulogne - Reims (Ligue 2) à 1,91, victoire de Séville contre Osasuna à 1,88, victoire de Trabzonspor à 1,79, Galatasaray ou nul et plus de 2,5 buts à 1,99. Notre position simple du 26/10 était « Osasuna marque » à 1,68 : perdue (Séville 2-0).
- **Ce que disait le modèle sur Séville - Osasuna :** victoire de Séville 47 % contre 51 à 54 % pour les bookmakers ; victoire d'Osasuna 27 % contre 19 à 21 % ; « Osasuna marque » 68 % contre 58 % pour les bookmakers. Le modèle surestimait l'équipe qui se déplace, de près de 10 points. Les bookmakers avaient raison.
- **Déjà corrigé :** avec les règles du 27/10, ce pari n'est plus retenu. La confiance est la moyenne du modèle et des bookmakers, et un modèle à plus de 8 points au-dessus des bookmakers écarte le pari. La victoire de Séville n'avait pas de valeur pour notre modèle (47 % pour une cote à 1,88), donc elle n'aurait pas été proposée non plus : sur ce match, l'agent ne prenait rien, ce qui reste mieux qu'un pari perdu.
- **Couverture :** la Ligue 2 et le championnat turc n'étaient pas suivis ; la Süper Lig est ajoutée depuis le 27/10. La Ligue 2 reste à décider.
- **Marchés :** « Galatasaray ou nul et plus de 2,5 buts » est un pari combiné sur un même match ; le flux de cotes n'en propose pas la cote (seulement « résultat et plus/moins de buts » sur les trois issues). Les marchés de mi-temps, puis corners et cartons, sont en cours d'ajout (Vincent, 27/10 : toutes les cotes doivent être étudiées).
- **Mesure :** 4 sur 4 sur une journée ne dit rien d'un pronostiqueur ; on compare sur des dizaines de jours. Notre ratio de la meilleure confiance simple est suivi à part, échecs compris.
