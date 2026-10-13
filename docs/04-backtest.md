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

## Score estimé : l'issue d'abord (11 octobre 2026)
Même backtest (10 174 matchs, 5 championnats, hors échantillon). L'ancien score estimé prenait le score exact le plus probable : c'est souvent 1-1 même quand une équipe est favorite (domicile à 50 % → 1-1 reste le score le plus probable). Résultat : **55,7 % de nuls annoncés pour 24,6 % de nuls réels**, issue juste dans **42,0 %** des cas.
Nouvelle règle : on choisit d'abord l'issue la plus probable (1, N ou 2), puis le score le plus probable dans cette issue. Un nul n'est annoncé que s'il est vraiment l'issue la plus probable (0,5 % des matchs). **Issue juste dans 52,6 %** des cas (Ligue 1 39,5 → 51,4 %, Premier League 42,9 → 54,0 %, LaLiga 45,4 → 52,4 %, Bundesliga 38,0 → 51,3 %, Serie A 43,3 → 53,6 %). Le score exact tombe un peu moins souvent (12,5 → 11,1 %).

**Avantage du terrain :** il est ajusté par championnat (modèle refait pour chaque championnat) et il est bien calibré : victoires à domicile prévues 43,2 %, observées 43,5 % ; extérieur prévu 32,4 %, observé 32,0 % ; écart de buts domicile prévu +0,27, observé +0,27. Sur les matchs serrés (écart 1/2 < 10 points, 1 965 matchs) : domicile prévu 36,2 %, observé 37,6 %, dans la marge d'erreur. Pas de correction nécessaire.

## Ligue des champions (modèle inter-championnats)
**Données :** coupe d'Europe (C1) via API-Football, saisons 2018-19 à 2025-26, tours préliminaires compris, **score à 90 minutes** (prolongations et tirs au but ignorés) ; cinq grands championnats via openfootball (saisons 2018-19 à 2025-26 ; fin de saison 2025-26 partiellement absente du jeu openfootball, 2019-20 de Ligue 1 arrêtée). Saisons testées : 2021-22 à 2025-26, les précédentes servent d'échauffement.
**Protocole :** celui de la production pour la C1. Chaque semaine de C1, un seul modèle est réajusté sur tous les matchs des cinq championnats et de la C1 joués avant cette semaine (3 ans glissants), clubs vus dans moins de 20 matchs écartés, puis il prédit les matchs de C1 de la semaine dont il connaît les deux clubs.
**Référence naïve :** fréquences domicile / nul / extérieur des matchs de C1 antérieurs.
Résultats bruts : [backtest-ucl.json](backtest-ucl.json), script `backend/scripts/backtest_ucl.py`. Exécuté le 13 octobre 2026.

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

### Ce que cela montre
- **Le modèle inter-championnats a un vrai pouvoir prédictif en C1** : Brier inférieur de 11,5 % à la référence, entre 9 et 14 % selon la saison, comparable aux 8 à 11 % des championnats.
- **Le gain est faible en élimination directe (5 %)** : affiches plus équilibrées entre grands clubs ; le modèle ignore aussi le score du match aller (hypothèse, non testée ici).
- **Pas de surconfiance sur les favoris à 70 %+**, plutôt l'inverse : 85 % de victoires pour 78 % annoncés, mais sur 47 matchs seulement (marge ± 10 points). L'avantage du terrain en C1 est sous-estimé (le modèle utilise un seul avantage du terrain pour tous les championnats et la C1) et le nul est surestimé.

### Ce que cela ne montre pas
- **Aucune rentabilité.** API-Football `/odds` ne renvoie rien pour les matchs passés de C1 (0 résultat, testé sur un match et sur la saison 2023) : pas de cote de clôture historique, donc aucune mesure de valeur.
- **Échantillon réduit** : 465 matchs au total, 47 favoris à 70 %+. La calibration haute est indicative, pas établie à quelques points près.
- Le modèle ne couvre que 39 % des matchs de C1 (ceux entre clubs connus), essentiellement les affiches entre clubs des cinq championnats.
