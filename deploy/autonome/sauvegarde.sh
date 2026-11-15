#!/bin/bash
# Toutes les heures : 1) copie datée de la base (gardée 30 jours) ; 2) copie lisible par Claude dans le dépôt privé
# GitHub, branche « donnees » (Claude y lit ratios, positions, journal et voyants, sans toucher au serveur).
set -uo pipefail
A=/srv/analytrack; J=$(date +%F-%H)
tar -czf $A/sauvegardes/base-$J.tgz -C $A base fichiers/state/odds_credits.txt 2>/dev/null
find $A/sauvegardes -name 'base-*.tgz' -mtime +30 -delete
D=$A/donnees-git
if [ ! -d $D/.git ]; then git clone -q --depth 1 -b donnees git@github.com:Vincentadmin77/agent-sportif.git $D 2>/dev/null \
  || { git init -q $D && git -C $D checkout -q -b donnees && git -C $D remote add origin git@github.com:Vincentadmin77/agent-sportif.git; }; fi
rsync -a --delete --exclude '.git' --exclude '_verrou' $A/base/ $D/base/
journalctl -u analytrack --since "-1 day" --no-pager -o short-iso 2>/dev/null | tail -n 3000 > $D/journal-24h.log
cd $D && git add -A && git -c user.name=analytrack -c user.email=serveur@analytrack.local commit -q -m "données $J" && git push -q origin donnees
exit 0
