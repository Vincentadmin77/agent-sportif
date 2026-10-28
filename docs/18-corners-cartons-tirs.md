# Corners, cartons et tirs (27 octobre 2026)

**Demande (Vincent, 27/10) :** « toutes les cotes doivent être étudiées pour trouver la plus forte probabilité ». Après la mi-temps ([17-mi-temps.md](17-mi-temps.md)), deuxième lot : les corners, les cartons et les tirs.

## Modèle

`backend/sportai/engine/football/counts.py`, le même pour les cinq statistiques.

- **Moyennes d'équipe.** Pour chaque équipe, on prend ce qu'elle obtient et ce qu'elle concède : à domicile pour l'équipe qui reçoit, à l'extérieur pour l'autre. On garde ses 19 derniers matchs de ce type, en tirant vers la moyenne du championnat l'équivalent de 5 matchs. Le nombre attendu d'une équipe vaut son « pour », multiplié par le « contre » de l'adversaire, divisé par la moyenne du championnat.
- **Prudence.** La moyenne attendue est encore rapprochée de celle du championnat, d'une pente β (entre 0,2 et 0,7 selon le championnat et la statistique). β est mesurée sur les matchs passés : elle dit quelle part de l'écart prévu se retrouve vraiment dans les faits.
- **Loi.** Chaque équipe suit une loi binomiale négative, et sa dispersion est mesurée sur les matchs passés du championnat. Le total du match a sa propre loi, avec sa propre dispersion. Les deux équipes ne sont en effet pas indépendantes : celle qui domine prend les corners que l'autre ne prend pas.
- **Paris couverts.** Le plus et le moins du match, ceux de chaque équipe, et « qui en a le plus » (1N2).

## Backtest

Le script est `backend/scripts/backtest_counts.py <stat>`. Il lit le cache API-Football, sans aucune requête, sur les mêmes saisons de test que les buts. On a testé 2 614 matchs, soit les saisons 2025-26 et 2026-27 ; pour le Brasileirão, les saisons 2025 et 2026. Le calcul n'utilise que les matchs passés, et les moyennes du championnat sont recalculées chaque semaine.

La référence est la fréquence du pari dans le championnat sur les 3 années précédentes. La règle de décision est celle de [11-backtest-buts.md](11-backtest-buts.md). Un pari est validé si quatre conditions sont réunies :

- le Brier fait mieux que la référence ;
- la log-loss fait mieux que la référence ;
- l'intervalle à 95 % de l'écart de Brier est entièrement sous 0 ;
- aucun championnat n'est pire de plus de 0,002.

**Limites.**

- Il n'y a pas de cotes historiques pour ces marchés. Le test dit que les probabilités sont plus justes que la moyenne du championnat, pas qu'elles battent les bookmakers.
- Beaucoup de paris ont été testés à la fois, donc quelques validations de justesse peuvent être dues au hasard.
- Une ligne que le test n'a pas couverte (13,5 corners par exemple) reste « non validé ».

**Ce qu'il en ressort.**

- **Corners :** tous les paris d'équipe (2,5 à 6,5) et « qui en a le plus » (domicile, extérieur) sont validés, ainsi que les totaux 8,5, 10,5 et 12,5. Les totaux 7,5, 9,5 et 11,5 ne le sont pas, ni le nul.
- **Cartons :** les totaux et les cartons par équipe sont validés. « Qui en a le plus » ne l'est pas, sauf l'extérieur.
- **Tirs :** les tirs par équipe et « qui en a le plus » sont validés, ainsi que les totaux de 24,5 et plus.
- **Tirs cadrés :** les tirs cadrés par équipe, le 1N2 et les totaux 6,5 et 7,5 sont validés.

Les résultats bruts sont dans `backtest-corners.json`, `backtest-cartons.json`, `backtest-jaunes.json`, `backtest-tirs.json` et `backtest-tirs_cadres.json`.

## En production

`backend/sportai/services/comptages.py`, appelé par `runs.count_markets` juste après les paris de mi-temps.

