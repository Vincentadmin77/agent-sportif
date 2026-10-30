# Agent mathématicien : registre des positions et rapport interne

Demandé par Vincent le 28/10/2026. Le mathématicien ne propose aucune position : il contrôle celles des autres
agents, tient des comptes honnêtes et teste les idées avant la production. État des lieux et plan :
https://claude.ai/artifact/5HMHHwjG8xXcP2i5S75GfN

## Registre (étape 1, pas encore branché sur les passages)
`sportai/services/registre.py`, `scripts/mathematicien.py`. Une fiche par position, écrite une seule fois dès que la
position est figée (H−45) : heure de décision, cote prise, bookmaker, chances modèle / marché / retenue, version des
règles (empreinte des constantes `PICK_*` de pronostic.py), version du code. Ensuite seulement : cote de clôture (les
cotes du document du match si un passage d'avant-match a eu lieu après la décision, sinon `null`), CLV, résultat,
gain à mise égale (interne). Un changement ultérieur de la position est noté dans `modifications`, jamais appliqué.
Une fiche vue pour la première fois après le coup d'envoi porte `enregistre_apres_coup_envoi`. L'historique recalculé
est importé à part (`recalcule: true`, sans heure de décision) et mesuré séparément.

```bash
python scripts/mathematicien.py registre --page PAGE --out OUT   # après chaque passage H−45 et au final  # lit aussi PAGE/tennis/ (le M au tennis, 30/10)
python scripts/mathematicien.py importer --page PAGE --out OUT   # une fois : jours recalculés
python scripts/mathematicien.py rapport  --page PAGE             # rapport du lundi
```
PAGE = collections `matchs`, `pronostic`, `registre` sauvées par ArtifactData `list`. OUT/registre/<jour>.json va dans
la collection `registre` (doc_id = le jour). Aucun appel d'API. Le rendement et les creux restent internes : jamais
d'argent sur le site.

## Limites connues
- La cote stockée est la meilleure du marché mondial, pas celle d'un bookmaker français : rendement flatteur.
- La vraie cote de clôture demande un passage d'avant-match après H−45 (sinon la CLV n'est pas calculée).
- Le basket n'a pas encore de `paris_retenus` figés comparables : à brancher quand son format sera stable.

## Règle « chance du marché ≥ 60 % » rejouée sur 5 saisons (28/10, hypothèse, pas en production)
`scripts/replay_marche.py`. Une position par match, la plus probable selon le marché sans marge (cotes de clôture
moyennes football-data, 1X2, plus/moins 2,5, handicap asiatique demi-ligne), cote 1,50-2,30. Rien n'est ajusté sur les
données : les seuils sont fixés d'avance. 6 championnats (sans Brésil ni coupes), 2022-23 à 2026-27 (au 26/10).

| Seuil | Positions | Réussite | Annoncé | Cote moy. | Rendement mise égale | Semaines < 50 % |
| --- | --- | --- | --- | --- | --- | --- |
| ≥ 60 % | 1 541 | 64,2 % | 61,7 % | 1,54 | −1,1 % | 8 / 80 |
| ≥ 55 % | 4 609 | 60,8 % | 58,8 % | 1,62 | −1,8 % | 16 / 156 |
| ≥ 50 % | 7 955 | 57,7 % | 56,2 % | 1,70 | −2,5 % | 23 / 157 |

Par saison (≥ 60 %) : 64,5 / 68,5 / 58,4 / 63,5 / 74,4 % (78 positions). Réussite régulière et un peu au-dessus de
l'annoncé, mais pas d'avantage financier prouvé sur le prix moyen.

## Cote valable et direct à blanc (Vincent 28/10 13:53, interne seulement : choix « Interne pour l'instant » 13:56)
Avant le match, chaque position très probable (mélange modèle + marché ≥ 70 %, buteur ≥ 40 %) mais sous la cote 1,50
reçoit sa **cote valable** = 1,03 / chance (`registre.targets`). En direct, à chaque cycle (`live_page.live_doc` →
`registre.capture_live`), une ligne de `direct.cotes_direct` d'une de ces cibles est notée une seule fois dans
`live_a_blanc` du document du match si : statut « opportunite » (contrôles du sens du match passés), cote 1,50-2,30 et
≥ cote valable d'avant-match, chance du direct × cote ≥ 1,03, minute ≤ 80, et pour un buteur une note live ≥ 6,5 (à
défaut au moins un tir ; sans forme connue : laissé). Le registre les reprend (`live_a_blanc: true`, réglées au
final) et le rapport les mesure à part (`direct_a_blanc`). Jamais affiché, jamais compté comme position. Seuils non
testés : on juge après 100 cas.

## Les choix du M à blanc (Vincent 28/10 14:04 : un onglet comme le joueur pro)
À la première exécution du registre après le passage H−45 et avant le coup d'envoi, chaque match reçoit la position
du M (`registre.m_choice`) : marchés résultat et buts, cote 1,50-2,30, la sélection que le marché sans marge voit la
plus probable, à 60 % ou plus ; sinon `pari: null` et la raison. Fiche `<jour>|<match>|M`, `choix_m: true`, jamais
changée ensuite, réglée au final, mesurée à part (`le_m_a_blanc`). Le même passage fige `cibles[<match>]` : les 5
positions les plus probables (≥ 70 %) trop courtes avant le match, avec leur cote valable (au moins 1,50).
Collection `registre/<jour>` : `fiches` (positions officielles, `choix_m`, `live_a_blanc`, recalculées) et `cibles`.

## Avis par match (Vincent 28/10 14:35, page admin)
`registre/<jour>.avis[<match_id>]` (basket : `basket-<id>`), recalculé à chaque exécution du registre jusqu'au coup
d'envoi puis figé : `officielle` (pari, cote, proba = modèle, proba_marche, niveau, resultat), `visa_m` {feu
vert|orange|rouge, raison}, `choix_m` (dès le passage H−45), `enqueteur` (phrases du contexte : absents, arbitre marqué,
rotation, séries, météo), `sans_position` ; après le match `bilan` (qui avait raison). Feu : rouge si marché < 50 %,
modèle ≥ 6 pts au-dessus ou ≥ 10 pts en dessous du marché ; vert si marché ≥ 60 % ; orange sinon. Seuils tirés du
bilan 11-27/10 et de la rétro 5 saisons, non validés comme veto. Avis réels côte à côte, aucun dialogue inventé.
