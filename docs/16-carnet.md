# Le carnet : combien d'abonnés ont pris chaque proposition

Demande de Vincent (21/10/2026) : à notre façon, en interne, afficher sur chaque ticket, idée, position de match
et pari du joueur pro le nombre de personnes qui l'ont mis dans leur carnet.

## Identifiants des propositions (communs à la page et au site)

| Proposition | item_id | kind |
|---|---|---|
| Top idée officielle (safe, semi safe, fun) | `ticket:<jour>:<type>` | ticket |
| Idée de la boîte à idées | `idee:<jour>:<type>:<n>` | idee |
| Position d'un match | `position:<id du document match>` | position |
| Pari du joueur pro | `joueur:<jour>:<n° de décision>` | joueur |

## Site (backend)

Table `carnet_entries` (un abonné ne compte qu'une fois par proposition), routes réservées aux abonnés connectés :
`POST /carnet` {item_id, kind, label}, `DELETE /carnet/{item_id}`, `GET /carnet` (mon carnet, avec le compteur),
`GET /carnet/compteurs?ids=a,b,c` (nombre de personnes par proposition).

## Page actuelle (Artifact)

Bouton « Ajouter à mon carnet » et compteur « N personnes » gérés par la page elle-même (base partagée de la page).
Tant que le service n'est pas en ligne, seul Vincent voit la page : les compteurs resteront à 0 ou 1.
