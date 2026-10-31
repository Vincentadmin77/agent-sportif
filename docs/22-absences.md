# 22. Joueurs clés absents : correction des chances de victoire

## En bref

Une équipe privée de ses meilleurs buteurs et passeurs marque moins que ce que prévoit le modèle. L'effet est réel,
mais plus petit qu'on ne le croit : environ 8 à 15 % de buts en moins, quelques points de chance de victoire en moins,
jamais la moitié. Depuis cette version, le modèle en tient compte pour les **chances de victoire, de nul et de
handicap** seulement.

## L'étude (13 559 matchs)

- **Données.** 13 559 matchs de championnat avec les deux compositions, d'août 2020 à octobre 2026 : Premier League,
  Ligue 1, Bundesliga, Serie A, Liga, Süper Lig et Brasileirão. Aucune requête : tout venait du cache.
- **Méthode.** Le modèle de match habituel (Dixon-Coles), recalculé chaque semaine sans jamais voir l'avenir. Pour
  chaque équipe et chaque match, on regarde ses 19 matchs de championnat précédents :
  - la **part des buts et passes décisives** de chaque joueur ;
  - qui n'est **pas dans le groupe du jour** (ni titulaire ni remplaçant) ;
  - combien de **gardiens ou défenseurs habituels** manquent chez l'adversaire.
- **Ce qu'on mesure** (buts marqués divisés par buts attendus) :

| Part des buts + passes absente | Matchs | Buts marqués / attendus |
|---|---|---|
| moins de 10 % | 13 615 | 1,04 |
| 10 à 25 % | 9 474 | 1,01 |
| 25 à 40 % | 3 244 | 0,93 |
| 40 % ou plus | 785 | 0,91 |

- **Un joueur sur le banc ne compte pas comme absent** : son effet est nul. Seuls comptent les joueurs hors du groupe
  (blessés, suspendus, écartés).
- Deux gardiens ou défenseurs habituels absents ou plus : l'équipe encaisse 1,06 fois les buts attendus.

## La règle

Pour chaque équipe :

```
f = exp( -0,395 × (part absente - 0,128)  +  0,046 × (défenseurs absents chez l'adversaire - 0,423) )
```

- **Limites.** f est borné entre 0,80 et 1,10.
- **Application.** Les buts attendus de l'équipe à domicile sont multipliés par f domicile, ceux de l'équipe à
  l'extérieur par f extérieur. Le reste du modèle ne change pas.
- **Centrage.** 0,128 et 0,423 sont les moyennes de l'apprentissage. Une équipe au complet reçoit donc un léger bonus
  (+3 %, +1,6 % le matin) et une équipe très diminuée un malus.

**Quand.**

| Passage | Source | Force |
|---|---|---|
| 45 min avant le match, compositions officielles publiées | joueurs de l'effectif hors du groupe du jour | entière |
| Matin, 3 h avant, ou compositions pas encore sorties | liste des blessés et suspendus (`/injuries`), joueurs marqués absents ; les incertains ne comptent pas | moitié (exposant divisé par deux) |
| Aucune information (liste vide, erreur, Ligue des champions ou Ligue Europa) | rien | aucune correction (f = 1) |

**Où.**

| Marchés | Correction |
|---|---|
| 1N2, double chance, remboursé si nul, handicaps asiatiques et européens | **oui** |
| Nombre de buts, buts d'une équipe, les deux équipes marquent, score exact, mi-temps, buteurs | **non** : buts attendus non corrigés |

Pour les marchés sans correction, l'étude ne montre aucun gain sur le nombre de buts. Sans centrage, elle montre même
une perte.

- **Part des buts + passes.** Elle est calculée sur cette saison et la précédente, pour ce club seulement. Les
  données viennent de `/players`, déjà lu pour les compositions.
- **Joueurs comptés.** Seuls les joueurs de l'effectif de cette saison comptent : un joueur parti l'été n'est pas
  « absent ».
- **Défenseurs habituels.** Ce sont les gardiens et défenseurs titulaires dans au moins la moitié des matchs de
  championnat de la saison.

## Résultat hors échantillon

- **Découpage.** Apprentissage avant juillet 2025, test de juillet 2025 à octobre 2026 (2 952 matchs).
- **Correction à pleine force** (différence avec le modèle sans correction ; négatif = mieux) :
  - log-loss 1N2 : -0,0025 [-0,0045 ; -0,0006] ;
  - Brier 1N2 : -0,0017 [-0,0029 ; -0,0003] ;
  - plus/moins de 2,5 buts : +0,0003, non significatif.
- **Correction à demi-force :** log-loss 1N2 -0,0017, toujours significatif.
- **Où se fait le gain.** Il se concentre sur les 925 matchs avec une forte absence (log-loss -0,0066). Cinq
  championnats sur sept s'améliorent. La Serie A et la Liga se dégradent un peu.
- **Exemple.** Real Madrid sans Benzema ni Vinícius (50 % des buts + passes) contre Grenade : victoire du Real à
  79 % avant correction, 70 % après. Le Real a gagné 1-0.

## Sur la page

- **Champ ajouté.** Le document du match reçoit `contexte.absences_effet` :
  `{"domicile": f, "exterieur": f, "source": "compositions" | "blessés", "phrases": [...]}`.
- **Phrase.** Quand une équipe bouge de 5 % ou plus, une phrase simple s'ajoute au contexte, par exemple : « Sans X
  et Y (40 % des buts et passes décisives), l'attaque de Z est revue à la baisse. »
- **Liste du jour.** La liste des absents lue le matin est gardée sur la page (`absents`). Le suivi en direct ne la
  redemande donc pas.
- **Coût.** Aucune requête The Odds API. Côté API-Football : `/injuries` une fois par jour et par match, et
  `/players` pour une équipe qui a un absent ou dont la composition est publiée.

## Limites

- **Liste du matin incomplète.** Elle ne voit pas les choix de l'entraîneur. D'où la demi-force, qui n'a pas pu être
  testée exactement : `/injuries` n'est pas en cache.
- **Effet déjà dans les cotes.** Les bookmakers intègrent déjà les absences annoncées. La correction sert surtout à
  ne pas surestimer un favori diminué, rarement à créer un écart avec le marché.
- **Gain modeste.** C'est un petit gain moyen, pas une refonte du modèle.
- **Suivi à faire.** Comparer quelques semaines le facteur du matin avec celui du passage à 45 min.
