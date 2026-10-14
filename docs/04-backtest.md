# Backtest du modèle Dixon-Coles (données réelles)

**Données :** résultats openfootball (domaine public) des cinq grands championnats, saisons 2018-19 à 2025-26. Les deux premières saisons servent d'échauffement.
**Protocole :** chronologique, hors échantillon. Chaque semaine, le modèle est réajusté uniquement sur les matchs antérieurs (3 saisons glissantes), puis prédit les matchs de la semaine. Les équipes sans historique suffisant (promues) sont ignorées, comme en production.
**Référence naïve :** fréquences historiques domicile / nul / extérieur.
Résultats bruts : [backtest-openfootball.json](backtest-openfootball.json). Exécuté le 9 octobre 2026.

| Championnat | Matchs prédits | Brier 1X2 modèle | Brier référence | Log-loss modèle | Log-loss référence | Brier +2,5 buts modèle | Brier référence |
|---|---|---|---|---|---|---|---|
| Ligue 1 | 1 956 | **0,597** | 0,650 | **1,000** | 1,073 | 0,249 | 0,248 |
| Premier League | 2 161 | **0,585** | 0,646 | **0,984** | 1,066 | 0,247 | 0,246 |
| LaLiga | 2 163 | **0,589** | 0,645 | **0,988** | 1,067 | 0,246 | 0,249 |
| Bundesliga | 1 750 | **0,598** | 0,649 | **1,002** | 1,073 | 0,234 | 0,238 |
| Serie A | 2 144 | **0,583** | 0,656 | **0,979** | 1,083 | 0,251 | 0,250 |

Plus c'est bas, mieux c'est.

## Ce que cela montre
- **Résultat 1X2 : le modèle a un vrai pouvoir prédictif**, stable sur les cinq championnats (Brier inférieur de 8 à 11 % à la référence).
- **Calibration 1X2 correcte** (Premier League, victoire à domicile) : prévu 25 % → observé 25 %, 45 % → 45 %, 65 % → 63 %. Légère surconfiance au-delà de 80 % (prévu 84 % → observé 79 %, 58 matchs).
- **Total de buts (+2,5) : aucun gain sur la référence.** Le modèle ne sait pas mieux prévoir le nombre de buts qu'une fréquence moyenne. **Décision : les marchés « total de buts » et « les deux équipes marquent » sont désactivés par défaut** dans le moteur de sélection, jusqu'à ce qu'un modèle enrichi (xG, tirs) prouve un gain.

## Ce que cela ne montre pas
- **Aucune rentabilité.** Ce jeu de données n'a pas de cotes. Pour référence, les cotes de clôture des bookmakers atteignent typiquement un Brier d'environ 0,57 à 0,58 sur ces championnats (ordre de grandeur, non mesuré ici) : le modèle est probablement **moins précis que le marché**. C'est attendu pour un modèle fondé uniquement sur les scores, et c'est pourquoi le filtre exige un avantage robuste et rejette les écarts trop grands avec le marché.
- Étape suivante indispensable : backtest contre les cotes historiques (The Odds API, offre payante, historique depuis 2020) en mesurant la valeur par rapport à la cote de clôture (CLV).

## Tous les marchés chiffrés depuis le score (14 octobre 2026)
Même backtest (10 174 matchs, 5 championnats, 2020-21 à 2025-26, hors échantillon), étendu à chaque famille de paris que le modèle chiffre depuis la matrice des scores (`scripts/backtest.py`, champ `market_families` de `backtest-openfootball.json`). Chaque famille est découpée en quelques paris oui/non sans remboursement ; la référence est la fréquence de chaque pari sur les 760 matchs précédant la semaine (le test que réussit le 1X2 et que rate le +2,5).

