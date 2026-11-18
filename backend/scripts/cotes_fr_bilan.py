import sys,json,glob,collections,statistics as st
rows=[]
for f in sorted(glob.glob(sys.argv[1]+'/2026-*.json')):
    d=json.load(open(f)); rows+= [dict(r,jour=f[-15:-5]) for r in d['positions'].values()]
# cote FR d'une position : Winamax si relevée, sinon moyenne des sites FR, sinon écart moyen du sport
def cfr_of(r):
    if r.get('winamax'): return r['winamax']['cote']
    return r.get('cote_fr_moyenne')
ex={}
for r in rows:
    if cfr_of(r): ex[(r['match'],r['pari'])]=dict(r,cote_fr_moyenne=cfr_of(r),ecart_pct=round(100*(cfr_of(r)/r['cote']-1),1))
gap={s:st.mean(r['ecart_pct'] for r in ex.values() if r['sport']==s)/100 for s in ('foot','basket','tennis')}
nb_winamax={s:sum(1 for r in ex.values() if r['sport']==s and r.get('winamax')) for s in gap}
print({k:round(v*100,1) for k,v in gap.items()})
agg=collections.defaultdict(lambda: collections.defaultdict(list))
for r in rows:
    if r['resultat'] not in ('gagne','perdu'): continue
    k=(r['sport'],r['liste'],r['acteur'])
    e=ex.get((r['match'],r['pari']))
    cfr=e['cote_fr_moyenne'] if e else round(r['cote']*(1+gap[r['sport']]),3)
    agg[k]['res'].append((r['resultat']=='gagne',r['cote'],cfr,bool(e)))
out=[]
for k,v in sorted(agg.items()):
    res=v['res']; n=len(res); w=sum(x[0] for x in res)
    cm=st.mean(x[1] for x in res); cf=st.mean(x[2] for x in res)
    pts=sum((x[1]-1) if x[0] else -1 for x in res); ptf=sum((x[2]-1) if x[0] else -1 for x in res)
    o=dict(sport=k[0],liste=k[1],acteur=k[2],jouees=n,gagnees=w,pct=round(100*w/n,1),cote=round(cm,2),seuil=round(100/cm,1),cote_fr=round(cf,2),seuil_fr=round(100/cf,1),
           marge=round(100*w/n-100/cm,1),marge_fr=round(100*w/n-100/cf,1),points=round(pts,1),points_fr=round(ptf,1),exactes=sum(x[3] for x in res))
    out.append(o); print(o)
json.dump({'periode':min(r['jour'] for r in rows)+' au '+max(r['jour'] for r in rows if r['resultat'] in ('gagne','perdu')),'ecart_moyen_pct':{k:round(v*100,1) for k,v in gap.items()},'nb_exactes':{s:sum(1 for r in ex.values() if r['sport']==s) for s in gap},'nb_winamax':nb_winamax,'acteurs':out},open(sys.argv[1]+'/bilan.json','w'),ensure_ascii=False,indent=1)
