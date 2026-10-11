# Backtest des marchés de buts : les deux équipes marquent, plus de 1,5 / 2,5 / 3,5 buts

**Question :** un modèle enrichi (forme des équipes, xG, tirs) prévoit-il mieux ces marchés qu'une simple fréquence historique ? Le backtest Dixon-Coles ([04-backtest.md](04-backtest.md)) avait montré que non pour le modèle seul (Brier +2,5 buts égal à la référence), d'où la désactivation de ces marchés.
**Données :** API-Football v3 (offre PRO) : résultats et statistiques de chaque match (xG, tirs cadrés, tirs totaux). Ligue 1, Premier League, LaLiga, Bundesliga, Serie A, saisons 2020-21 à 2026-27 (jusqu'au 11 octobre 2026) ; Brasileirão 2021 à 2026. Saison régulière uniquement (barrages exclus). 13 200 matchs au total. L'xG n'existe qu'à partir de la mi-2022 : avant, il est remplacé par les tirs cadrés multipliés par le taux de conversion moyen du championnat. Tirs cadrés et tirs totaux sont disponibles partout.
**Coût :** 721 requêtes API-Football (714 réponses mises en cache, 6 refusées par la limite de 300 requêtes par minute, partagée avec le produit en ligne, et 1 contrôle du quota), sur un budget de 1 500. Le quota quotidien restant n'est jamais descendu sous 6 800. Les réponses brutes sont dans `/mnt/project-files/agent-sportif/data/api-football/` (un fichier JSON compressé par requête). Le script les relit sans rien retélécharger.
**Script :** `backend/scripts/backtest_goals.py`. Résultats bruts : [backtest-buts.json](backtest-buts.json). Exécuté le 11 octobre 2026.

## Protocole

- **Chronologique, hors échantillon.** Chaque semaine, Dixon-Coles est réajusté sur les 3 années précédentes (comme dans `backtest.py`) et la régression du modèle enrichi est réajustée sur tous les matchs passés des 6 championnats. Les variables d'un match sont calculées uniquement à partir des matchs commencés avant lui. Les équipes promues sans historique suffisant sont ignorées, comme en production. Les trois prévisions portent exactement sur les mêmes matchs.
- **Trois prévisions par marché :**
  - **référence** : fréquence du marché dans le championnat sur les 3 années précédentes, recalculée chaque semaine (passé uniquement) ;
  - **Dixon-Coles seul** : probabilité tirée de la matrice des scores ;
  - **modèle enrichi** : régression logistique régularisée (L2), une par marché. Elle combine la probabilité Dixon-Coles et ses buts attendus λ, μ avec la forme des équipes : buts marqués et encaissés à domicile par l'équipe qui reçoit et à l'extérieur par l'équipe qui se déplace (saison en cours, ramenés vers la moyenne du championnat quand il y a peu de matchs), xG pour et contre et tirs cadrés sur les 8 derniers matchs, tirs totaux pour et contre (à domicile pour l'équipe qui reçoit, à l'extérieur pour l'autre), régularité des tirs (coefficient de variation sur 10 matchs, et son interaction avec le volume de tirs), taux de buts du championnat cette saison, et taux « les deux marquent » et « +2,5 » de chaque équipe sur 19 matchs.
- **Choix du modèle sur une saison de validation.** Une variante Poisson (un modèle de buts par équipe qui alimente la matrice des scores) et trois niveaux de régularisation ont aussi été essayés. Le choix s'est fait sur la saison 2024-25 seulement (Brasileirão 2024). La régression logistique l'emporte sur les 4 marchés. **Test :** saison 2025-26 et début de 2026-27 (Brasileirão 2025 et 2026), soit 2 583 matchs jamais utilisés pour ajuster ni choisir quoi que ce soit.
- **Mesures :** score de Brier et log-loss (plus c'est bas, mieux c'est). L'intervalle de confiance à 95 % de l'écart avec la référence est obtenu par bootstrap apparié par match. La calibration est mesurée par déciles de probabilité prévue.
- **Règle de décision, fixée avant de regarder le test :** un marché est validé si, sur l'ensemble du test, le modèle enrichi fait mieux que la référence en Brier **et** en log-loss, si l'intervalle à 95 % de l'écart de Brier est entièrement sous 0, et si aucun championnat n'est pire que la référence de plus de 0,002 en Brier.

## Résultats sur le test (2 583 matchs)

### Les deux équipes marquent (oui) — **non validé**

