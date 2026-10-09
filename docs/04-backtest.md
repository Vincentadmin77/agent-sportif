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
