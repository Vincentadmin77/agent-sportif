# Marchés de mi-temps (27 octobre 2026)

**Demande (Vincent, 27/10) :** « il y a énormément de cotes et toutes doivent être étudiées pour trouver la plus forte probabilité ». Premier lot : tout ce qui se règle sur le score à la mi-temps et le score final.

**Modèle :** `backend/sportai/engine/football/halves.py`. Les buts attendus du match (λ, μ, ceux du modèle de score) sont partagés entre les deux mi-temps : la 1re suit deux lois de Poisson indépendantes de moyennes f·λ et f·μ, la 2e (1−f)·λ et (1−f)·μ, où f est la part des buts marqués avant la pause dans le championnat sur les 3 saisons précédentes (Ligue 1 0.436, Premier League 0.438, LaLiga 0.439, Bundesliga 0.456, Serie A 0.428, Brasileirão 0.45 ; 0,44 ailleurs). Les deux mi-temps étant indépendantes, chaque pari (mi-temps, 2e mi-temps, mi-temps / fin de match, « dans chaque mi-temps », « au moins une mi-temps »…) est calculé et réglé sur les mêmes poids.

**Backtest :** `backend/scripts/backtest_halftime.py`, mêmes données (cache API-Football, aucune requête), même Dixon-Coles réajusté chaque semaine et même règle de décision que [11-backtest-buts.md](11-backtest-buts.md) : validé si Brier et log-loss font mieux que la fréquence du championnat sur 3 ans, intervalle à 95 % de l'écart de Brier entièrement sous 0, et aucun championnat pire de plus de 0,002. Test : saisons 2025-26 et début 2026-27 (Brasileirão 2025-2026), 2583 matchs. Pas de cotes historiques de mi-temps : le test porte sur la justesse des probabilités, pas sur un gain. Résultats bruts : [backtest-mi-temps.json](backtest-mi-temps.json). Beaucoup de marchés testés à la fois : quelques « validés » de justesse peuvent l'être par hasard.

« Zone 60 % » : les matchs où le modèle donnait au moins 60 %, ce qu'aurait donné une Confiance élevée (prévu → observé).