| Championnat | Matchs | Fréquence observée | Brier modèle enrichi | Brier DC | Brier référence | Log-loss modèle | Log-loss DC | Log-loss référence |
|---|---|---|---|---|---|---|---|---|
| Ligue 1 | 339 | 51 % | 0,2521 | 0,2539 | 0,2517 | 0,6974 | 0,7019 | 0,6966 |
| Premier League | 421 | 57 % | 0,2455 | 0,2466 | 0,2456 | 0,6841 | 0,6865 | 0,6844 |
| LaLiga | 420 | 57 % | 0,2442 | 0,2513 | 0,2472 | 0,6815 | 0,6966 | 0,6876 |
| Bundesliga | 330 | 63 % | 0,2361 | 0,2437 | 0,2343 | 0,6651 | 0,6805 | 0,6616 |
| Serie A | 427 | 47 % | 0,2500 | 0,2497 | 0,2507 | 0,6932 | 0,6928 | 0,6946 |
| Brasileirão | 646 | 52 % | 0,2492 | 0,2537 | 0,2495 | 0,6916 | 0,7010 | 0,6922 |
| **Ensemble** | **2583** | 54 % | **0,2466** | 0,2502 | 0,2471 | **0,6864** | 0,6941 | 0,6873 |

- Écart Brier modèle − référence : -0,0004, IC 95 % [-0,0015 ; +0,0006] (IC 98,75 %, correction pour 4 marchés : [-0,0018 ; +0,0009]).
- Écart log-loss modèle − référence : -0,0009, IC 95 % [-0,0030 ; +0,0013].
- Dixon-Coles seul − référence (Brier) : +0,0032, IC 95 % [+0,0001 ; +0,0063] ; modèle enrichi − Dixon-Coles : -0,0036, IC 95 % [-0,0060 ; -0,0011].
- Robustesse (hors règle) : contre une référence plus réactive (fréquence des 365 derniers jours), écart Brier -0,0010, IC 95 % [-0,0022 ; +0,0002] ; sans le championnat le plus favorable (LaLiga), écart +0,0001, IC 95 % [-0,0010 ; +0,0012].
- Par saison : 2025-26 Brier 0,2472 contre 0,2472 ; 2026-27 (début) 0,2445 contre 0,2465.
- Calibration du modèle enrichi par déciles (prévu → observé) : 47 → 45 %, 49 → 49 %, 51 → 49 %, 52 → 53 %, 53 → 52 %, 54 → 59 %, 55 → 59 %, 56 → 55 %, 57 → 59 %, 60 → 59 %.
- Même tableau pour Dixon-Coles seul : 36 → 44 %, 42 → 52 %, 45 → 55 %, 47 → 46 %, 50 → 55 %, 52 → 56 %, 54 → 55 %, 57 → 61 %, 60 → 55 %, 65 → 61 %.

### Plus de 1,5 but — **validé**

| Championnat | Matchs | Fréquence observée | Brier modèle enrichi | Brier DC | Brier référence | Log-loss modèle | Log-loss DC | Log-loss référence |
|---|---|---|---|---|---|---|---|---|
| Ligue 1 | 339 | 73 % | 0,2028 | 0,2061 | 0,2021 | 0,5989 | 0,6095 | 0,5958 |
| Premier League | 421 | 79 % | 0,1654 | 0,1681 | 0,1662 | 0,5127 | 0,5186 | 0,5153 |
| LaLiga | 420 | 78 % | 0,1692 | 0,1722 | 0,1761 | 0,5199 | 0,5251 | 0,5379 |
| Bundesliga | 330 | 85 % | 0,1272 | 0,1308 | 0,1270 | 0,4218 | 0,4304 | 0,4222 |
| Serie A | 427 | 72 % | 0,2009 | 0,2033 | 0,2029 | 0,5909 | 0,5966 | 0,5960 |
| Brasileirão | 646 | 75 % | 0,1888 | 0,1913 | 0,1898 | 0,5648 | 0,5700 | 0,5675 |
| **Ensemble** | **2583** | 76 % | **0,1778** | 0,1806 | 0,1795 | **0,5395** | 0,5461 | 0,5441 |

