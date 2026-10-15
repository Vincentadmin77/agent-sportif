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
