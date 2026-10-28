# Buteurs : le modèle « historique du joueur » et la règle de position (28/10/2026)

## Ce qui change
La chance qu'un joueur marque au moins un but ne se calcule plus seulement sur sa saison en cours avec son club.
Elle part de **tout son historique connu** (tous ses clubs, toutes les saisons des championnats en cache), avec
plus de poids pour les matchs récents. L'ancien calcul reste en secours pour un joueur absent de l'historique
(championnats non couverts : Portugal, Pays-Bas, Grèce…).

## Le calcul, en clair
Pour un joueur **titulaire** :
1. **Buts par 90 minutes**, de deux façons :
   - « forme » : ses buts et minutes, un match d'il y a 3 mois compte pour moitié ;
   - « carrière » : la même chose, un match d'il y a 10 ans compte pour moitié.

   Chacun est ramené vers la moyenne de son poste (attaquant 22 %, milieu 8 %, défenseur 3 % des buts de son
   équipe), avec un poids de 16 matchs pleins pour la forme et de 20 pour la carrière. Un joueur qui a peu joué reste
   donc près de la moyenne de son poste. On garde ensuite 25 % de forme et 75 % de carrière.
2. **Adversaire et terrain** : buts encaissés par l'adversaire par match (récents d'abord, ramenés vers la moyenne
   du championnat) divisés par la moyenne du championnat, × 1,15 à domicile ou ÷ 1,15 à l'extérieur.
3. **Temps de jeu** : ses minutes habituelles quand il est titulaire (90 au plus), ramenées vers 80 minutes.
4. Chance = 1 − exp(−0,975 × buts par 90 × adversaire et terrain × minutes / 90).

Les pénaltys comptent, les buts contre son camp non. Le poste vient de la composition officielle quand elle est
publiée, sinon du dernier match du joueur.

## L'étude (walk-forward : chaque match prédit avec les seuls matchs d'avant)
- 15 591 matchs terminés, 479 104 lignes joueur-match (Premier League, Liga, Serie A, Bundesliga, Ligue 1,
  Süper Lig 2020-2026, Brésil 2021-2026). Réglage sur 2022-07 → 2025-06, test sur 2025-07 → 2026-10.
- Test (titulaires) : log-loss **0,2637** contre 0,2722 pour l'ancien calcul (plus bas = mieux), Brier 0,0752
  contre 0,0766.
- Calibration : entre 30 et 60 % annoncés, le nouveau modèle tombe juste (34 % annoncés → 34 % marqués ; 44 % → 43 %).
  L'ancien surestimait les joueurs en série en début de saison (44 % annoncés → 38 %).
- Joueurs annoncés à 45 % ou plus : 229 cas, **51 %** ont marqué (ancien calcul : 43 %).
- Sur les 113 offres de buteur de la page (15-27/10) retrouvées dans les données : log-loss **0,6125** contre
  0,6715 pour l'ancien calcul et 0,6823 pour le marché (1/cote). Les joueurs proposés à ces cotes ont marqué 31 %
  du temps pour 50 % annoncés par les bookmakers.

## Règle pour retenir un buteur comme position du match
Un buteur ne peut être la position d'un match que si **tout** est vrai :
- cote entre 1,50 et 2,30 ;
- le joueur est **titulaire confirmé** sur la composition officielle, lue au passage de H−45 (jamais avant) ;
- chance du modèle ≥ 1/cote + 3 points (valeur) ;
- écart entre le modèle et 1/cote de 10 points au plus.

Il reste « non validé ». Il est classé comme les corners ou les cartons : les marchés résultat et buts gardent la
priorité, un buteur ne passe devant que s'il est 10 points plus sûr ; s'il n'y a aucune autre position sur le
match, il peut être retenu. Jamais en « Top confiance ». Sur les 171 offres de l'étude, cette règle n'en retient
qu'**une** (Kane à 2,22 le 21/10, gagnée) : attendre 0 à 2 cas par semaine, surtout Kane, Mbappé ou Haaland à 2,00
ou plus contre une défense faible.

## Cotes des listes de buteurs : nettoyage
- Un même joueur apparaissait sous deux orthographes (« Jhon Duran » / « Jhon Durán » : 334 doublons du 10 au 28/10).
  Désormais une seule cote par joueur et par match : la meilleure des bookmakers.
- Certaines listes ressemblent à un autre marché sous le même nom (5 joueurs de Benfica entre 1,60 et 2,27 le 15/10,
  6 joueurs du PSG entre 1,83 et 2,20 le 17/10). Une liste d'un bookmaker pour une équipe est écartée quand elle
  met **plus de 3 joueurs sous 2,30** ou **plus d'1 joueur sous 1,50**. Sur la page du 10 au 28/10, ces listes
  (14 sur 316) ont donné 31 % de buteurs pour 52 % annoncés ; les autres 37 % pour 51 %.
- Le flux API-Football vérifié le 28/10 (Serie A, Bet365) range bien les buteurs sous « Anytime Goal Scorer »,
  « Home Anytime Goal Scorer » et « Away Anytime Goal Scorer », avec les mêmes cotes ; « Player to Score or Assist »
  est un marché à part, non lu.

## Fichiers et mise à jour
- Historique : `/mnt/project-files/agent-sportif/state/buteurs_historique.json.gz` (≈ 0,5 Mo, 10 072 joueurs,
  209 équipes), construit par `python3 backend/scripts/buteurs_historique.py` (`--rebuild` pour repartir de zéro).
- Mise à jour : le rafraîchissement quotidien du modèle de buts (`agent_pass.py goals`) télécharge déjà les matchs
  terminés (20 par requête) ; il ajoute ensuite ces matchs à l'historique. Aucune requête en plus, aucun appel à
  The Odds API. Un match n'est jamais compté deux fois.
- Code : `backend/sportai/engine/football/scorer_history.py` (calcul), `scorers.py` (prix des buteurs),
  `services/buteurs.py` (chargement, mise à jour), `services/pronostic.py` (règle de position).

## Limites
- Les coupes et l'Europe ne sont pas dans l'historique : un buteur de Ligue des champions est un peu sous-estimé.
  Contre un adversaire hors historique, le facteur adversaire vient des buts attendus du modèle de match (borné
  entre 0,5 et 2), sinon du terrain seul. C'est le seul écart avec l'étude.
- Le modèle reste prudent tout en haut (au-delà de 50 % annoncés, les joueurs ont marqué 6 à 7 points de plus).