- **Données d'équipe.** Elles viennent du même cache API-Football que le rafraîchissement quotidien des buts, qui télécharge les statistiques de chaque match terminé. Un résumé du jour (`comptages_resume.json.gz`) évite de relire le cache à chaque passage : il est relu dès qu'un nouveau fichier de statistiques arrive.
- **Pas de prix** pour une équipe dans trois cas : moins de 3 matchs connus, dernier match connu de plus de 45 jours, ou coupe d'Europe (pas de moyennes communes aux championnats).
- **Cotes.** Paris du flux : « Corners Over Under », « Home/Away Corners Over/Under », « Corners 1x2 », « Cards Over/Under », « Home/Away Team Total Cards », les cartons jaunes, « Total Shots », « Shots.1x2 », « Total ShotOnGoal » et « ShotOnTarget 1x2 ». Seules les lignes en ,5 sont prises (pas de remboursement). La marge est retirée bookmaker par bookmaker, puis on prend la médiane.
- **Paris retenus.** Les paris validés rejoignent les autres paris validés du match. Les autres sont « à l'essai », avec les mêmes règles que la mi-temps.
- **Règlement.** Par l'agent, sur les statistiques finales du suivi en direct (`direct.stats` de la page : corners, tirs, tirs cadrés, cartons jaunes et rouges). La page ne règle pas elle-même ces paris.

## Arbitre (28 octobre 2026)

**Question de Vincent :** « est-ce que la Top confiance tient compte de l'arbitre ? » (Genoa - Juventus, cartons moins de 4,5, arbitre Doveri). Avant ce jour, non : l'arbitre n'était qu'une phrase du contexte.

**Facteur arbitre.** Les cartons attendus des deux équipes et du match sont multipliés par un facteur : les cartons par match de l'arbitre dans le championnat, rapprochés de la moyenne du championnat de l'équivalent de 20 matchs, divisés par cette moyenne. Un arbitre inconnu a un facteur de 1. Sur l'échantillon, le facteur va de 0,89 à 1,10 (10e et 90e centiles).

**Backtest.** Le script est `backend/scripts/backtest_referee.py`. Il reprend les mêmes 2 614 matchs et le même calcul semaine par semaine, et compare pari par pari le modèle avec arbitre au modèle sans arbitre.

Résultats selon la force du rapprochement vers la moyenne :

| Rapprochement | Nettement mieux | Moins bien |
|---|---|---|
| 5 matchs | 2 paris sur 29 | aucun |
| 10 matchs | 6 sur 29 | aucun |
| 20 matchs | 14 sur 29 (totaux 2,5 à 5,5, cartons de l'équipe à l'extérieur) | aucun |

Les cartons jaunes seuls donnent le même résultat : 14 sur 29 à 20 matchs.

La valeur de 20 a été choisie sur ces mêmes saisons de test, donc le gain est un peu optimiste. Les résultats sont dans `backtest-arbitre-cartons.json` et `backtest-arbitre-jaunes.json`.

**En production.** L'arbitre vient du flux des matchs (`Fixture.referee`). L'historique de chaque arbitre est compté sur les matchs du cache, par championnat. Chaque ligne de cartons porte `arbitre` {nom, matchs, facteur}.

Exemple : Genoa - Juventus, avec Doveri (104 matchs, facteur 0,91). Pour « cartons moins de 4,5 », le modèle passe de 64,7 % à 71,1 %, contre 66,0 % pour les bookmakers.

## Résultats détaillés

« Zone ≥ 60 % » : les matchs où le modèle donnait au moins 60 %, c'est-à-dire ce qu'aurait donné une Confiance élevée (prédit → observé).

### Corners

Dispersion par championnat (équipe / total) : FR1 10.04 / 68.82, EN1 10.26 / 75.66, ES1 10.56 / 76.81, DE1 11.04 / 72.95, IT1 9.42 / 41.45, BR1 9.56 / 66.61