- Écart Brier modèle − référence : -0,0017, IC 95 % [-0,0031 ; -0,0003] (IC 98,75 %, correction pour 4 marchés : [-0,0034 ; +0,0001]).
- Écart log-loss modèle − référence : -0,0045, IC 95 % [-0,0082 ; -0,0007].
- Dixon-Coles seul − référence (Brier) : +0,0011, IC 95 % [-0,0015 ; +0,0037] ; modèle enrichi − Dixon-Coles : -0,0028, IC 95 % [-0,0044 ; -0,0012].
- Robustesse (hors règle) : contre une référence plus réactive (fréquence des 365 derniers jours), écart Brier -0,0018, IC 95 % [-0,0031 ; -0,0004] ; sans le championnat le plus favorable (LaLiga), écart -0,0007, IC 95 % [-0,0021 ; +0,0008].
- Par saison : 2025-26 Brier 0,1806 contre 0,1824 ; 2026-27 (début) 0,1660 contre 0,1673.
- Calibration du modèle enrichi par déciles (prévu → observé) : 66 → 70 %, 70 → 69 %, 72 → 73 %, 73 → 72 %, 75 → 76 %, 76 → 81 %, 78 → 78 %, 79 → 79 %, 81 → 82 %, 85 → 83 %.
- Même tableau pour Dixon-Coles seul : 58 → 70 %, 65 → 72 %, 69 → 72 %, 71 → 75 %, 74 → 76 %, 76 → 76 %, 78 → 77 %, 81 → 78 %, 83 → 83 %, 88 → 85 %.

### Plus de 2,5 buts — **non validé**

| Championnat | Matchs | Fréquence observée | Brier modèle enrichi | Brier DC | Brier référence | Log-loss modèle | Log-loss DC | Log-loss référence |
|---|---|---|---|---|---|---|---|---|
| Ligue 1 | 339 | 53 % | 0,2456 | 0,2492 | 0,2492 | 0,6845 | 0,6925 | 0,6916 |
| Premier League | 421 | 55 % | 0,2487 | 0,2513 | 0,2479 | 0,6908 | 0,6968 | 0,6890 |
| LaLiga | 420 | 51 % | 0,2424 | 0,2465 | 0,2507 | 0,6777 | 0,6862 | 0,6946 |
| Bundesliga | 330 | 66 % | 0,2249 | 0,2295 | 0,2264 | 0,6410 | 0,6489 | 0,6454 |
| Serie A | 427 | 47 % | 0,2502 | 0,2560 | 0,2494 | 0,6937 | 0,7060 | 0,6920 |
| Brasileirão | 646 | 49 % | 0,2505 | 0,2542 | 0,2504 | 0,6943 | 0,7023 | 0,6939 |
| **Ensemble** | **2583** | 53 % | **0,2450** | 0,2490 | 0,2467 | **0,6828** | 0,6913 | 0,6864 |

- Écart Brier modèle − référence : -0,0017, IC 95 % [-0,0041 ; +0,0006] (IC 98,75 %, correction pour 4 marchés : [-0,0048 ; +0,0012]).
- Écart log-loss modèle − référence : -0,0036, IC 95 % [-0,0081 ; +0,0013].
- Dixon-Coles seul − référence (Brier) : +0,0023, IC 95 % [-0,0018 ; +0,0061] ; modèle enrichi − Dixon-Coles : -0,0040, IC 95 % [-0,0061 ; -0,0019].
- Robustesse (hors règle) : contre une référence plus réactive (fréquence des 365 derniers jours), écart Brier -0,0018, IC 95 % [-0,0040 ; +0,0003] ; sans le championnat le plus favorable (LaLiga), écart -0,0004, IC 95 % [-0,0029 ; +0,0020].
- Par saison : 2025-26 Brier 0,2459 contre 0,2465 ; 2026-27 (début) 0,2409 contre 0,2473.
- Calibration du modèle enrichi par déciles (prévu → observé) : 39 → 47 %, 43 → 43 %, 46 → 47 %, 48 → 48 %, 50 → 48 %, 52 → 53 %, 54 → 58 %, 56 → 57 %, 59 → 60 %, 65 → 65 %.
- Même tableau pour Dixon-Coles seul : 31 → 46 %, 38 → 47 %, 42 → 46 %, 45 → 48 %, 48 → 48 %, 51 → 61 %, 54 → 53 %, 58 → 53 %, 62 → 61 %, 70 → 64 %.

### Plus de 3,5 buts — **validé**

| Championnat | Matchs | Fréquence observée | Brier modèle enrichi | Brier DC | Brier référence | Log-loss modèle | Log-loss DC | Log-loss référence |
|---|---|---|---|---|---|---|---|---|
| Ligue 1 | 339 | 32 % | 0,2160 | 0,2208 | 0,2195 | 0,6229 | 0,6338 | 0,6310 |
| Premier League | 421 | 30 % | 0,2105 | 0,2132 | 0,2132 | 0,6120 | 0,6182 | 0,6180 |
| LaLiga | 420 | 26 % | 0,1885 | 0,1898 | 0,1932 | 0,5626 | 0,5667 | 0,5748 |
| Bundesliga | 330 | 46 % | 0,2422 | 0,2456 | 0,2502 | 0,6770 | 0,6856 | 0,6937 |
| Serie A | 427 | 22 % | 0,1759 | 0,1801 | 0,1753 | 0,5374 | 0,5474 | 0,5357 |
| Brasileirão | 646 | 25 % | 0,1889 | 0,1925 | 0,1874 | 0,5668 | 0,5772 | 0,5629 |
| **Ensemble** | **2583** | 29 % | **0,2006** | 0,2039 | 0,2028 | **0,5901** | 0,5985 | 0,5950 |

