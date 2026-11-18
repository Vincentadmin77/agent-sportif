#!/bin/bash
# Passage hockey horaire (routine du fil « Préparer l'arrivée du hockey NHL »).
# Journée hockey = soirée américaine : avant 12 h (Paris) on traite encore la veille.
set -e
R=/home/user/agent-sportif; B=/mnt/project-files/agent-sportif
cd $R/backend
python3 -c "import sportai" 2>/dev/null || (NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 pip install -q -e ".[dev]")
H=$(TZ=Europe/Paris date +%H)
if [ "$H" -lt 12 ]; then J=$(TZ=Europe/Paris date -d yesterday +%F); else J=$(TZ=Europe/Paris date +%F); fi
mkdir -p $B/hockey/jours /tmp/claude-0/hk
F=$B/hockey/jours/$J.json
if [ -f "$F" ]; then python3 scripts/hockey.py passage --jour $J --doc $F --out $F; else python3 scripts/hockey.py passage --jour $J --out $F; fi
# veille encore ouverte (matchs finis après minuit) : règlement
P=$(TZ=Europe/Paris date -d "$J -1 day" +%F)
[ -f $B/hockey/jours/$P.json ] && python3 scripts/hockey.py regler --doc $B/hockey/jours/$P.json --out $B/hockey/jours/$P.json || true
cp $F /tmp/claude-0/hk/doc-$J.json
[ -f $B/hockey/jours/$P.json ] && cp $B/hockey/jours/$P.json /tmp/claude-0/hk/doc-$P.json
echo "JOUR=$J VEILLE=$P"