| Famille | Événements notés | Brier modèle | Brier référence | Gain | Log-loss modèle / référence | Championnats en gain |
|---|---|---|---|---|---|---|
| Handicap asiatique (−1,5 ; −0,5 ; +0,5 ; +1,5 domicile) | 40 696 | 0,1694 | 0,1910 | **+11,3 %** | 0,510 / 0,566 | 5/5 |
| Handicap européen (−1 domicile) | 30 522 | 0,1782 | 0,1953 | **+8,8 %** | 0,535 / 0,577 | 5/5 |
| Buts de l'équipe à domicile (+0,5 ; +1,5 ; +2,5) | 30 522 | 0,1808 | 0,1936 | **+6,6 %** | 0,541 / 0,573 | 5/5 |
| Gagner sans encaisser | 20 348 | 0,1480 | 0,1577 | **+6,2 %** | 0,463 / 0,494 | 5/5 |
| Buts de l'équipe à l'extérieur | 30 522 | 0,1747 | 0,1844 | **+5,3 %** | 0,525 / 0,551 | 5/5 |
| Sans but encaissé (clean sheet) | 20 348 | 0,1785 | 0,1869 | **+4,5 %** | 0,537 / 0,560 | 5/5 |
| Résultat + nombre de buts (2,5) | 61 044 | 0,1290 | 0,1348 | **+4,3 %** | 0,418 / 0,435 | 5/5 |
| Résultat + les deux équipes marquent | 61 044 | 0,1308 | 0,1357 | **+3,6 %** | 0,421 / 0,437 | 5/5 |
| Score exact (8 scores courants) | 81 392 | 0,0711 | 0,0715 | +0,5 % | 0,268 / 0,271 | 5/5 |
| Nombre de buts (1,5 ; 2,5 ; 3,5) | 30 522 | 0,2095 | 0,2099 | +0,2 % | 0,608 / 0,609 | 3/5 |
| Nombre exact de buts | 50 870 | 0,1517 | 0,1519 | +0,1 % | 0,472 / 0,472 | 3/5 |
| Pair / impair | 10 174 | 0,2498 | 0,2500 | +0,1 % | 0,693 / 0,693 | 4/5 |
| Tranche de buts (0-1, 2-3, 4+) | 30 522 | 0,2106 | 0,2106 | 0,0 % | 0,610 / 0,610 | 3/5 |
| Buts + les deux équipes marquent | 40 696 | 0,1681 | 0,1681 | 0,0 % | 0,509 / 0,509 | 2/5 |
| Les deux équipes marquent | 10 174 | 0,2481 | 0,2468 | −0,5 % | 0,690 / 0,687 | 1/5 |

