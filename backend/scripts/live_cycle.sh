#!/bin/bash
# One live cycle for the routines: refresh the local copy of the page from the last output, run
# agent_pass.py live, print the ArtifactData batch to write. Usage: COMPS="FR1 BR1" live_cycle.sh
# Needs /tmp/claude-0/mirror (ArtifactData list of matchs, joueur) and /tmp/claude-0/versions.json
# ({doc_id: version} for matchs, {"joueur/<day>": version} for the player's day).
cd "$(dirname "$0")/.."
M=/tmp/claude-0/mirror; O=/tmp/claude-0/liveout
[ -d $O/matchs ] && cp $O/matchs/*.json $M/matchs/ 2>/dev/null
[ -d $O/joueur ] && mkdir -p $M/joueur && cp $O/joueur/*.json $M/joueur/ 2>/dev/null
rm -rf $O
python3 scripts/agent_pass.py live --page $M --out $O ${COMPS:-FR1 ES1 IT1 BR1} 2>&1 | tail -1
# A match just ended: settle tickets, bilan and bankroll right away, nobody needs to ask (owner, 2026-10-11).
if grep -qs '"statut": "termine"' $O/matchs/*.json; then
  cp $O/matchs/*.json $M/matchs/; [ -d $O/joueur ] && cp $O/joueur/*.json $M/joueur/
  rm -rf $O.fin && python3 scripts/agent_pass.py pronostic --page $M --out $O.fin 2>&1 | tail -1
  for sub in pronostic stats joueur; do [ -d $O.fin/$sub ] && mkdir -p $O/$sub && cp $O.fin/$sub/*.json $O/$sub/; done
fi
python3 - <<'PY'
import json,glob,os
v=json.load(open('/tmp/claude-0/versions.json'))
w=[]
for p in sorted(glob.glob('/tmp/claude-0/liveout/matchs/*.json')):
    k=os.path.basename(p)[:-5]; d=json.load(open(p))
    print(k[11:], d['statut'], d.get('score'), d.get('minute'), d['direct'].get('periode'))
    w.append({"op":"set","collection":"matchs","doc_id":k,"file_path":p,"if_version":v.get(k)})
for sub in ("joueur", "pronostic", "stats"):  # the player's day followed live; tickets and bilan when a match ended
    for p in sorted(glob.glob(f'/tmp/claude-0/liveout/{sub}/*.json')):
        k=os.path.basename(p)[:-5]
        w.append({"op":"set","collection":sub,"doc_id":k,"file_path":p,"if_version":v.get(f"{sub}/{k}")})
print(json.dumps(w))
for x in w:  # the batch printed above is expected to succeed
    key = x['doc_id'] if x['collection'] == 'matchs' else f"{x['collection']}/{x['doc_id']}"
    v[key] = v.get(key, 0) + 1
json.dump(v,open('/tmp/claude-0/versions.json','w'))
PY