| Pari | Observé | Prévu | Écart de Brier [IC 95 %] | Écart log-loss | Zone 60 % | Verdict |
|---|---|---|---|---|---|---|
| ht_1x2:home | 36 % | 33 % | -0.0110 [-0.0150 ; -0.0070] | -0.0249 | 47 : 66 → 62 % | **validé** |
| ht_1x2:draw | 40 % | 41 % | -0.0006 [-0.0025 ; +0.0011] | -0.0014 | – | non validé |
| ht_1x2:away | 24 % | 25 % | -0.0088 [-0.0120 ; -0.0055] | -0.0241 | 12 : 64 → 42 % | **validé** |
| 2h_1x2:home | 39 % | 37 % | -0.0151 [-0.0196 ; -0.0106] | -0.0328 | 110 : 66 → 68 % | **validé** |
| 2h_1x2:draw | 35 % | 36 % | -0.0014 [-0.0032 ; +0.0004] | -0.0032 | – | non validé |
| 2h_1x2:away | 26 % | 27 % | -0.0088 [-0.0126 ; -0.0050] | -0.0227 | 21 : 65 → 62 % | **validé** |
| ht_dc:1X | 76 % | 75 % | -0.0088 [-0.0120 ; -0.0055] | -0.0241 | 2361 : 77 → 78 % | **validé** |
| ht_dc:X2 | 64 % | 67 % | -0.0110 [-0.0150 ; -0.0070] | -0.0249 | 1918 : 72 → 69 % | **validé** |
| ht_dc:12 | 60 % | 59 % | -0.0006 [-0.0025 ; +0.0011] | -0.0014 | 991 : 64 → 64 % | non validé |
| 2h_dc:1X | 74 % | 73 % | -0.0088 [-0.0126 ; -0.0050] | -0.0227 | 2212 : 76 → 76 % | **validé** |
| 2h_dc:X2 | 61 % | 63 % | -0.0151 [-0.0196 ; -0.0106] | -0.0328 | 1659 : 71 → 69 % | **validé** |
| 2h_dc:12 | 65 % | 64 % | -0.0014 [-0.0032 ; +0.0004] | -0.0032 | 2083 : 66 → 66 % | non validé |
| ht_totals:over@0.5 | 71 % | 69 % | +0.0009 [-0.0011 ; +0.0031] | +0.0020 | 2389 : 70 → 71 % | non validé |
| ht_totals:under@0.5 | 29 % | 31 % | +0.0009 [-0.0011 ; +0.0031] | +0.0020 | – | non validé |
| ht_totals:over@1.5 | 33 % | 34 % | -0.0006 [-0.0031 ; +0.0018] | -0.0014 | 14 : 63 → 71 % | non validé |
| ht_totals:under@1.5 | 67 % | 66 % | -0.0006 [-0.0031 ; +0.0018] | -0.0014 | 2026 : 69 → 69 % | non validé |
| ht_totals:over@2.5 | 11 % | 13 % | -0.0004 [-0.0016 ; +0.0006] | -0.0029 | – | non validé |
| ht_totals:under@2.5 | 89 % | 87 % | -0.0004 [-0.0016 ; +0.0006] | -0.0029 | 2582 : 87 → 89 % | non validé |
| 2h_totals:over@0.5 | 80 % | 77 % | +0.0015 [-0.0004 ; +0.0033] | +0.0033 | 2571 : 77 → 80 % | non validé |
| 2h_totals:under@0.5 | 20 % | 23 % | +0.0015 [-0.0004 ; +0.0033] | +0.0033 | – | non validé |
| 2h_totals:over@1.5 | 46 % | 44 % | +0.0021 [-0.0010 ; +0.0051] | +0.0046 | 80 : 64 → 62 % | non validé |
| 2h_totals:under@1.5 | 54 % | 56 % | +0.0021 [-0.0010 ; +0.0051] | +0.0046 | 895 : 65 → 58 % | non validé |
| 2h_totals:over@2.5 | 20 % | 19 % | -0.0006 [-0.0025 ; +0.0014] | -0.0005 | – | non validé |
| 2h_totals:under@2.5 | 80 % | 81 % | -0.0006 [-0.0025 ; +0.0014] | -0.0005 | 2562 : 81 → 80 % | non validé |
| ht_team_total_home:over@0.5 | 50 % | 48 % | -0.0076 [-0.0115 ; -0.0037] | -0.0159 | 338 : 66 → 69 % | non validé |
| ht_team_total_home:under@0.5 | 50 % | 52 % | -0.0076 [-0.0115 ; -0.0037] | -0.0159 | 623 : 66 → 61 % | non validé |
| ht_team_total_home:over@1.5 | 15 % | 15 % | -0.0043 [-0.0067 ; -0.0021] | -0.0160 | 1 : 63 → 0 % | **validé** |
| ht_team_total_home:under@1.5 | 85 % | 85 % | -0.0043 [-0.0067 ; -0.0021] | -0.0160 | 2558 : 85 → 86 % | **validé** |
| ht_team_total_away:over@0.5 | 39 % | 41 % | -0.0061 [-0.0097 ; -0.0027] | -0.0125 | 110 : 65 → 59 % | **validé** |
| ht_team_total_away:under@0.5 | 61 % | 59 % | -0.0061 [-0.0097 ; -0.0027] | -0.0125 | 1335 : 67 → 67 % | **validé** |
| ht_team_total_away:over@1.5 | 10 % | 11 % | -0.0021 [-0.0038 ; -0.0004] | -0.0103 | – | **validé** |
| ht_team_total_away:under@1.5 | 90 % | 89 % | -0.0021 [-0.0038 ; -0.0004] | -0.0103 | 2574 : 90 → 90 % | **validé** |
| 2h_team_total_home:over@0.5 | 59 % | 55 % | -0.0077 [-0.0119 ; -0.0036] | -0.0165 | 806 : 68 → 70 % | **validé** |
| 2h_team_total_home:under@0.5 | 41 % | 45 % | -0.0077 [-0.0119 ; -0.0036] | -0.0165 | 209 : 65 → 59 % | **validé** |
| 2h_team_total_home:over@1.5 | 22 % | 20 % | -0.0050 [-0.0081 ; -0.0017] | -0.0142 | 8 : 63 → 50 % | **validé** |
| 2h_team_total_home:under@1.5 | 78 % | 80 % | -0.0050 [-0.0081 ; -0.0017] | -0.0142 | 2476 : 81 → 79 % | **validé** |
| 2h_team_total_away:over@0.5 | 50 % | 47 % | -0.0057 [-0.0095 ; -0.0018] | -0.0115 | 357 : 66 → 68 % | **validé** |
| 2h_team_total_away:under@0.5 | 50 % | 53 % | -0.0057 [-0.0095 ; -0.0018] | -0.0115 | 684 : 66 → 61 % | **validé** |
| 2h_team_total_away:over@1.5 | 15 % | 15 % | -0.0019 [-0.0042 ; +0.0004] | -0.0053 | – | non validé |
| 2h_team_total_away:under@1.5 | 85 % | 85 % | -0.0019 [-0.0042 ; +0.0004] | -0.0053 | 2558 : 86 → 85 % | non validé |
| ht_btts:yes | 18 % | 19 % | +0.0006 [-0.0006 ; +0.0018] | +0.0016 | – | non validé |
| ht_btts:no | 82 % | 81 % | +0.0006 [-0.0006 ; +0.0018] | +0.0016 | 2583 : 81 → 82 % | non validé |
| 2h_btts:yes | 29 % | 25 % | +0.0018 [+0.0000 ; +0.0036] | +0.0057 | – | non validé |
| 2h_btts:no | 71 % | 75 % | +0.0018 [+0.0000 ; +0.0036] | +0.0057 | 2572 : 75 → 71 % | non validé |
| ht_odd_even:odd | 46 % | 45 % | -0.0000 [-0.0009 ; +0.0008] | -0.0001 | – | non validé |
| 2h_odd_even:odd | 49 % | 47 % | +0.0004 [-0.0006 ; +0.0015] | +0.0008 | – | non validé |
| htft:home/home | 28 % | 27 % | -0.0139 [-0.0181 ; -0.0097] | -0.0354 | 30 : 66 → 63 % | **validé** |
| htft:home/draw | 5 % | 5 % | -0.0001 [-0.0003 ; +0.0002] | -0.0006 | – | non validé |
| htft:home/away | 2 % | 2 % | +0.0001 [-0.0001 ; +0.0002] | +0.0021 | – | non validé |
| htft:draw/home | 15 % | 15 % | -0.0021 [-0.0032 ; -0.0010] | -0.0093 | – | **validé** |
| htft:draw/draw | 15 % | 15 % | -0.0009 [-0.0019 ; +0.0001] | -0.0037 | – | non validé |
| htft:draw/away | 11 % | 11 % | -0.0027 [-0.0040 ; -0.0015] | -0.0136 | – | **validé** |
| htft:away/home | 3 % | 2 % | -0.0001 [-0.0001 ; +0.0001] | -0.0012 | – | non validé |
| htft:away/draw | 6 % | 5 % | -0.0000 [-0.0002 ; +0.0001] | -0.0001 | – | non validé |
| htft:away/away | 16 % | 19 % | -0.0080 [-0.0114 ; -0.0048] | -0.0295 | 8 : 63 → 38 % | **validé** |
| highest_half:1st | 28 % | 31 % | +0.0006 [-0.0000 ; +0.0011] | +0.0013 | – | non validé |
| highest_half:2nd | 46 % | 44 % | +0.0003 [-0.0006 ; +0.0011] | +0.0006 | – | non validé |
| highest_half:draw | 26 % | 26 % | -0.0002 [-0.0013 ; +0.0008] | -0.0006 | – | non validé |
| goal_both_halves:yes | 57 % | 54 % | +0.0031 [-0.0003 ; +0.0064] | +0.0062 | 647 : 65 → 62 % | non validé |
| goal_both_halves:no | 43 % | 46 % | +0.0031 [-0.0003 ; +0.0064] | +0.0062 | 157 : 64 → 48 % | non validé |
| team_both_halves_home:yes | 30 % | 27 % | -0.0086 [-0.0126 ; -0.0045] | -0.0190 | 30 : 67 → 83 % | **validé** |
| team_both_halves_away:yes | 20 % | 20 % | -0.0049 [-0.0080 ; -0.0019] | -0.0144 | 10 : 64 → 50 % | **validé** |
| win_both_halves_home:yes | 14 % | 14 % | -0.0076 [-0.0106 ; -0.0046] | -0.0301 | 1 : 67 → 0 % | **validé** |
| win_both_halves_away:yes | 6 % | 8 % | -0.0015 [-0.0033 ; +0.0003] | -0.0134 | – | non validé |
| win_either_half_home:yes | 60 % | 56 % | -0.0202 [-0.0254 ; -0.0151] | -0.0444 | 1049 : 71 → 73 % | **validé** |
| win_either_half_away:yes | 44 % | 45 % | -0.0213 [-0.0263 ; -0.0162] | -0.0445 | 437 : 68 → 69 % | **validé** |
| btts_both_halves:yes | 6 % | 5 % | +0.0003 [-0.0001 ; +0.0006] | +0.0031 | – | non validé |

