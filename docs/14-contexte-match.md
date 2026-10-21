# Contexte du match (ajout du 21/10/2026, demande de Vincent après la comparaison avec SofaScore)

Chaque passage avant-match (matin, H−3, H−45, H−15) écrit dans le document du match un champ `contexte`
(`sportai/services/contexte.py`, appelé par `_context` dans `scripts/agent_pass.py`) :

| Clé | Contenu | Source | Coût |
|---|---|---|---|
| `arbitre` | nom, matchs connus, `profil` (sévère / dans la moyenne / clément / inconnu), cartons et penalties par match contre la moyenne de ses championnats | matchs API-Football déjà en cache, index refait chaque jour par `goals-refresh` dans `state/arbitres.json` | 0 requête |
| `classement.domicile` / `.exterieur` | rang, points, matchs joués et restants, zone, écart avec la place au-dessus (`au_dessus`) et en dessous (`en_dessous`), `enjeu` (« fort », « normal », « sans enjeu ») seulement en fin de saison | API-Football `/standings` | 1 requête par championnat et passage |
| `series.domicile` / `.exterieur` | sur les 10 derniers matchs toutes compétitions : victoires, nuls, défaites, +2,5 buts, les deux marquent, clean sheets, sans marquer, `serie` (V/N/D, du plus ancien au plus récent) | API-Football `/fixtures?team&last=10` | 2 requêtes par match et par jour |
| `meteo` | ciel, température, pluie (mm/h), vent et rafales (km/h) à l'heure du coup d'envoi, `effet` en clair | Open-Meteo (géocodage du stade gardé dans `state/stades.json`) | gratuit |
| `phrases` | une phrase simple par élément, à afficher telle quelle | | |
| `note` | « Contexte affiché pour l'analyse, pas encore pris en compte dans les probabilités. » | | |

Rien de ce contexte ne change une probabilité ni une sélection tant qu'un test ne l'a pas validé.

## Test de l'arbitre (sur l'historique, sans regarder le futur)
10 953 matchs de championnat en cache, chaque arbitre jugé uniquement sur ses matchs précédents
(au moins 8, moyenne tirée vers celle du championnat) ; points cartons = jaune 1, rouge 2 :

| Arbitre jugé avant le match | Matchs | Points cartons réels | Moyenne du championnat | Plus de 4,5 points cartons |
|---|---|---|---|---|
| sévère | 873 | 5,09 | 4,60 | 54 % |
| dans la moyenne | 8 904 | 4,64 | 4,57 | 47 % |
| clément | 1 176 | 3,98 | 4,63 | 36 % |

L'effet est réel et assez net : c'est la base d'un futur marché « cartons » (pas encore proposé).
Les arbitres de Coupe d'Europe qui n'arbitrent pas dans un championnat suivi sortent « inconnu ».

## Météo
Open-Meteo est autorisé dans le réseau de l'environnement depuis le 21/10/2026 (le premier appel de géocodage est
parfois lent, d'où un délai de 20 s). S'il ne répond pas, `meteo` garde la dernière prévision et le passage continue.
Licence commerciale Open-Meteo à prendre avant la mise en vente.