- Écart Brier modèle − référence : -0,0022, IC 95 % [-0,0043 ; -0,0001] (IC 98,75 %, correction pour 4 marchés : [-0,0049 ; +0,0004]).
- Écart log-loss modèle − référence : -0,0049, IC 95 % [-0,0098 ; +0,0003].
- Dixon-Coles seul − référence (Brier) : +0,0011, IC 95 % [-0,0023 ; +0,0044] ; modèle enrichi − Dixon-Coles : -0,0033, IC 95 % [-0,0050 ; -0,0017].
- Robustesse (hors règle) : contre une référence plus réactive (fréquence des 365 derniers jours), écart Brier -0,0012, IC 95 % [-0,0032 ; +0,0007] ; sans le championnat le plus favorable (Bundesliga), écart -0,0014, IC 95 % [-0,0035 ; +0,0008].
- Par saison : 2025-26 Brier 0,1993 contre 0,2015 ; 2026-27 (début) 0,2059 contre 0,2082.
- Calibration du modèle enrichi par déciles (prévu → observé) : 18 → 24 %, 21 → 21 %, 23 → 24 %, 25 → 23 %, 27 → 25 %, 29 → 26 %, 31 → 32 %, 34 → 32 %, 37 → 37 %, 44 → 48 %.
- Même tableau pour Dixon-Coles seul : 14 → 22 %, 19 → 25 %, 22 → 24 %, 24 → 25 %, 26 → 23 %, 29 → 34 %, 32 → 28 %, 36 → 30 %, 40 → 37 %, 49 → 44 %.

### Les tirs totaux et la régularité des tirs aident-ils ? (ablation)

Le modèle retenu a été réajusté sans les variables de tirs totaux et de régularité, puis sans la régularité seule, et enfin réduit à une simple recalibration de la probabilité Dixon-Coles. Mêmes matchs de test ; un écart négatif signifie que la variable retirée aidait.

| Marché | Brier modèle complet | Sans tirs totaux ni régularité | Sans régularité seule | Recalibration Dixon-Coles seule |
|---|---|---|---|---|
| Les deux équipes marquent | 0,2466 | 0,2465 (écart +0,0001, IC [-0,0002 ; +0,0003]) | 0,2465 (+0,0001, [-0,0001 ; +0,0003]) | 0,2474 (-0,0007, [-0,0017 ; +0,0002]) |
| Plus de 1,5 but | 0,1778 | 0,1777 (+0,0001, [-0,0003 ; +0,0005]) | 0,1778 (+0,0000, [-0,0003 ; +0,0003]) | 0,1782 (-0,0004, [-0,0013 ; +0,0004]) |
| Plus de 2,5 buts | 0,2450 | 0,2448 (+0,0001, [-0,0003 ; +0,0006]) | 0,2448 (+0,0001, [-0,0002 ; +0,0004]) | 0,2457 (-0,0007, [-0,0019 ; +0,0004]) |
| Plus de 3,5 buts | 0,2006 | 0,2006 (-0,0001, [-0,0003 ; +0,0002]) | 0,2005 (+0,0000, [-0,0001 ; +0,0002]) | 0,2025 (-0,0019, [-0,0032 ; -0,0007]) |

**Réponse : non.** Les tirs totaux pour et contre (séparés domicile / extérieur), leur régularité et l'interaction régularité × volume n'améliorent aucun marché. Les écarts sont de l'ordre de 0,0001, avec des intervalles centrés sur zéro, en Brier comme en log-loss. Leur effet est même très légèrement défavorable sur 3 marchés sur 4. L'information utile des tirs est déjà contenue dans l'xG et les tirs cadrés. Le modèle sans tirs totaux passe d'ailleurs la règle de décision exactement comme le modèle complet, sur les mêmes marchés. Les moyennes et écarts-types des tirs totaux de chaque équipe pour la saison 2026-27 (domicile et extérieur, pour et contre, avec effectifs) sont enregistrés à titre descriptif dans `/mnt/project-files/agent-sportif/data/tirs-equipes-2026.json` : à ce stade de la saison, 1 à 5 matchs par équipe et par lieu en Europe, 13 à 15 au Brasileirão.

