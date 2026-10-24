import shapefile, pandas as pd, numpy as np, json, hashlib, collections
from scipy.spatial import cKDTree
from sklearn.linear_model import LogisticRegression
def load(p):
    r=shapefile.Reader(p); f=[x[0] for x in r.fields[1:]]
    return pd.DataFrame([list(x) for x in r.records()],columns=f),r
c,cr=load('Data1/Customer_Clip')
c['x']=[s.points[0][0] for s in cr.shapes()]; c['y']=[s.points[0][1] for s in cr.shapes()]
d,_=load('Data1/arcos_prod_damage_09_28_Clip')
d['x']=d.Longitude; d['y']=d.Latitude
d['ev']=np.where(pd.to_datetime(d.Report_Tim).dt.year==2025,'feb25','other')
d['days']=(pd.to_datetime(d.Repaired_D)-pd.to_datetime(d.Report_Tim)).dt.days
k=111320.0; cy=37.2
def xy(df): return np.c_[df.x*k*np.cos(np.radians(cy)), df.y*k]
C=xy(c)
# upstream
up={}
cnt=collections.Counter()
for l in open('matched_upstream/matched_upstream.txt',encoding='utf8',errors='ignore'):
    t=l.rstrip('\r\n').split('\t'); 
    ch=[]
    for e in t[1:]:
        p=e.split('*')
        if len(p)<4: continue
        typ='T' if 'xfrmr' in p[0] else 'R' if 'Recloser' in p[2] else 'F' if p[2] in('Fuse','Switch') else 'B' if 'Breaker' in p[2] else 'O'
        ch.append((typ,p[1])); 
    up[t[0]]=ch
    for e in set(ch): cnt[e]+=1
c['premise_nu']=c.premise_nu.astype(str)
def feat(row):
    ch=up.get(row,[]); 
    return (sum(1 for t,_ in ch if t=='F'), sum(1 for t,_ in ch if t=='R'))
fr=c.premise_nu.map(feat); c['nF']=[a for a,b in fr]; c['nR']=[b for a,b in fr]
# local damage density (excluding target event for the label)
oth=d[d.ev=='other']; feb=d[d.ev=='feb25']
To=cKDTree(xy(oth)); Tf=cKDTree(xy(feb)); Ta=cKDTree(xy(d))
c['loc_other']=[len(x) for x in To.query_ball_point(C,1500)]
c['dist_dmg']=Ta.query(C)[0]
y=np.array([len(x)>0 for x in Tf.query_ball_point(C,400)]).astype(int)
cc=c.circuit_id.value_counts()
dc=d.groupby('Circuit').size()
d['cnum']=pd.to_numeric(d.Circuit.str.replace('CIR',''),errors='coerce'); d=d.dropna(subset=['cnum']); d['cnum']=d.cnum.astype(int)
c['cnum']=pd.to_numeric(c.circuit_id,errors='coerce')
cd=d.groupby('cnum').size()
c['circ_rate']=c.cnum.map(cd).fillna(0)/c.circuit_id.map(cc)*1000
c['oh']=(c.kind_of_se=='Overhead').astype(int)
F=pd.DataFrame({'dmg_nearby':np.log1p(c.loc_other),'circuit_rate':np.log1p(c.circ_rate),'overhead':c.oh,'fuses':c.nF,'reclosers':c.nR})
mu,sd=F.mean(),F.std()
Z=(F-mu)/sd
m=LogisticRegression(C=1.0).fit(Z,y)
print('base rate',y.mean(),'coef',dict(zip(F.columns,m.coef_[0].round(2))),m.intercept_)
from sklearn.metrics import roc_auc_score; print('AUC',roc_auc_score(y,m.predict_proba(Z)[:,1]))
c['p']=m.predict_proba(Z)[:,1]
# priority facilities (web mercator -> lon/lat)
def merc(x,yv): return x/6378137*180/np.pi, (2*np.arctan(np.exp(yv/6378137))-np.pi/2)*180/np.pi
pf=[]
for f in ['Data1/Hospitals_Medic_Pairwis_Clip','Data1/Fire_Stations_E_Pairwis_Clip','Data1/Ambulance_Servi_Pairwis_Clip']:
    r=shapefile.Reader(f)
    for s in r.shapes(): 
        lo,la=merc(*s.points[0]); pf.append((lo,la,f.split('/')[1][:4]))
pf=pd.DataFrame(pf,columns=['x','y','t']); Tp=cKDTree(xy(pf))
c['dpf']=Tp.query(C)[0]
# historical repair duration by damage type (feb25 event, repaired)
h=feb[(feb.Repaired=='Yes')&(feb.days>=0)&(feb.days<=14)]
hist={t:[round(float(g.days.quantile(q)),1) for q in(.5,.9)]+[int(len(g))] for t,g in h.groupby('Inform_Wor') if len(g)>=5}
print(hist)
mix={str(k_):v.to_dict() for k_,v in d.groupby('cnum').Inform_Wor.value_counts().unstack(fill_value=0).iterrows()}
# customers for app: use the entire dataset (every premise becomes a node)
rng=np.random.default_rng(7)
ls=c[c.critical_c!='N/A']
s=c.drop_duplicates('id')
def anon(v): return hashlib.sha256(('hk26'+str(v)).encode()).hexdigest()[:6].upper()
cidx={v:i for i,v in enumerate(sorted(c.circuit_id.unique()))}
rows=[]
for i,r in s.iterrows():
    ch=up.get(r.premise_nu,[]); chain=[[t,int(cnt[(t,i2)])] for t,i2 in ch if t in 'TFRB']
    rows.append([anon(r.premise_nu),round(float(r.x),6),round(float(r.y),6),cidx[r.circuit_id],int(r.oh),
      round(float(F.loc[i,'dmg_nearby']),2),round(float(F.loc[i,'circuit_rate']),2),int(r.nF),int(r.nR),
      int(r.dpf<3200),int(r.critical_c!='N/A'),int(min(r.dist_dmg,9999)),chain[:5],round(float(r.p),3)])
circ={cidx[k_]:[anon('c'+k_),int(cc[k_]),int(cd.get(int(k_),0)) if str(k_).isdigit() else 0] for k_ in cidx}
dm=d
out=dict(rows=[[ round(v,6) if isinstance(v,float) else v for v in r] for r in rows],circ=circ,
 dmg=[[round(float(a),6),round(float(b),6),int(e=='feb25')] for a,b,e in zip(dm.x,dm.y,dm.ev)],
 coef=[round(float(v),3) for v in m.coef_[0]],b=round(float(m.intercept_[0]),3),mu=[round(float(v),3) for v in mu],sd=[round(float(v),3) for v in sd],
 names=list(F.columns),hist=hist,base=round(float(y.mean()),3),auc=round(roc_auc_score(y,m.predict_proba(Z)[:,1]),3),
 n=len(c),nd=len(d),ncirc=len(cidx),nls=int((c.critical_c!='N/A').sum()))
json.dump(out,open('app_data.json','w'),separators=(',',':'))
import os; print(os.path.getsize('app_data.json'), len(rows))