## Ce qu'on en retient

- Le modèle sait qui domine une mi-temps : victoire à la mi-temps ou en 2e mi-temps, double chance par mi-temps, « gagne au moins une mi-temps », mi-temps / fin de match, buts d'une équipe par mi-temps. Ces paris sont validés.
- Pour le nombre total de buts par mi-temps, il ne fait pas mieux que la fréquence du championnat (comme pour les buts du match) : non validés. Il sous-estime un peu les buts de 2e mi-temps (77 % prévu contre 80 % observé pour « au moins un but en 2e mi-temps »).
- La plupart des paris de mi-temps ont une chance moyenne : peu atteignent 60 % à une cote de 1,60 ou plus. Ils entrent dans les cotes étudiées de chaque match et ne sont retenus que s'ils passent les mêmes règles que les autres (confiance, accord avec les bookmakers, valeur).

## Branchement

- `services/mi_temps.py` lit les paris de mi-temps du flux de cotes (les noms exacts sont dans `HALF_BETS`), enlève la marge des bookmakers sur les groupes complets (médiane des bookmakers ; sinon 1/cote) et les calcule. `runs.half_markets` les range sur le match (`mi_temps_cotes`) et les retire de la liste « non analysé ».
- `pronostic.legs_from_db` les ajoute aux cotes du match : validés comme les autres marchés validés, les autres en essai avec les mêmes garde-fous. Lignes en quart (x,25 / x,75) non calculées.
- Réglage : sur le score à la mi-temps (`score_mi_temps`, écrit par le suivi en direct depuis API-Football) et le score final. Un match terminé sans score à la pause laisse le pari en attente.
