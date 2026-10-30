# Les spécialistes : un agent par sport, à blanc (30/10/2026)

Demandé par Vincent le 30/10 (« un agent par sport, qui s'inspire des meilleurs dans son domaine »). Chaque
spécialiste garde les axes de son sport, donne UN choix par match ou passe, et a son propre ratio. À blanc : jamais
publié, hors ratio officiel, hors ratio du M. Les réglages du M et les positions officielles ne bougent pas avant le
rapport du M du lundi 2/11. Noms affichés : à choisir par Vincent (pas de vrais noms de personnes sur le site).

Code : `backend/sportai/services/specialistes.py` (règles), `backend/scripts/specialistes.py` (passage),
`backend/scripts/backtest_specialistes.py` (test tennis sur l'historique), tests `tests/test_specialistes.py`.

## Méthodes
| Sport | Inspiré de | Marchés | Règle |
|---|---|---|---|
| Foot | Benham / Bloom (le marché comme base, xG), Sumpter, Tippett | résultat (1/2, DC, DNB), plus/moins 1,5-3,5, les deux marquent | chance = 65 % marché + 35 % modèle ≥ 55 % ; cote 1,50-2,30 qui paie plus que la chance ; modèle entre 0 et +6 pts au-dessus du marché (plus haut : il a raté une info) ; la plus probable |
| Basket | Voulgaris (projection de score, écart en points) | total de points, handicap (pas de vainqueur) | écart projection / chiffre principal des bookmakers ≥ 4 pts (total) ou ≥ 3 pts (handicap), ×1,5 les 3 premières semaines de saison, jamais plus du double (sinon c'est souvent un absent non vu) ; pari sur la ligne principale |
| Tennis | Sackmann (Elo par surface) | vainqueur seulement | Elo et marché à 5 pts au plus, chance 50/50 ≥ 55 %, valeur à la meilleure cote, aucun joueur revenu après plus de 60 jours sans match |

Seuils fixés d'avance, pas ajustés sur les résultats.

## Tests sur l'historique (30/10)
- Foot, rejoué sur les documents de la page (14-29/10, 140 matchs terminés) : **16/19 (84 %)**, cote moyenne 1,64,
  point mort 61 %. Le M sur les mêmes matchs : 42/74 (57 %). Échantillon petit : à confirmer en vrai.
- Tennis, juillet 2024 → octobre 2026 avec les cotes (`backtest_specialistes.py`). Choix tennis actuel : 2 374/4 075
  (58,3 %), point mort 59,7 %. Spécialiste, 3 marchés, écart 8 pts : 59,8 % pour 61,3 % ; vainqueur seul, 8 pts :
  59,1 % pour 59,8 % ; **vainqueur seul, 5 pts (retenu) : 521/863 (60,4 %), cote moyenne 1,68, point mort 60,0 %**.
  Seule version au-dessus du point mort, de peu, choisie parmi 3 essais : à confirmer en vrai.
- Basket : pas d'historique de cotes ; 4 positions rejouables sur la page (28/10), 0/4. À juger en vrai.

## Passage (toutes les heures)
1. ArtifactData `query` de `matchs` et `basket_matchs` (jour == J) et `list` de `specialistes`, `out_dir=/tmp/claude-0/spec/page`.
2. `cd backend && python3 scripts/specialistes.py --page /tmp/claude-0/spec/page --out /tmp/claude-0/spec/out`
   (tennis : 2 appels api-tennis.com par circuit, clé `API_TENNIS_KEY`).
3. Écrire `out/specialistes/<J>.json` et `out/specialistes/bilan.json` dans la collection `specialistes`.

Choix recalculé à chaque passage avant le match, figé au passage H−45 (foot, basket) ou 45 min avant (tennis).
Un match vu pour la première fois après le début reste hors ratio (`enregistre_apres_coup_envoi`).
Journée : 08:00-02:00 foot et tennis, basket jusqu'à 04:00 (un passage avant 04:00 compte pour la veille).
