"""Replay of the market-only rule on 5 seasons (agent mathématicien, 2026-10-28; docs/22-agent-mathematicien.md).

One position per match: among 1X2, over/under 2.5 and half-line Asian handicap, the selection with the highest
de-vigged market chance (proportional, football-data closing Avg odds), odds 1.50-2.30, chance >= threshold.
Nothing is fitted: the thresholds are set in advance, so the seasons are all out of sample for the rule.
Usage: download https://www.football-data.co.uk/mmz4281/<2223..2627>/<E0,F1,SP1,D1,I1,T1>.csv as <code>_<season>.csv
into /tmp/claude-0/fd/, then python scripts/replay_marche.py
"""
import csv,glob,collections,math,sys
from datetime import datetime
def f(r,k):
    try: v=float(r.get(k) or ''); return v if v>1 else None
    except ValueError: return None
def cands(r):
    out=[]
    h,d,a=f(r,'AvgCH') or f(r,'AvgH'),f(r,'AvgCD') or f(r,'AvgD'),f(r,'AvgCA') or f(r,'AvgA')
    hg,ag=int(r['FTHG']),int(r['FTAG'])
    if h and d and a:
        s=1/h+1/d+1/a
        out+= [('1X2 dom',h,(1/h)/s,hg>ag),('1X2 nul',d,(1/d)/s,hg==ag),('1X2 ext',a,(1/a)/s,ag>hg)]
    o,u=f(r,'AvgC>2.5') or f(r,'Avg>2.5'),f(r,'AvgC<2.5') or f(r,'Avg<2.5')
    if o and u:
        s=1/o+1/u; out+=[('plus 2,5',o,(1/o)/s,hg+ag>2),('moins 2,5',u,(1/u)/s,hg+ag<3)]
    try: l=float(r.get('AHCh') or r.get('AHh') or '')
    except ValueError: l=None
    ah,aa=f(r,'AvgCAHH') or f(r,'AvgAHH'),f(r,'AvgCAHA') or f(r,'AvgAHA')
    if l is not None and ah and aa and abs(l*2-round(l*2))<1e-9 and round(l*2)%2==1:
        s=1/ah+1/aa; m=hg-ag+l; out+=[('handicap dom',ah,(1/ah)/s,m>0),('handicap ext',aa,(1/aa)/s,m<0)]
    return out
rows=[]
for p in glob.glob('/tmp/claude-0/fd/*.csv'):
    lg,season=p.split('/')[-1][:-4].split('_')
    for r in csv.DictReader(open(p,encoding='utf-8-sig',errors='ignore')):
        if not r.get('FTHG'): continue
        try: dt=datetime.strptime(r['Date'],'%d/%m/%Y')
        except ValueError: dt=datetime.strptime(r['Date'],'%d/%m/%y')
        rows.append((season,lg,dt,cands(r)))
def rule(th,lo=1.5,hi=2.3,fam=None):
    picks=[]
    for season,lg,dt,c in rows:
        c=[x for x in c if lo<=x[1]<=hi and x[2]>=th and (fam is None or x[0].split()[0] in fam)]
        if c: b=max(c,key=lambda x:x[2]); picks.append((season,dt,)+b)
    return picks
def show(picks,lab):
    print('\n'+lab)
    by=collections.defaultdict(list)
    for p in picks: by[p[0]].append(p)
    for s in sorted(by)+['TOUT']:
        v=picks if s=='TOUT' else by[s]; n=len(v); w=sum(x[5] for x in v)
        wk=collections.defaultdict(list)
        for x in v: wk[x[1].isocalendar()[:2]].append(x[5])
        wk=[k for k in wk.values() if len(k)>=10]
        bad=sum(sum(k)/len(k)<.5 for k in wk)
        print(f' {s:5s} n={n:5d} réussite={w/n:.1%} annoncé={sum(x[4] for x in v)/n:.1%} cote={sum(x[3] for x in v)/n:.2f} roi={sum((x[3]-1) if x[5] else -1 for x in v)/n:+.1%} semaines<50%={bad}/{len(wk)}')
show(rule(0.60),'Marché >= 60 %, cote 1,50-2,30, 1 par match (1X2, plus/moins 2,5, handicap)')
show(rule(0.55),'Marché >= 55 %')
show(rule(0.50),'Marché >= 50 % (le plus probable de chaque match)')