| Pari | Observé | Prédit | Écart Brier (IC 95 %) | Zone ≥ 60 % : n, prédit → observé | Validé |
|---|---|---|---|---|---|
| total:over@7.5 | 72.5% | 74.0% | -0.0006 (-0.0015 ; +0.0003) | 2609, 74% → 73% | non |
| total:over@8.5 | 61.3% | 62.7% | -0.0014 (-0.0027 ; -0.0001) | 1745, 66% → 64% | oui |
| total:over@9.5 | 50.0% | 50.7% | -0.0013 (-0.0027 ; +0.0001) | 81, 61% → 49% | non |
| total:over@10.5 | 38.1% | 39.1% | -0.0016 (-0.0030 ; -0.0002) | 0 | oui |
| total:over@11.5 | 28.0% | 28.8% | -0.0007 (-0.0019 ; +0.0004) | 0 | non |
| total:over@12.5 | 20.0% | 20.2% | -0.0013 (-0.0021 ; -0.0004) | 0 | oui |
| total:under@7.5 | 27.5% | 26.0% | -0.0006 (-0.0015 ; +0.0003) | 0 | non |
| total:under@8.5 | 38.8% | 37.3% | -0.0014 (-0.0027 ; -0.0001) | 0 | oui |
| total:under@9.5 | 50.0% | 49.3% | -0.0013 (-0.0027 ; +0.0001) | 83, 62% → 59% | non |
| total:under@10.5 | 61.9% | 60.9% | -0.0016 (-0.0030 ; -0.0002) | 1397, 65% → 65% | oui |
| total:under@11.5 | 72.0% | 71.2% | -0.0007 (-0.0019 ; +0.0004) | 2604, 71% → 72% | non |
| total:under@12.5 | 80.0% | 79.8% | -0.0013 (-0.0021 ; -0.0004) | 2614, 80% → 80% | oui |
| home:over@2.5 | 84.4% | 84.0% | -0.0020 (-0.0036 ; -0.0005) | 2614, 84% → 84% | oui |
| home:under@2.5 | 15.6% | 16.0% | -0.0020 (-0.0036 ; -0.0005) | 0 | oui |
| home:over@3.5 | 71.5% | 71.2% | -0.0061 (-0.0087 ; -0.0034) | 2371, 73% → 73% | oui |
| home:under@3.5 | 28.5% | 28.8% | -0.0061 (-0.0087 ; -0.0034) | 0 | oui |
| home:over@4.5 | 57.4% | 56.9% | -0.0067 (-0.0099 ; -0.0034) | 1039, 66% → 65% | oui |
| home:under@4.5 | 42.6% | 43.1% | -0.0067 (-0.0099 ; -0.0034) | 121, 64% → 62% | oui |
| home:over@5.5 | 43.9% | 43.0% | -0.0081 (-0.0115 ; -0.0045) | 93, 65% → 68% | oui |
| home:under@5.5 | 56.1% | 57.0% | -0.0081 (-0.0115 ; -0.0045) | 963, 67% → 66% | oui |
| home:over@6.5 | 31.9% | 31.0% | -0.0080 (-0.0111 ; -0.0049) | 9, 64% → 33% | oui |
| home:under@6.5 | 68.1% | 69.0% | -0.0080 (-0.0111 ; -0.0049) | 2225, 72% → 71% | oui |
| away:over@2.5 | 73.0% | 74.5% | -0.0031 (-0.0050 ; -0.0011) | 2572, 75% → 73% | oui |
| away:under@2.5 | 27.0% | 25.5% | -0.0031 (-0.0050 ; -0.0011) | 0 | oui |
| away:over@3.5 | 56.2% | 58.0% | -0.0047 (-0.0075 ; -0.0019) | 1055, 66% → 64% | oui |
| away:under@3.5 | 43.8% | 42.0% | -0.0047 (-0.0075 ; -0.0019) | 35, 62% → 60% | oui |
| away:over@4.5 | 41.0% | 41.9% | -0.0058 (-0.0088 ; -0.0028) | 58, 64% → 55% | oui |
| away:under@4.5 | 59.0% | 58.1% | -0.0058 (-0.0088 ; -0.0028) | 1112, 66% → 67% | oui |
| away:over@5.5 | 27.6% | 28.4% | -0.0057 (-0.0082 ; -0.0033) | 1, 65% → 100% | oui |
| away:under@5.5 | 72.4% | 71.6% | -0.0057 (-0.0082 ; -0.0033) | 2432, 73% → 74% | oui |
| away:over@6.5 | 18.1% | 18.2% | -0.0034 (-0.0052 ; -0.0015) | 0 | oui |
| away:under@6.5 | 81.9% | 81.8% | -0.0034 (-0.0052 ; -0.0015) | 2601, 82% → 82% | oui |
| 1x2:home | 55.7% | 55.0% | -0.0113 (-0.0151 ; -0.0075) | 850, 66% → 69% | oui |
| 1x2:draw | 9.8% | 10.4% | -0.0004 (-0.0008 ; +0.0000) | 0 | non |
| 1x2:away | 34.5% | 34.5% | -0.0103 (-0.0138 ; -0.0069) | 36, 65% → 58% | oui |

