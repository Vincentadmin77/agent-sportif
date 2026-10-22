#!/bin/bash
# One live cycle for the routines: refresh the local copy of the page from the last output, run
# agent_pass.py live, print the ArtifactData batch to write. Usage: COMPS="FR1 BR1" live_cycle.sh
# Needs /tmp/claude-0/mirror (ArtifactData list of matchs, joueur) and /tmp/claude-0/versions.json
# ({doc_id: version} for matchs, {"joueur/<day>": version} for the player's day).
cd "$(dirname "$0")/.."
M=/tmp/claude-0/mirror; O=/tmp/claude-0/liveout
[ -d $O/matchs ] && cp $O/matchs/*.json $M/matchs/ 2>/dev/null
for sub in joueur; do [ -d $O/$sub ] && mkdir -p $M/$sub && cp $O/$sub/*.json $M/$sub/ 2>/dev/null; done
rm -rf $O
python3 scripts/agent_pass.py live --page $M --out $O ${COMPS:-FR1 ES1 IT1 BR1} 2>&1 | tail -1
# A match just ended: settle tickets, bilan and bankroll right away, nobody needs to ask (owner, 2026-10-11).
if grep -qs '"statut": "termine"' $O/matchs/*.json; then
  cp $O/matchs/*.json $M/matchs/; [ -d $O/joueur ] && cp $O/joueur/*.json $M/joueur/
  # the day of the match that ended, not the clock's: a late Brazilian match ends after midnight in Paris
  DAY=$(basename "$(grep -ls '"statut": "termine"' $O/matchs/*.json | head -1)" | cut -c1-10)
  rm -rf $O.fin && python3 scripts/agent_pass.py pronostic --page $M --out $O.fin --day $DAY 2>&1 | tail -1
  for sub in pronostic stats joueur; do [ -d $O.fin/$sub ] && mkdir -p $O/$sub && cp $O.fin/$sub/*.json $O/$sub/; done
fi
python3 - <<'PY'
import json,glob,os
v=json.load(open('/tmp/claude-0/versions.json'))
w=[]
for p in sorted(glob.glob('/tmp/claude-0/liveout/matchs/*.json')):
    k=os.path.basename(p)[:-5]; d=json.load(open(p))
    print(k[11:], d['statut'], d.get('score'), d.get('minute'), (d.get('direct') or {}).get('periode'))
    w.append({"op":"set","collection":"matchs","doc_id":k,"file_path":p,"if_version":v.get(k)})
days={os.path.basename(p)[:10] for p in glob.glob('/tmp/claude-0/liveout/matchs/*.json')}
for sub in ("joueur", "pronostic", "stats"):  # the player's day followed live; tickets and bilan when a match ended
    for p in sorted(glob.glob(f'/tmp/claude-0/liveout/{sub}/*.json')):
        k=os.path.basename(p)[:-5]
        if sub != "stats" and k[:4].isdigit() and k not in days:
            continue  # an earlier day's finished matches still sit in the page copy: leave that day alone
        w.append({"op":"set","collection":sub,"doc_id":k,"file_path":p,"if_version":v.get(f"{sub}/{k}")})
# a batch takes at most 1 MiB: since every market is listed, match pages weigh ~120 KB, so split it
chunks,size=[[]],0
for x in w:
    n=os.path.getsize(x["file_path"])
    if chunks[-1] and size+n>900_000: chunks.append([]); size=0
    chunks[-1].append(x); size+=n
for c in chunks: print(json.dumps(c))  # one batch per line, write them in order
for x in w:  # the batch printed above is expected to succeed
    key = x['doc_id'] if x['collection'] == 'matchs' else f"{x['collection']}/{x['doc_id']}"
    v[key] = v.get(key, 0) + 1
json.dump(v,open('/tmp/claude-0/versions.json','w'))
PY
