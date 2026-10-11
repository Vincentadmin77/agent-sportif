# Cartons rouges : ce que disent les vrais matchs

## La question

Quand une équipe prend un carton rouge, le modèle live baisse ses chances de
marquer et augmente celles de l'adversaire. Jusqu'ici ces deux réglages
(0,67 et 1,25) étaient des valeurs « au jugé », jamais vérifiées.

## Ce qu'on a fait

- **3 511 matchs réels** : Ligue 1, Premier League, LaLiga, Bundesliga et
  Serie A, saisons 2024-25 et 2025-26 (données API-Football).
- **590 matchs avec au moins un rouge**, dont **424** utilisables : premier
  rouge entre la 10e et la 85e minute (les autres sont trop tôt, trop tard,
  ou avec deux rouges à la même minute).
- Pour chaque match, on compare les buts marqués **après** le rouge aux buts
  qu'on aurait attendus sans rouge, vu la force des deux équipes sur la
  saison et le moment du match (on marque un peu plus en fin de match).
- Si un deuxième rouge tombe ensuite, on arrête le comptage à ce moment-là.
- Coût : 187 requêtes API (les réponses sont gardées en cache).

## Résultats

| Équipe | Buts attendus | Buts marqués | Multiplicateur | Fourchette (95 %) | Ancien réglage |
|---|---|---|---|---|---|
| Équipe à 10 | 239 | 118 | **0,49** | 0,41 – 0,58 | 0,67 |
| Adversaire à 11 | 270 | 432 | **1,60** | 1,47 – 1,74 | 1,25 |

En clair : après un rouge, l'équipe à 10 marque **environ deux fois moins**
que prévu, et l'adversaire marque **60 % de plus**. L'effet est bien plus
fort que ce que le modèle supposait. Les deux fourchettes excluent les
anciennes valeurs : ce n'est pas un hasard d'échantillon.

Selon le score au moment du rouge :

| Score au moment du rouge | Matchs | Équipe à 10 | Adversaire |
|---|---|---|---|
| Égalité | 162 | 0,47 (0,36 – 0,60) | 1,49 (1,32 – 1,67) |
| L'équipe à 10 mène | 111 | 0,46 (0,30 – 0,64) | 2,11 (1,73 – 2,52) |
| L'équipe à 10 est menée | 151 | 0,55 (0,40 – 0,72) | 1,45 (1,26 – 1,66) |

Quand l'équipe à 10 mène, l'adversaire pousse fort et marque beaucoup plus
(une partie de cet effet existe même sans rouge : une équipe menée attaque
davantage). Le modèle n'utilise pas encore ce détail ; il applique la même
correction quel que soit le score.

Vérifications :

- Les penalties obtenus juste après le rouge (26 sur 432 buts) ne changent
  pas la conclusion : sans eux, l'adversaire est encore à 1,51.
- En ne gardant que les matchs avec un seul rouge (370), on trouve la même
  chose : 0,51 et 1,61.

## Ce qui a changé

Le modèle live utilise désormais **0,49** pour l'équipe à 10 et **1,60** pour
son adversaire (au lieu de 0,67 et 1,25). Concrètement, après un rouge,
l'agent donne plus de chances de victoire à l'équipe à 11 et moins à
l'équipe réduite qu'avant.

Limite connue : le modèle live répartit les buts de façon uniforme dans le
match, alors qu'en réalité il y en a un peu plus en fin de match. Ce point
est indépendant des cartons rouges et n'a pas été modifié ici.

Pour refaire le calcul : `python scripts/calibrate_red_cards.py` depuis
`backend/` (aucune requête si le cache est présent).