### Cartons (jaunes + rouges)

Dispersion par championnat (équipe / total) : FR1 500 / 97.2, EN1 500 / 25.71, ES1 22.01 / 13.35, DE1 500 / 21.21, IT1 500 / 70.31, BR1 500 / 25.8

| Pari | Observé | Prédit | Écart Brier (IC 95 %) | Zone ≥ 60 % : n, prédit → observé | Validé |
|---|---|---|---|---|---|
| total:over@2.5 | 78.0% | 79.0% | -0.0025 (-0.0040 ; -0.0010) | 2613, 79% → 78% | oui |
| total:over@3.5 | 61.4% | 62.3% | -0.0056 (-0.0081 ; -0.0032) | 1349, 70% → 69% | oui |
| total:over@4.5 | 43.3% | 45.0% | -0.0062 (-0.0086 ; -0.0039) | 308, 63% → 61% | oui |
| total:over@5.5 | 28.3% | 29.9% | -0.0034 (-0.0052 ; -0.0016) | 0 | oui |
| total:over@6.5 | 16.1% | 18.4% | -0.0023 (-0.0035 ; -0.0011) | 0 | oui |
| total:under@2.5 | 22.0% | 21.0% | -0.0025 (-0.0040 ; -0.0010) | 0 | oui |
| total:under@3.5 | 38.6% | 37.7% | -0.0056 (-0.0081 ; -0.0032) | 2, 61% → 50% | oui |
| total:under@4.5 | 56.7% | 55.0% | -0.0062 (-0.0086 ; -0.0039) | 1081, 65% → 68% | oui |
| total:under@5.5 | 71.7% | 70.1% | -0.0034 (-0.0052 ; -0.0016) | 2023, 74% → 76% | oui |
| total:under@6.5 | 83.9% | 81.6% | -0.0023 (-0.0035 ; -0.0011) | 2611, 82% → 84% | oui |
| home:over@0.5 | 87.2% | 86.7% | -0.0018 (-0.0028 ; -0.0009) | 2614, 87% → 87% | oui |
| home:under@0.5 | 12.8% | 13.3% | -0.0018 (-0.0028 ; -0.0009) | 0 | oui |
| home:over@1.5 | 59.8% | 60.7% | -0.0040 (-0.0062 ; -0.0018) | 1255, 68% → 67% | oui |
| home:under@1.5 | 40.2% | 39.3% | -0.0040 (-0.0062 ; -0.0018) | 6, 63% → 83% | oui |
| home:over@2.5 | 31.9% | 34.5% | -0.0042 (-0.0061 ; -0.0021) | 5, 61% → 60% | oui |
| home:under@2.5 | 68.1% | 65.5% | -0.0042 (-0.0061 ; -0.0021) | 1843, 70% → 73% | oui |
| home:over@3.5 | 14.6% | 16.4% | -0.0012 (-0.0024 ; -0.0000) | 0 | oui |
| home:under@3.5 | 85.4% | 83.5% | -0.0012 (-0.0024 ; -0.0000) | 2612, 84% → 85% | oui |
| away:over@0.5 | 90.9% | 90.1% | -0.0009 (-0.0016 ; -0.0002) | 2614, 90% → 91% | oui |
| away:under@0.5 | 9.1% | 9.9% | -0.0009 (-0.0016 ; -0.0002) | 0 | oui |
| away:over@1.5 | 67.4% | 67.8% | -0.0038 (-0.0059 ; -0.0016) | 2231, 70% → 69% | oui |
| away:under@1.5 | 32.6% | 32.2% | -0.0038 (-0.0059 ; -0.0016) | 0 | oui |
| away:over@2.5 | 41.0% | 42.2% | -0.0037 (-0.0057 ; -0.0016) | 27, 62% → 56% | oui |
| away:under@2.5 | 59.0% | 57.8% | -0.0037 (-0.0057 ; -0.0016) | 1185, 66% → 67% | oui |
| away:over@3.5 | 19.5% | 22.1% | -0.0016 (-0.0029 ; -0.0003) | 0 | oui |
| away:under@3.5 | 80.5% | 77.9% | -0.0016 (-0.0029 ; -0.0003) | 2607, 78% → 81% | oui |
| 1x2:home | 31.5% | 35.0% | -0.0006 (-0.0025 ; +0.0013) | 0 | non |
| 1x2:draw | 23.5% | 19.3% | +0.0013 (+0.0003 ; +0.0023) | 0 | non |
| 1x2:away | 45.0% | 45.8% | -0.0021 (-0.0039 ; -0.0001) | 10, 61% → 50% | oui |