Ce que cela montre : les familles qui dépendent du **rapport de force** (handicaps, buts de chaque équipe, gagner sans encaisser, clean sheet, résultat combiné) ont un vrai pouvoir prédictif, stable sur les cinq championnats, du même ordre que le 1X2 (dont une bonne part vient d'ailleurs : handicap −0,5 = victoire). Celles qui ne dépendent que du **nombre total de buts** (plus/moins, les deux marquent, nombre exact, tranches, pair/impair) ne font pas mieux qu'une fréquence moyenne, comme le +2,5 plus haut.

Décision : **tous ces marchés restent « non validés »** dans le moteur. Battre une fréquence moyenne n'est pas battre les bookmakers, et aucune cote historique de ces marchés n'a été testée. Ils entrent dans les sélections, tickets et le joueur à l'essai : valeur d'au moins 6 % (sélections) ou 3 % (tickets, joueur), marché complet à comparer, un par ticket, mise divisée par deux. Les familles du premier groupe sont les meilleures candidates à une validation, à décider par le propriétaire ou après un backtest contre leurs cotes.

## Score estimé : l'issue d'abord (11 octobre 2026)
Même backtest (10 174 matchs, 5 championnats, hors échantillon). L'ancien score estimé prenait le score exact le plus probable : c'est souvent 1-1 même quand une équipe est favorite (domicile à 50 % → 1-1 reste le score le plus probable). Résultat : **55,7 % de nuls annoncés pour 24,6 % de nuls réels**, issue juste dans **42,0 %** des cas.
Nouvelle règle : on choisit d'abord l'issue la plus probable (1, N ou 2), puis le score le plus probable dans cette issue. Un nul n'est annoncé que s'il est vraiment l'issue la plus probable (0,5 % des matchs). **Issue juste dans 52,6 %** des cas (Ligue 1 39,5 → 51,4 %, Premier League 42,9 → 54,0 %, LaLiga 45,4 → 52,4 %, Bundesliga 38,0 → 51,3 %, Serie A 43,3 → 53,6 %). Le score exact tombe un peu moins souvent (12,5 → 11,1 %).

**Avantage du terrain :** il est ajusté par championnat (modèle refait pour chaque championnat) et il est bien calibré : victoires à domicile prévues 43,2 %, observées 43,5 % ; extérieur prévu 32,4 %, observé 32,0 % ; écart de buts domicile prévu +0,27, observé +0,27. Sur les matchs serrés (écart 1/2 < 10 points, 1 965 matchs) : domicile prévu 36,2 %, observé 37,6 %, dans la marge d'erreur. Pas de correction nécessaire.

## Ligue des champions (modèle inter-championnats)

**Mise à jour du 14 octobre 2026 : 25 championnats de plus et les C3 / C4.** Les clubs hors cinq grands championnats (Porto, Feyenoord, Bodø/Glimt, Shakhtar, Slovan Bratislava...) n'avaient pas assez de matchs : 5 des 9 matchs de C1 du 14 octobre n'étaient pas analysés. Le modèle C1 apprend désormais aussi sur les championnats PT1, NL1, TR1, BE1, AT1, SC1, GR1, UA1, CH1, CZ1, DK1, HR1, RS1, NO1, SK1, CY1, PL1, IL1, SE1, KZ1, AZ1, BG1, HU1, RO1, SI1 et sur la Ligue Europa et la Ligue Conférence (API-Football, score à 90 min, `UCL_EXTRA` dans `services/runs.py`). Les coupes relient les championnats entre eux : un club dominant un championnat faible est placé par les résultats européens de son championnat, pas mis au niveau des grands clubs. Même protocole (`backtest_ucl.py`, `--no-extra` pour l'ancien périmètre), saisons 2021-22 à 2025-26 :

| Périmètre | Matchs prédits | Brier brut | Brier corrigé (×1,2 walk-forward) | Brier référence | Gain corrigé |
|---|---|---|---|---|---|
| Ancien périmètre (5 championnats + C1) | 465 / 1206 | 0,553 | 0,551 | 0,625 | 11,8 % |
| Nouveau périmètre, tous | **994 / 1206** | 0,538 | **0,535** | 0,629 | 15,0 % |
| dont matchs déjà couverts avant | 465 | 0,550 | 0,546 | 0,625 | 12,7 % |
| dont matchs nouvellement couverts | 529 | 0,528 | 0,525 | 0,633 | 17,0 % |

Club hors cinq championnats contre club des cinq (374 matchs) : victoire du « petit » club prévue 22,3 % (brute) / 20,2 % (corrigée), observée 21,4 % ± 4,2 : pas de surévaluation des clubs de championnats faibles, aucun terme de force de championnat n'a été nécessaire. Restent non couverts 212 matchs, presque tous de tours préliminaires (clubs de championnats non chargés). Avec ce périmètre l'étirement ajusté walk-forward monte à 1,17-1,30 (1,25 sur toutes les prévisions) ; la constante de production reste 1,2. Les chiffres détaillés ci-dessous sont ceux de l'ancien périmètre ; [backtest-ucl.json](backtest-ucl.json) contient le nouveau.

**Données :** coupe d'Europe (C1) via API-Football, saisons 2018-19 à 2025-26, tours préliminaires compris, **score à 90 minutes** (prolongations et tirs au but ignorés) ; cinq grands championnats via openfootball (saisons 2018-19 à 2025-26 ; fin de saison 2025-26 partiellement absente du jeu openfootball, 2019-20 de Ligue 1 arrêtée). Saisons testées : 2021-22 à 2025-26, les précédentes servent d'échauffement.
**Protocole :** celui de la production pour la C1. Chaque semaine de C1, un seul modèle est réajusté sur tous les matchs des cinq championnats et de la C1 joués avant cette semaine (3 ans glissants), clubs vus dans moins de 20 matchs écartés, puis il prédit les matchs de C1 de la semaine dont il connaît les deux clubs.
**Référence naïve :** fréquences domicile / nul / extérieur des matchs de C1 antérieurs.
Résultats bruts : [backtest-ucl.json](backtest-ucl.json), script `backend/scripts/backtest_ucl.py`. Exécuté le 13 octobre 2026, relancé le 14 octobre avec la correction de calibration (mêmes 465 prévisions brutes, chiffres ci-dessous inchangés).

| Périmètre | Matchs prédits | Brier 1X2 modèle | Brier référence | Gain | Log-loss modèle | Log-loss référence |
|---|---|---|---|---|---|---|
| Toutes saisons | 465 | **0,553** | 0,625 | 11,5 % | **0,938** | 1,035 |
| 2021-22 | 76 | **0,584** | 0,644 | 9,4 % | **0,982** | 1,063 |
| 2022-23 | 80 | **0,548** | 0,634 | 13,6 % | **0,935** | 1,054 |
| 2023-24 | 77 | **0,578** | 0,638 | 9,4 % | **0,977** | 1,057 |
| 2024-25 | 109 | **0,543** | 0,610 | 10,9 % | **0,917** | 1,008 |
| 2025-26 | 123 | **0,531** | 0,612 | 13,2 % | **0,906** | 1,016 |
| Poules / phase de ligue | 304 | **0,533** | 0,620 | 14,0 % | **0,911** | 1,027 |
| Élimination directe (finales comprises) | 144 | **0,600** | 0,634 | 5,4 % | **1,001** | 1,053 |
| Deux clubs des cinq championnats | 311 | **0,565** | 0,624 | 9,5 % | **0,956** | 1,034 |
| Au moins un club hors cinq championnats | 154 | **0,530** | 0,627 | 15,4 % | **0,903** | 1,038 |

Plus c'est bas, mieux c'est. (Élimination directe : 139 matchs + 5 finales, valeurs combinées. Les 17 autres matchs prédits sont des tours préliminaires.)

**Matchs écartés : 741 sur 1 206** (452 de tours préliminaires, 272 de poules / phase de ligue, 17 d'élimination directe), toujours parce qu'un club hors des cinq championnats a moins de 20 matchs dans la fenêtre (Benfica, Porto, PSV, Celtic, Galatasaray… selon les saisons ; Bodø/Glimt, Young Boys, Feyenoord, Fenerbahçe… jamais). Aucun club des cinq championnats n'est perdu par un problème d'orthographe des noms.

**Calibration, favoris à 70 % et plus** (domicile ou extérieur) :

| Prévu (tranche) | Matchs | Prévu moyen | Observé |
|---|---|---|---|
| 70-75 % | 18 | 72 % | 83 % |
| 75-80 % | 16 | 78 % | 94 % |
| 80-85 % | 6 | 82 % | 50 % |
| 85-90 % | 3 | 89 % | 100 % |
| 90 % et plus | 4 | 93 % | 100 % |
| **Total 70 %+** | **47** | **78 %** | **85 % (± 10 points)** |

Victoire à domicile, toutes probabilités : prévu 25 % → observé 23 %, 35 % → 36 %, 45 % → 48 %, 54 % → 60 %, 65 % → 80 % (56 matchs), 75 % → 86 % (28 matchs). Moyennes : domicile prévu 44,5 % / observé 48,4 % ; nul prévu 23,0 % / observé 19,1 %.

### Correction de calibration (14 octobre 2026)
**Correction retenue : écart de force étiré de 20 %.** Pour un match de C1, l'écart `log(buts attendus domicile / buts attendus extérieur)` (avantage du terrain compris) est multiplié par `stretch` ; le niveau de buts du match (moyenne géométrique des deux) est conservé. Un seul paramètre, appliqué aux buts attendus : les probabilités 1X2, le score estimé et les échantillons d'incertitude restent cohérents entre eux. En production : `UCL_CALIBRATION = RateCalibration(stretch=1.2)` dans `services/runs.py` (`None` la coupe), appliqué uniquement à la C1.

**Protocole sans regard vers l'avenir :** les saisons 2019-20 et 2020-21 sont prédites (même protocole hebdomadaire) uniquement pour ajuster la première correction. Pour chaque saison testée S, `stretch` est ajusté (maximum de vraisemblance 1X2) sur les prévisions des saisons antérieures à S seulement, puis appliqué à S. Valeurs obtenues : 1,09 (2021-22), 1,07 (2022-23), 1,19 (2023-24), 1,19 (2024-25), 1,15 (2025-26) ; sur les 579 prévisions 2019-26 : 1,196 → constante de production 1,2.

**Avantage du terrain propre à la C1 : essayé, rejeté.** Ajusté avec l'étirement, il fait pire hors échantillon (Brier 0,556, log-loss 0,942, moins bien que le modèle brut) : la saison 2020-21 à huis clos (domicile 36 % observé pour 44 % prévu) le tire vers le négatif, et d'une saison à l'autre il varie de −0,06 à +0,12 sans tendance stable. Seul : Brier 0,558. Sur l'ensemble des saisons testées (ajustement a posteriori, donc optimiste) il ne vaudrait que +0,04.

| Périmètre | Matchs | Brier brut | Brier corrigé | Gain brut → corrigé | Log-loss brut | Log-loss corrigé |
|---|---|---|---|---|---|---|
| Toutes saisons | 465 | 0,553 | **0,551** | 11,5 → 11,8 % | 0,938 | **0,935** |
| 2021-22 | 76 | 0,584 | 0,584 | 9,4 → 9,4 % | 0,982 | 0,982 |
| 2022-23 | 80 | 0,548 | **0,544** | 13,6 → 14,1 % | 0,935 | **0,930** |
| 2023-24 | 77 | 0,578 | **0,575** | 9,4 → 9,8 % | 0,977 | **0,976** |
| 2024-25 | 109 | **0,543** | 0,544 | 10,9 → 10,7 % | **0,917** | 0,919 |
| 2025-26 | 123 | 0,531 | **0,527** | 13,2 → 13,9 % | 0,906 | **0,899** |
| Poules / phase de ligue | 304 | 0,533 | **0,530** | 14,0 → 14,6 % | 0,911 | **0,907** |
| Élimination directe (hors finales) | 139 | **0,601** | 0,602 | 5,2 → 5,0 % | **1,002** | 1,003 |
| Deux clubs des cinq championnats | 311 | 0,565 | **0,562** | 9,5 → 10,0 % | 0,956 | **0,952** |
| Au moins un club hors cinq championnats | 154 | 0,530 | 0,530 | 15,4 → 15,4 % | 0,903 | 0,903 |

Écart de Brier par match (brut − corrigé) : **+0,0020, intervalle à 95 % [−0,0013 ; +0,0050]** (bootstrap apparié) : l'amélioration est faible et **pas statistiquement établie**. Le plafond est bas : même ajustée a posteriori sur les 465 matchs testés, la meilleure correction n'atteint que 0,550.

**Calibration 1X2, les trois issues ensemble** (1 395 probabilités) :

| Prévu (tranche) | Brut : n | Brut : prévu → observé | Corrigé : n | Corrigé : prévu → observé |
|---|---|---|---|---|
| 0-10 % | 37 | 6 % → 5 % | 66 | 6 % → 8 % |
| 10-20 % | 204 | 16 % → 13 % | 229 | 16 % → 16 % |
| 20-30 % | 538 | 25 % → 22 % | 499 | 25 % → 22 % |
| 30-40 % | 204 | 35 % → 35 % | 182 | 35 % → 35 % |
| 40-50 % | 173 | 45 % → 47 % | 155 | 45 % → 43 % |
| 50-60 % | 117 | 55 % → 60 % | 113 | 55 % → 64 % |
| 60-70 % | 75 | 64 % → **73 %** | 88 | 65 % → **66 %** |
| 70-80 % | 34 | 75 % → **88 %** | 35 | 75 % → **77 %** |
| 80-90 % | 9 | 84 % → 67 % | 21 | 84 % → 81 % |
| 90 % et plus | 4 | 93 % → 100 % | 7 | 95 % → 100 % |

**Favoris à 70 %+ :** brut 47 matchs, prévu 78 % → observé 85 % ; **corrigé 63 matchs, prévu 80,0 % → observé 81,0 % (± 10 points)**. La sous-confiance sur les favoris disparaît. Reste : le nul encore surestimé (prévu 22,3 % / observé 19,1 %, brut 23,0 %) et la tranche 50-60 % un peu sous-estimée (victoire à domicile prévue 55 % → observée 64 %, 72 matchs), écart que l'avantage du terrain spécifique n'a pas su corriger hors échantillon.

**Rentabilité : toujours non mesurable.** Pas de cote historique de C1 (API-Football `/odds` vide pour les matchs passés, The Odds API historique payant) : aucun ROI ni CLV possible.

### Ce que cela montre
- **Le modèle inter-championnats a un vrai pouvoir prédictif en C1** : Brier inférieur de 11,5 % à la référence, entre 9 et 14 % selon la saison, comparable aux 8 à 11 % des championnats.
- **Le gain est faible en élimination directe (5 %)** : affiches plus équilibrées entre grands clubs ; le modèle ignore aussi le score du match aller (hypothèse, non testée ici).
- **Modèle brut : pas de surconfiance sur les favoris à 70 %+**, plutôt l'inverse : 85 % de victoires pour 78 % annoncés (47 matchs, ± 10 points). **Corrigé (écart étiré de 20 %) : 81 % pour 80 % annoncés** sur 63 matchs. Le gain global de la correction est faible (Brier 0,553 → 0,551, non significatif) mais elle rend les probabilités hautes exploitables sans biais connu.
- La sous-estimation apparente de l'avantage du terrain en C1 (domicile 44,5 % prévu / 48,4 % observé) ne se corrige pas de façon stable hors échantillon : non corrigée.

### Ce que cela ne montre pas
- **Aucune rentabilité, avant comme après correction.** API-Football `/odds` ne renvoie rien pour les matchs passés de C1 (0 résultat, testé sur un match et sur la saison 2023) : pas de cote de clôture historique, donc aucune mesure de valeur.
- **Échantillon réduit** : 465 matchs au total, 47 favoris à 70 %+. La calibration haute est indicative, pas établie à quelques points près.
- Le modèle ne couvre que 39 % des matchs de C1 (ceux entre clubs connus), essentiellement les affiches entre clubs des cinq championnats.