## Ce que cela montre

- **Dixon-Coles seul fait en moyenne moins bien que la simple fréquence** sur les 4 marchés (Brier de +0,001 à +0,003 ; l'écart n'est significatif que pour « les deux marquent »). Les tableaux de calibration expliquent pourquoi : il est trop sûr de lui. Quand il annonce 31 % de « +2,5 », on observe 46 % ; quand il annonce 70 %, on observe 64 %. Les écarts qu'il voit entre les matchs sont exagérés. Le constat de [04-backtest.md](04-backtest.md) se confirme sur ces nouvelles données.
- **Le modèle enrichi corrige ce défaut.** Il fait mieux que Dixon-Coles sur les 4 marchés, avec des intervalles nettement sous 0 (Brier de -0,003 à -0,004). Ses probabilités sont bien calibrées sur tous les déciles, sauf le premier : il sous-estime un peu les matchs annoncés comme les plus fermés.
- **Contre la référence, le gain est faible** : 0,2 à 1,1 % du Brier. Il n'est statistiquement établi que pour +1,5 et +3,5 buts.
- **Le volume de tirs, la régularité des tirs et leur interaction n'apportent rien** (voir l'ablation).

## Ce que cela ne montre pas

- **Aucune rentabilité.** Il n'existe pas de cotes historiques sur les buts dans ces données : **faire mieux qu'une fréquence moyenne n'est pas faire mieux que les bookmakers.** Leurs cotes intègrent déjà la forme, l'xG, les compositions et l'argent du marché. Même un marché validé ici doit donc passer les contrôles existants de valeur et d'écart avec le marché avant toute sélection.
- **Le gain est fragile.** Avec une correction pour les 4 marchés testés (intervalle à 98,75 %), aucun écart ne reste entièrement sous 0. Pour +1,5 but, l'avantage repose surtout sur LaLiga : sans elle, l'écart tombe à -0,0007 avec un intervalle qui contient 0. Pour +3,5 buts, l'intervalle de l'écart de log-loss touche 0 ([-0,0098 ; +0,0003]). Face à une référence plus réactive (fréquence des 365 derniers jours), +1,5 tient encore ([-0,0031 ; -0,0004]) mais pas +3,5 ([-0,0032 ; +0,0007]). Une partie du gain vient sans doute de ce que le modèle suit l'évolution du nombre de buts dans le championnat au fil de la saison.
- La saison 2026-27 en cours ne compte qu'environ 500 matchs de test : on ne peut pas encore conclure saison par saison.

## Décision par marché

| Marché | Décision | Commentaire |
|---|---|---|
| Les deux équipes marquent | **Non validé** | Gain non significatif (IC [-0,0015 ; +0,0006]), nul hors LaLiga, et Bundesliga moins bonne que la référence (+0,0018 en Brier). Reste désactivé. |
| Plus de 1,5 but | **Validé (fragile)** | Les 4 critères sont remplis : Brier 0,1778 contre 0,1795, IC [-0,0031 ; -0,0003] ; log-loss 0,5395 contre 0,5441 ; pire championnat : Ligue 1 (+0,0007). Gain concentré sur LaLiga. |
| Plus de 2,5 buts | **Non validé** | Meilleur en moyenne (Brier 0,2450 contre 0,2467) mais l'IC contient 0 ([-0,0041 ; +0,0006]). Reste désactivé. |
| Plus de 3,5 buts | **Validé (fragile)** | Les 4 critères sont remplis : Brier 0,2006 contre 0,2028, IC [-0,0043 ; -0,0001] ; log-loss 0,5901 contre 0,5950 ; pire championnat : Brasileirão (+0,0014). Ne résiste pas à une référence sur 365 jours. |

Pour les deux marchés validés, le code du modèle est dans `backend/sportai/engine/football/goals_model.py` : `GoalsModel.fit` sur l'historique, `GoalsModel.predict` pour un match ; il n'est pas branché en production. Recommandation : l'activer **uniquement pour +1,5 et +3,5 buts**, toujours derrière les contrôles de valeur et d'écart avec le marché, avec des mises prudentes. Relancer ce backtest à la mi-saison 2026-27. Pour cela, déplacer les deux fichiers de liste de la saison en cours (`fixtures_league*_season2026.json.gz`) afin qu'ils soient retéléchargés ; le reste du cache est réutilisé. Étape suivante indispensable, comme pour le 1X2 : un backtest contre les cotes de clôture des marchés de buts.