### Cartons jaunes

Dispersion par championnat (équipe / total) : FR1 500 / 500, EN1 500 / 46.37, ES1 205.27 / 19.12, DE1 500 / 35.5, IT1 500 / 500, BR1 500 / 52.61

| Pari | Observé | Prédit | Écart Brier (IC 95 %) | Zone ≥ 60 % : n, prédit → observé | Validé |
|---|---|---|---|---|---|
| total:over@2.5 | 76.6% | 77.5% | -0.0027 (-0.0044 ; -0.0010) | 2611, 77% → 77% | oui |
| total:over@3.5 | 59.1% | 59.6% | -0.0059 (-0.0085 ; -0.0034) | 1131, 69% → 67% | oui |
| total:over@4.5 | 40.0% | 41.5% | -0.0067 (-0.0091 ; -0.0043) | 106, 63% → 65% | oui |
| total:over@5.5 | 24.4% | 26.3% | -0.0032 (-0.0048 ; -0.0014) | 0 | oui |
| total:over@6.5 | 13.1% | 15.3% | -0.0015 (-0.0025 ; -0.0005) | 0 | oui |
| total:under@2.5 | 23.4% | 22.5% | -0.0027 (-0.0044 ; -0.0010) | 0 | oui |
| total:under@3.5 | 40.9% | 40.4% | -0.0059 (-0.0085 ; -0.0034) | 16, 61% → 69% | oui |
| total:under@4.5 | 60.0% | 58.5% | -0.0067 (-0.0091 ; -0.0043) | 1373, 67% → 69% | oui |
| total:under@5.5 | 75.6% | 73.7% | -0.0032 (-0.0048 ; -0.0014) | 2337, 76% → 78% | oui |
| total:under@6.5 | 86.9% | 84.7% | -0.0015 (-0.0025 ; -0.0005) | 2614, 85% → 87% | oui |
| home:over@0.5 | 86.7% | 85.5% | -0.0014 (-0.0026 ; -0.0004) | 2614, 86% → 87% | oui |
| home:under@0.5 | 13.3% | 14.5% | -0.0014 (-0.0026 ; -0.0004) | 0 | oui |
| home:over@1.5 | 58.1% | 58.2% | -0.0040 (-0.0063 ; -0.0017) | 1043, 67% → 66% | oui |
| home:under@1.5 | 41.9% | 41.8% | -0.0040 (-0.0063 ; -0.0017) | 13, 62% → 69% | oui |
| home:over@2.5 | 29.6% | 31.9% | -0.0040 (-0.0058 ; -0.0020) | 0 | oui |
| home:under@2.5 | 70.4% | 68.1% | -0.0040 (-0.0058 ; -0.0020) | 2059, 72% → 73% | oui |
| home:over@3.5 | 12.7% | 14.5% | -0.0011 (-0.0021 ; -0.0001) | 0 | oui |
| home:under@3.5 | 87.3% | 85.5% | -0.0011 (-0.0021 ; -0.0001) | 2614, 86% → 87% | oui |
| away:over@0.5 | 90.2% | 89.1% | -0.0010 (-0.0018 ; -0.0002) | 2614, 89% → 90% | oui |
| away:under@0.5 | 9.8% | 10.9% | -0.0010 (-0.0018 ; -0.0002) | 0 | oui |
| away:over@1.5 | 65.9% | 65.5% | -0.0034 (-0.0058 ; -0.0011) | 1967, 68% → 69% | oui |
| away:under@1.5 | 34.1% | 34.5% | -0.0034 (-0.0058 ; -0.0011) | 0 | oui |
| away:over@2.5 | 38.3% | 39.4% | -0.0044 (-0.0063 ; -0.0024) | 6, 61% → 33% | oui |
| away:under@2.5 | 61.7% | 60.6% | -0.0044 (-0.0063 ; -0.0024) | 1466, 67% → 68% | oui |
| away:over@3.5 | 16.6% | 19.8% | -0.0011 (-0.0022 ; +0.0001) | 0 | non |
| away:under@3.5 | 83.4% | 80.2% | -0.0011 (-0.0022 ; +0.0001) | 2612, 80% → 83% | non |
| 1x2:home | 31.9% | 34.7% | -0.0009 (-0.0028 ; +0.0010) | 0 | non |
| 1x2:draw | 23.5% | 19.9% | +0.0007 (-0.0002 ; +0.0016) | 0 | non |
| 1x2:away | 44.6% | 45.4% | -0.0028 (-0.0047 ; -0.0009) | 6, 61% → 50% | oui |

