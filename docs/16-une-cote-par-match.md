# Une cote par match (à partir du 28 octobre 2026)

Décision de Vincent (27/10/2026), après le bilan du 11 au 26 octobre : les agents cherchent **une seule cote valable par
match**. Elle est affichée sur la fiche du match (onglet « Probabilité retenue », à côté de « Buteurs ») avec sa
confiance, et chacun peut l'ajouter à son carnet. Le **Top confiance du jour** (une seule sélection officielle) est la
cote retenue la plus sûre de la journée, à **1,60 ou plus** (Vincent, 27/10 09:12 : « 1 seul ticket retenu qui devient
la meilleure confiance, toujours au-dessus de 1.60 » ; remplace les 3 sélections décidées le même matin). Plus de combinés, d'idées bonus ni de « Top 3 des cotes » à partir du 28/10 ; l'historique
d'avant reste tel quel.

Règles (sportai/services/pronostic.py, `PICK_*`) :

- cote de 1,60 au moins, sans plafond (Vincent, 27/10 12:52 : « la cote mini est de 1.60 mais pas de cote max ») :
  une grosse cote passe seulement si sa chance est vraiment forte (« les agents sont là pour trouver du réalisme, pas
  du rêve ») ;
- confiance = moitié chance du modèle (après la correction de la revue), moitié chance des bookmakers ; au moins 50 %,
  et de la valeur à cette confiance (confiance × cote > 1) ;
- le modèle pas plus de 8 points au-dessus des bookmakers ;
- pas de « une équipe marque moins de X buts » (en pause), pas de buteur, pas de pari bloqué par le risque de rotation ;
- parmi ce qui reste, la plus sûre ; « Confiance élevée » à partir de 60 %, « Confiance moyenne » en dessous ;
- figée 45 min avant le coup d'envoi ; un match sans cote valable dit pourquoi.

Document `pronostic/<jour>` : `paris_retenus` (match_id → pari ou raison), `selections` (une seule : le Top confiance du jour, `SELECTION_MIN_ODDS` = 1,60), `regle`.
Bilan : catégories `pari_retenu` et `selection_jour`, et `paris_retenus_par_niveau`.

Rejeu sur les jours du 11 au 26/10 (cotes du dernier passage, corrections de la revue d'aujourd'hui, donc un peu
optimiste et sur les mêmes jours qui ont servi à fixer les règles) : 34 cotes passées sur 54 (63 %, cote moyenne 1,73),
les 3 sélections 15 sur 23 (65 %), Confiance élevée 23 sur 30 (77 %), Confiance moyenne 11 sur 24 (46 %).

Top confiance du jour sur le même rejeu : 8 jours sur 12 en ont un (aucune cote retenue à 1,60 ou plus les autres
jours), 4 passés, 3 pas passés, 1 remboursé. Trop peu pour conclure : à suivre sur les vrais jours.

## Combinés de 2 (Vincent, 27/10 09:24)

« Combiné de deux max à côté » : `combines` dans `pronostic/<jour>`, affichés dans l'onglet « Probabilité retenue » des
deux matchs. Deux cotes retenues de deux matchs différents (sur un même match, la cote du combiné n'est pas le produit
des cotes), jamais le match du Top confiance, jamais un pari dans deux combinés, les plus sûres associées d'abord,
sans plafond de cote (`COMBO_MAX_ODDS` = None depuis le 27/10 12:52), un seul par jour (`COMBOS_PER_DAY` = 1 : la
meilleure confiance combiné). Figé avec son premier match. Jamais retenu comme Top confiance, mais
son propre ratio dans le bilan (catégorie `combine`, hors taux global). Rejeu 11-26/10 : 10 passés, 12 pas passés,
1 remboursé, cote moyenne 2,98.

## Tout l'historique recalculé (Vincent, 27/10 09:31 et 09:32)

« Faire et calculer comme si nous avions fait comme ça depuis le début », « refais tout en fonction de ça » :
`PER_MATCH_FROM` passe au 1er octobre (les tests des anciens tickets gardent leur chemin avec un report de date), et
les journées du 11 au 26/10 de la page ont été recalculées le 27/10 avec ces règles (cotes du dernier passage connu,
réglées sur les scores finaux). Chaque document recalculé porte `recalcule: true` et garde les anciens tickets sous
`ancien`. La page les marque « Recalculé ». Résultat : Top confiance 4 validés, 3 non validés, 1 remboursé ; cotes
retenues 34 sur 54 (Confiance élevée 23/30, moyenne 11/24) ; combinés de 2 10 sur 22 (1 remboursé).

## Cote mini 1,60, sans cote max, un combiné par jour (Vincent, 27/10 12:52)

« La cote mini est de 1.60 mais pas de cote max » ; la page dit seulement que ce sont la meilleure confiance simple et
la meilleure confiance combiné retenues par l'analyse. Les filtres de confiance (au moins 50 %, valeur, modèle pas plus
de 8 points au-dessus des bookmakers) restent : c'est eux qui décident, pas la cote. Rejeu 11-26/10 refait : Top
confiance 4 validés, 4 non validés, 1 remboursé ; cotes retenues 26 sur 48 (élevée 9/13, moyenne 17/35) ; combiné du
jour 2 validés, 4 non validés (cotes 2,76 à 3,92). La plus grosse cote retenue reste 2,21 : avec ces filtres, une cote
de 3 ou plus n'a presque jamais assez de chance réelle.

## Meilleure confiance simple : Confiance élevée seulement (Vincent, 27/10 14:56)

La meilleure confiance simple n'est choisie que parmi les cotes retenues en Confiance élevée (confiance d'au moins
60 %, `SELECTION_MIN_CONF`), à 1,60 ou plus. Un jour sans Confiance élevée à 1,60 ou plus n'a pas de meilleure
confiance simple. Rejeu 11-26/10 avant ce changement : les 4 non validés venaient tous de la Confiance moyenne ; avec
la règle, 4 validés, 0 non validé, 1 remboursé, mais une sélection 5 jours sur 12 seulement.
