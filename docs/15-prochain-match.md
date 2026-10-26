# Le prochain match de chaque équipe (risque de rotation)

Demande de Vincent (26/10/2026) : pour chaque match analysé, regarder le match suivant de chaque équipe, pour savoir
si elle risque de se ménager ce soir parce qu'elle doit tout donner peu après contre une équipe plus forte ou dans
un match plus important.

## Ce que fait l'agent

À chaque passage d'avant-match, pour les deux équipes de chaque match du jour :

1. Il cherche leur prochain match, toutes compétitions confondues (championnat, coupes nationales, coupes d'Europe).
   Si cette source ne répond pas, il prend les calendriers de la saison déjà enregistrés
   (`data/calendrier`, qui n'ont pas les coupes nationales). La recherche est faite une fois par jour et par match
   (2 requêtes), puis gardée pour les passages suivants de la journée.
2. Il note l'importance de ce prochain match et celle du match de ce soir, sur une échelle simple :
   - 4 : phase finale européenne (barrages, huitièmes, quarts, demies, finale) ;
   - 3 : phase de ligue européenne, demi-finale ou finale de coupe nationale, ou match de championnat à gros
     enjeu en fin de saison (titre, Europe, maintien) ;
   - 2 : match de championnat, quart de finale de coupe nationale ;
   - 1 : premiers tours de coupe nationale, super coupe ;
   - 0 : match amical.
3. Il compare la force des deux adversaires (celui de ce soir et le suivant) quand ils sont dans le même
   classement : points pris par match. L'adversaire suivant est « plus fort » s'il prend au moins 0,5 point de
   plus par match (à partir de 3 matchs joués).

## La règle du risque de rotation

Il y a un risque quand le prochain match a lieu **4 jours ou moins** après celui de ce soir (en jours de
calendrier) **et** :

- soit il est **plus important** que celui de ce soir ;
- soit l'adversaire est **nettement plus fort**, dans un match au moins aussi important que celui de ce soir.

Le risque est **élevé** quand le match plus important est 3 jours ou moins après, ou quand les deux raisons
s'additionnent. Sinon il est **modéré**.

Exemple de phrase sur la fiche du match : « Lyon joue Bayern Munich 3 jours après (Ligue des champions, match plus
important) : risque de rotation élevé. »

## Effet sur les idées

L'effet réel d'une rotation n'a pas encore été mesuré sur nos données, donc l'agent reste prudent :

- les probabilités du modèle ne changent pas ;
- quand le risque d'une équipe est **élevé**, les top idées (safe, semi safe, fun) et les autres idées de la boîte
  à idées n'utilisent plus les paris qui demandent à cette équipe de **gagner** (victoire, remboursé si nul,
  handicap de 0 ou moins, résultat + buts, gagne sans prendre de but, score exact gagnant) ou de **marquer
  beaucoup** (plus de 1,5 but de l'équipe, 2 buts ou plus) ;
- les autres paris du match restent possibles (double chance, nombre de buts du match, les deux équipes
  marquent…) ;
- le message du jour dit quelle équipe est concernée et pourquoi ;
- un risque modéré est seulement affiché ; les idées déjà figées ne changent pas ; la cote minimum de 1,50 et
  toutes les autres règles restent les mêmes.

## Où le trouver

Dans chaque fiche match, `contexte.prochain_match.domicile` et `contexte.prochain_match.exterieur` :

| Champ | Contenu |
| --- | --- |
| `equipe`, `adversaire` | l'équipe et son prochain adversaire |
| `date`, `jours`, `heures` | date du prochain match, jours de calendrier et heures depuis ce soir |
| `lieu` | `domicile` ou `exterieur` pour ce prochain match |
| `competition`, `tour` | la compétition et la journée ou le tour |
| `importance`, `importance_ce_soir` | l'échelle de 0 à 4 ci-dessus |
| `plus_important`, `adversaire_plus_fort` | les deux raisons possibles (`adversaire_plus_fort` vide si on ne sait pas comparer) |
| `force_adversaire_ce_soir`, `force_adversaire_suivant` | rang et points par match utilisés pour comparer |
| `risque_rotation` | `élevé`, `modéré` ou vide |
| `raisons`, `phrase` | les raisons et la phrase affichée (seulement s'il y a un risque) |
| `source` | `api` ou `calendrier` |

La phrase s'ajoute aussi à `contexte.phrases`.

## Limites

- La force n'est comparée que si les deux adversaires sont dans le même classement (même championnat, ou même
  phase de ligue européenne) : sinon seule l'importance compte.
- Les calendriers de secours n'ont pas les coupes nationales.
- Les seuils (4 jours, 3 jours, 0,5 point par match) sont des choix de bon sens, à vérifier sur les résultats.