### Tirs

Dispersion par championnat (équipe / total) : FR1 17.86 / 88.2, EN1 17.55 / 92.46, ES1 18.42 / 96.24, DE1 19.89 / 84.09, IT1 18.15 / 75.57, BR1 17.67 / 103.21

| Pari | Observé | Prédit | Écart Brier (IC 95 %) | Zone ≥ 60 % : n, prédit → observé | Validé |
|---|---|---|---|---|---|
| total:over@20.5 | 81.4% | 79.6% | -0.0011 (-0.0030 ; +0.0008) | 2580, 80% → 82% | non |
| total:over@22.5 | 71.0% | 67.7% | -0.0015 (-0.0042 ; +0.0011) | 2100, 71% → 73% | non |
| total:over@24.5 | 56.1% | 54.2% | -0.0064 (-0.0096 ; -0.0031) | 811, 65% → 66% | oui |
| total:over@26.5 | 42.7% | 40.7% | -0.0061 (-0.0092 ; -0.0029) | 61, 63% → 66% | oui |
| total:over@28.5 | 30.4% | 28.5% | -0.0044 (-0.0071 ; -0.0017) | 0 | oui |
| total:under@20.5 | 18.6% | 20.4% | -0.0011 (-0.0030 ; +0.0008) | 0 | non |
| total:under@22.5 | 29.0% | 32.3% | -0.0015 (-0.0042 ; +0.0011) | 10, 63% → 70% | non |
| total:under@24.5 | 43.9% | 45.8% | -0.0064 (-0.0096 ; -0.0031) | 215, 65% → 60% | oui |
| total:under@26.5 | 57.3% | 59.3% | -0.0061 (-0.0092 ; -0.0029) | 1226, 68% → 66% | oui |
| total:under@28.5 | 69.5% | 71.5% | -0.0044 (-0.0071 ; -0.0017) | 2376, 73% → 71% | oui |
| home:over@8.5 | 87.0% | 85.0% | -0.0065 (-0.0093 ; -0.0037) | 2562, 86% → 88% | oui |
| home:under@8.5 | 13.0% | 15.0% | -0.0065 (-0.0093 ; -0.0037) | 0 | oui |
| home:over@10.5 | 75.3% | 72.0% | -0.0155 (-0.0199 ; -0.0113) | 2115, 77% → 81% | oui |
| home:under@10.5 | 24.7% | 28.0% | -0.0155 (-0.0199 ; -0.0113) | 58, 65% → 66% | oui |
| home:over@12.5 | 60.9% | 56.8% | -0.0226 (-0.0279 ; -0.0176) | 1218, 70% → 74% | oui |
| home:under@12.5 | 39.1% | 43.2% | -0.0226 (-0.0279 ; -0.0176) | 435, 69% → 67% | oui |
| home:over@14.5 | 45.1% | 41.8% | -0.0223 (-0.0276 ; -0.0171) | 323, 67% → 72% | oui |
| home:under@14.5 | 54.9% | 58.2% | -0.0223 (-0.0276 ; -0.0171) | 1158, 73% → 69% | oui |
| away:over@8.5 | 69.8% | 70.5% | -0.0090 (-0.0131 ; -0.0050) | 2107, 75% → 73% | oui |
| away:under@8.5 | 30.2% | 29.4% | -0.0090 (-0.0131 ; -0.0050) | 39, 66% → 54% | oui |
| away:over@10.5 | 52.8% | 52.5% | -0.0158 (-0.0210 ; -0.0105) | 786, 69% → 69% | oui |
| away:under@10.5 | 47.2% | 47.5% | -0.0158 (-0.0210 ; -0.0105) | 547, 68% → 64% | oui |
| away:over@12.5 | 37.1% | 35.5% | -0.0164 (-0.0213 ; -0.0115) | 161, 67% → 62% | oui |
| away:under@12.5 | 62.9% | 64.5% | -0.0164 (-0.0213 ; -0.0115) | 1716, 73% → 71% | oui |
| away:over@14.5 | 23.7% | 22.1% | -0.0125 (-0.0166 ; -0.0084) | 24, 67% → 67% | oui |
| away:under@14.5 | 76.3% | 77.9% | -0.0125 (-0.0166 ; -0.0084) | 2393, 80% → 79% | oui |
| 1x2:home | 63.2% | 61.6% | -0.0269 (-0.0328 ; -0.0213) | 1522, 74% → 74% | oui |
| 1x2:draw | 3.9% | 5.3% | +0.0001 (-0.0001 ; +0.0003) | 0 | non |
| 1x2:away | 33.0% | 33.1% | -0.0253 (-0.0310 ; -0.0198) | 221, 70% → 67% | oui |

