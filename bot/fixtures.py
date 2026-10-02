"""Deterministic demo fixtures and practical summaries; never live market data."""
import numpy as np
import pandas as pd
from collections import Counter
from .models import iso
from .providers import validated_candles

def practical_insights(r,q,reference):
    checks=[]
    counts=Counter(m.status for m in r.rows.values())
    checks.append({'name':'Evidence coverage','value':f"{counts['ok']}/136 current observations or calculations",'note':'Completeness is not a probability of correctness. Model, scenario and imported rows are counted separately.'})
    checks.append({'name':'Price freshness at scan','value':'Current' if q and q['age_seconds']<=120 else 'Stale or missing','note':q['source']+'; '+q['as_of'] if q else 'No quote source available'})
    beta=r.rows[3]; residual=r.rows[26]; fit=r.rows[24]
    if beta.status=='ok' and fit.status=='ok':checks.append({'name':'Market exposure','value':f'BTC beta {beta.value:.2f} · R² {fit.value:.2f}','note':'A higher return can reflect higher beta. Low fit limits market attribution.'})
    if residual.status=='ok':checks.append({'name':'Unexplained daily move','value':f'{residual.value*100:+.2f}%','note':'Prior-fit BTC residual; not proof of informed buying or manipulation.'})
    if q and reference and q['source'].startswith('Kraken') and q['age_seconds']<=60 and reference['age_seconds']<=60:
        divergence=abs(q['price']/reference['price']-1)*10000
        checks.append({'name':'Cross-venue price difference','value':f'{divergence:.1f} bps','note':'Kraken midpoint vs Coinbase recent trade, USD pairs. Timing and spread can explain differences; not arbitrage profit.'})
    else:checks.append({'name':'Cross-venue price difference','value':'Unavailable','note':'Needs two fresh, comparable quotes; one source does not independently verify itself.'})
    checks.extend([{'name':'Execution readiness','value':'Needs your order details','note':'Real fees, realized slippage, size and fill quality cannot be inferred from a chart.'},
                   {'name':'Survival and discipline','value':'Manual review','note':'Check essential capital, invalidation and urgency. The bot cannot observe your finances or psychology.'}])
    return checks

def demo_data(coins,boundary):
    rng=np.random.default_rng(136)
    end=boundary//86400*86400
    times=np.arange(end-366*86400,end,86400)
    common=rng.normal(.0008,.025,len(times)-1)
    data={}
    bases={'BTC':64000,'ETH':3200,'SOL':140,'XRP':.6,'ADA':.45,'DOGE':.12,'LINK':15,'AVAX':32,'DOT':6,'LTC':80}
    for idx,c in enumerate(coins):
        changes=common if c=='BTC' else common*(.8+idx*.08)+rng.normal(0,.015,len(common))
        prices=bases.get(c,25)*np.r_[1,np.cumprod(1+changes)]
        rows=[dict(time=int(t),open=float(p),high=float(p*1.02),low=float(p*.98),close=float(p),volume=10000.,quote_volume=float(p*10000)) for t,p in zip(times,prices)]
        data[c,'daily']=validated_candles(rows,'SYNTHETIC demonstration',86400,now=boundary)
        intraday_rng=np.random.default_rng(13600+idx)
        itimes=np.arange(boundary-300*300,boundary,300)
        iprices=prices[-1]*np.cumprod(1+intraday_rng.normal(0,.002,len(itimes)))
        irows=[dict(time=int(t),open=float(p),high=float(p*1.003),low=float(p*.997),close=float(p),volume=float(v))
               for t,p,v in zip(itimes,iprices,intraday_rng.uniform(100,10000,len(itimes)))]
        data[c,'intraday']=validated_candles(irows,'SYNTHETIC demonstration',300,now=boundary)
        data[c,'reference_quote']={'price':float(prices[-1]),'as_of':iso(boundary),'source':'SYNTHETIC demonstration','age_seconds':0}
    return data
