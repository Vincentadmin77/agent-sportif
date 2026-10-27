# Une cote par match (à partir du 28 octobre 2026)

Décision de Vincent (27/10/2026), après le bilan du 11 au 26 octobre : les agents cherchent **une seule cote valable par
match**. Elle est affichée sur la fiche du match (onglet « Probabilité retenue », à côté de « Buteurs ») avec sa
confiance, et chacun peut l'ajouter à son carnet. Le **Top confiance du jour** (une seule sélection officielle) est la
cote retenue la plus sûre de la journée, à **1,60 ou plus** (Vincent, 27/10 09:12 : « 1 seul ticket retenu qui devient
la meilleure confiance, toujours au-dessus de 1.60 » ; remplace les 3 sélections décidées le même matin). Plus de combinés, d'idées bonus ni de « Top 3 des cotes » à partir du 28/10 ; l'historique
d'avant reste tel quel.

Règles (sportai/services/pronostic.py, `PICK_*`) :

- cote entre 1,50 et 2,20 ;
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