### Tirs cadrés

Dispersion par championnat (équipe / total) : FR1 23.19 / 70.75, EN1 27.16 / 199.8, ES1 28.06 / 71.93, DE1 26.86 / 125.45, IT1 31.36 / 108.64, BR1 31.53 / 406.79

| Pari | Observé | Prédit | Écart Brier (IC 95 %) | Zone ≥ 60 % : n, prédit → observé | Validé |
|---|---|---|---|---|---|
| total:over@6.5 | 75.8% | 75.5% | -0.0029 (-0.0046 ; -0.0013) | 2591, 76% → 76% | oui |
| total:over@7.5 | 64.3% | 63.4% | -0.0027 (-0.0049 ; -0.0005) | 1840, 67% → 66% | oui |
| total:over@8.5 | 51.9% | 50.5% | -0.0023 (-0.0047 ; +0.0002) | 256, 64% → 64% | non |
| total:over@9.5 | 38.7% | 38.0% | -0.0020 (-0.0045 ; +0.0003) | 18, 63% → 72% | non |
| total:over@10.5 | 27.4% | 27.1% | -0.0022 (-0.0042 ; -0.0001) | 0 | non |
| total:under@6.5 | 24.2% | 24.5% | -0.0029 (-0.0046 ; -0.0013) | 0 | oui |
| total:under@7.5 | 35.7% | 36.6% | -0.0027 (-0.0049 ; -0.0005) | 1, 61% → 0% | oui |
| total:under@8.5 | 48.1% | 49.5% | -0.0023 (-0.0047 ; +0.0002) | 257, 63% → 58% | non |
| total:under@9.5 | 61.3% | 62.0% | -0.0020 (-0.0045 ; +0.0003) | 1595, 67% → 65% | non |
| total:under@10.5 | 72.6% | 72.9% | -0.0022 (-0.0042 ; -0.0001) | 2519, 74% → 73% | non |
| home:over@2.5 | 82.8% | 81.7% | -0.0074 (-0.0100 ; -0.0050) | 2569, 82% → 83% | oui |
| home:under@2.5 | 17.2% | 18.3% | -0.0074 (-0.0100 ; -0.0050) | 0 | oui |
| home:over@3.5 | 68.7% | 66.5% | -0.0136 (-0.0177 ; -0.0098) | 1860, 72% → 75% | oui |
| home:under@3.5 | 31.3% | 33.6% | -0.0136 (-0.0177 ; -0.0098) | 56, 65% → 66% | oui |
| home:over@4.5 | 51.5% | 50.0% | -0.0178 (-0.0224 ; -0.0131) | 626, 68% → 71% | oui |
| home:under@4.5 | 48.5% | 50.0% | -0.0178 (-0.0224 ; -0.0131) | 631, 68% → 65% | oui |
| home:over@5.5 | 37.9% | 34.9% | -0.0185 (-0.0232 ; -0.0140) | 109, 68% → 75% | oui |
| home:under@5.5 | 62.1% | 65.1% | -0.0185 (-0.0232 ; -0.0140) | 1759, 73% → 70% | oui |
| away:over@2.5 | 71.1% | 71.0% | -0.0058 (-0.0090 ; -0.0025) | 2291, 73% → 73% | oui |
| away:under@2.5 | 28.9% | 29.0% | -0.0058 (-0.0090 ; -0.0025) | 5, 63% → 80% | oui |
| away:over@3.5 | 52.1% | 52.2% | -0.0109 (-0.0151 ; -0.0069) | 583, 68% → 66% | oui |
| away:under@3.5 | 47.9% | 47.8% | -0.0109 (-0.0151 ; -0.0069) | 367, 66% → 62% | oui |
| away:over@4.5 | 34.5% | 34.8% | -0.0097 (-0.0138 ; -0.0056) | 91, 68% → 65% | oui |
| away:under@4.5 | 65.5% | 65.2% | -0.0097 (-0.0138 ; -0.0056) | 1895, 71% → 70% | oui |
| away:over@5.5 | 20.7% | 21.2% | -0.0071 (-0.0103 ; -0.0039) | 12, 67% → 67% | oui |
| away:under@5.5 | 79.3% | 78.8% | -0.0071 (-0.0103 ; -0.0039) | 2475, 80% → 81% | oui |
| 1x2:home | 55.5% | 54.4% | -0.0249 (-0.0297 ; -0.0200) | 968, 69% → 70% | oui |
| 1x2:draw | 11.7% | 11.8% | -0.0006 (-0.0011 ; -0.0001) | 0 | oui |
| 1x2:away | 32.8% | 33.8% | -0.0222 (-0.0268 ; -0.0176) | 130, 68% → 71% | oui |
