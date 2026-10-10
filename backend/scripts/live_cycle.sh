#!/bin/bash
# One live cycle for the routines: refresh the local copy of the page from the last output, run
# agent_pass.py live, print the ArtifactData batch to write. Usage: COMPS="FR1 BR1" live_cycle.sh
# Needs /tmp/claude-0/mirror (ArtifactData list of matchs) and /tmp/claude-0/versions.json ({doc_id: version}).
cd "$(dirname "$0")/.."
M=/tmp/claude-0/mirror; O=/tmp/claude-0/liveout
[ -d $O/matchs ] && cp $O/matchs/*.json $M/matchs/ 2>/dev/null
rm -rf $O
python3 scripts/agent_pass.py live --page $M --out $O ${COMPS:-FR1 ES1 IT1 BR1} 2>&1 | tail -1
python3 - <<'PY'
import json,glob,os
v=json.load(open('/tmp/claude-0/versions.json'))
w=[]
for p in sorted(glob.glob('/tmp/claude-0/liveout/matchs/*.json')):
    k=os.path.basename(p)[:-5]; d=json.load(open(p))
    print(k[11:], d['statut'], d.get('score'), d.get('minute'), d['direct'].get('periode'))
    w.append({"op":"set","collection":"matchs","doc_id":k,"file_path":p,"if_version":v[k]})
print(json.dumps(w))
PY
