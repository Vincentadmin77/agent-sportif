# Les spécialistes : un agent par sport, à blanc (30/10/2026)

Demandé par Vincent le 30/10 (« un agent par sport, qui s'inspire des meilleurs dans son domaine »). Chaque
spécialiste garde les axes de son sport, donne UN choix par match ou passe, et a son propre ratio. À blanc : jamais
publié, hors ratio officiel, hors ratio du M. Les réglages du M et les positions officielles ne bougent pas avant le
rapport du M du lundi 2/11. Noms (Vincent 30/10 14:16) : **V1** foot, **V2** basket, **V3** tennis (jamais de vrais noms de personnes sur le site).

Code : `backend/sportai/services/specialistes.py` (règles), `backend/scripts/specialistes.py` (passage),
`backend/scripts/backtest_specialistes.py` (test tennis sur l'historique), tests `tests/test_specialistes.py`.

## Méthodes
| Sport | Inspiré de | Marchés | Règle |
|---|---|---|---|
| Foot | Benham / Bloom (le marché comme base, xG), Dixon-Coles, Sumpter, Tippett | résultat (1/2, DC, DNB), plus/moins 1,5-3,5, les deux marquent | chance = 65 % marché + 35 % modèle ≥ 55 % ; cote 1,50-2,30 qui paie plus que la chance ; modèle entre 0 et +6 pts au-dessus du marché (plus haut : il a raté une info) ; jamais sur l'équipe « chanceuse » (buts − xG, net des encaissés, sur 8 matchs, 2 de plus que l'adversaire) ; la plus probable |
| Basket | Voulgaris (projection de score, écart en points) | vainqueur, handicap, total de points (axes fixes) | écart projection / chiffre principal des bookmakers ≥ 4 pts (total) ou ≥ 3 pts (handicap), ×1,5 les 3 premières semaines de saison, jamais plus du double (sinon c'est souvent un absent non vu) ; pari sur la ligne principale ; vainqueur du même côté quand la cote du handicap sort de 1,50-2,30 ; blessés ESPN retirés de la projection, joueur clé incertain = passe |
| Tennis | Sackmann (Elo par surface), Benter (mélange logit), Shin/Buchdahl (marge retirée) | les 5 axes : vainqueur, sets, handicap jeux, total de jeux, aces (axes fixes) | chance = mélange logit 90 % marché (marge retirée méthode puissance) + 10 % Elo ; valeur ≥ 3 % à la meilleure cote (marge retirée méthode puissance sur le vainqueur, proportionnelle ailleurs) ; aucun joueur revenu après plus de 60 jours sans match |

Foot et basket : seuils fixés d'avance, pas ajustés sur les résultats. Tennis : version choisie parmi 3 essais (voir plus bas).

## Tests sur l'historique (30/10)
- Foot, rejoué sur les documents de la page (14-29/10, 140 matchs terminés) : **16/19 (84 %)**, cote moyenne 1,64,
  point mort 61 %. Le M sur les mêmes matchs : 42/74 (57 %). Échantillon petit : à confirmer en vrai.
- Tennis, juillet 2024 → octobre 2026 avec les cotes (`backtest_specialistes.py`). Choix tennis actuel : 2 374/4 075
  (58,3 %), point mort 59,7 %. Spécialiste, 3 marchés, écart 8 pts : 59,8 % pour 61,3 % ; vainqueur seul, 8 pts :
  59,1 % pour 59,8 % ; **vainqueur seul, 5 pts (retenu) : 521/863 (60,4 %), cote moyenne 1,68, point mort 60,0 %**.
  Version remplacée le 30/10 après les dossiers de recherche :
- Tennis, règles des dossiers testées sur 2024-25 puis vérifiées sur 2026 (`/tmp` grille, 14 372 matchs) : le marché seul
  a la meilleure log-loss (0,597 ; Elo 0,627 ; WElo 0,625 ; pénalité de retour −100/−150 Elo : pire). Seule règle
  positive sur les deux périodes : mélange 90 % marché + 10 % Elo, valeur ≥ 3 % (2024-25 : 54,7 % pour 53,6 % ;
  2026 : 55,6 % pour 54,3 %). **Retenue pour V3 : 344/620 (55,5 %), cote moyenne 1,89, point mort 53,6 %** ;
  ATP 59,3 % (point mort 53,7 %), WTA 51,0 % (53,6 %).
- Vincent 30/10 14:31 : les axes de chaque sport ne changent jamais. V3 repasse sur ses 5 axes avec la même règle :
  **1 276/2 305 (55,4 %), cote moyenne 1,88, point mort 53,8 %** ; ATP 55,7 % (53,5 %), WTA 55,0 % (54,1 %). Par axe :
  vainqueur 56,6 % (54,0 %), total de jeux 56,3 % (52,9 %), handicap jeux 54,2 % (54,3 %), aces 31/52, sets 3/6.
- Basket : pas d'historique de cotes ; 4 positions rejouables sur la page (28/10), 0/4. À juger en vrai.

## Passage (toutes les heures)
1. ArtifactData `query` de `matchs` et `basket_matchs` (jour == J) et `list` de `specialistes`, `out_dir=/tmp/claude-0/spec/page`.
2. `cd backend && python3 scripts/specialistes.py --page /tmp/claude-0/spec/page --out /tmp/claude-0/spec/out`
   (tennis : 2 appels api-tennis.com par circuit, clé `API_TENNIS_KEY`).
3. Écrire `out/specialistes/<J>.json` et `out/specialistes/bilan.json` dans la collection `specialistes`.

Choix recalculé à chaque passage avant le match, figé au passage H−45 (foot, basket) ou 45 min avant (tennis).
Un match vu pour la première fois après le début reste hors ratio (`enregistre_apres_coup_envoi`).
Journée : 08:00-02:00 foot et tennis, basket jusqu'à 04:00 (un passage avant 04:00 compte pour la veille).

## Équipes chanceuses (Benham, Tippett), testé le 30/10
Cotes de clôture football-data (Pinnacle), xG API-Football, 5 grands championnats, 2022-23 à 2026-27 (5 706 matchs
appariés). Miser sur l'équipe la moins chanceuse (buts − xG net des encaissés sur 8 matchs) quand l'écart est ≥ 2 :
998 positions, 54,3 % pour un point mort de 53,2 %, **+2,3 % contre la cote de clôture** (+3,9 % à la meilleure
cote). Variable selon la saison (2022-23 négative). Utilisé comme filtre seulement (V1 ne soutient jamais l'équipe
chanceuse), pas comme raison de prendre position.

## Données qui manquent
| Agent | Donnée | Source | Coût / blocage |
|---|---|---|---|
| V2 | Blessés et incertains NBA (priorité n° 1 des dossiers) | ESPN injuries JSON (site.api.espn.com), rapport officiel NBA (official.nba.com) | gratuit ; hôtes bloqués par le réseau de l'environnement |
| V2 | Minutes, rythme, efficacité (ORtg/DRtg/pace) | stats.nba.com (nba_api), cdn.nba.com | gratuit ; bloqués |
| V2 | Cotes NBA du passé pour tester | archives publiques (Kaggle, compte requis) | gratuit ; à voir |
| V1 | Cote de clôture Pinnacle (mesurer la CLV) | football-data.co.uk (après les matchs) | gratuit, accessible |
| V3 | Historique plus long des cotes | tennis-data.co.uk | gratuit ; bloqué |
| V3 | Données Sackmann (GitHub) | | licence non commerciale : à ne pas utiliser |
