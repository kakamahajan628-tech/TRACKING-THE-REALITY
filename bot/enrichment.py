import json
import math
import time
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from .models import Metric, iso, finite
from .indicators import divide


def age(timestamp, now=None):
    return (time.time() if now is None else now)-datetime.fromisoformat(timestamp.replace('Z','+00:00')).timestamp()


def add_microstructure(result, candles, book, stream, symbol, end):
    start=end-300
    if candles is not None:
        bars=candles.loc[candles.index+300<=end]
        if len(bars):
            last=bars.iloc[-1]
            kwargs=dict(source=candles.attrs['source'],as_of=iso(int(bars.index[-1])+300),frequency='5m',sample=1,
                        status='ok' if end-(int(bars.index[-1])+300)<=300 else 'stale')
            if 'quote_volume' in bars:
                result.put(81,last.quote_volume,'USD','Latest closed 5m candle; venue VWAP × executed base volume',**kwargs)
                result.calc(92,lambda:divide(last.quote_volume,last.volume),'USD','Latest closed 5m candle VWAP',**kwargs)
        # Complete previous UTC day, 288 five-minute returns, including prior-day last close.
        day_end=end//86400*86400
        required=np.arange(day_end-86400-300,day_end,300)
        if set(required).issubset(set(bars.index)):
            closes=bars.loc[required,'close'].to_numpy()
            rv=np.sqrt(np.sum(np.diff(np.log(closes))**2)*365)
            result.put(42,rv,'annualized fraction','Previous complete UTC day; 288 five-minute log returns; sqrt(365) scaling',
                       source=candles.attrs['source'],as_of=iso(day_end),frequency='1d',sample=288,status='ok')
    if book:
        bids,asks=book['bids'],book['asks']
        mid=(bids[0][0]+asks[0][0])/2
        bd=sum(p*q for p,q in bids if p>=mid*.999)
        ad=sum(p*q for p,q in asks if p<=mid*1.001)
        args=dict(source=book['source'],as_of=book['as_of'],frequency='snapshot',sample=len(bids)+len(asks),status='ok')
        result.put(83,(asks[0][0]-bids[0][0])/mid*10000,'bps','Quoted bid-ask spread at retrieval',**args)
        # If capped book has not reached the band boundary, do not claim complete band depth.
        complete=(bids[-1][0]<=mid*.999 or len(bids)<500) and (asks[-1][0]>=mid*1.001 or len(asks)<500)
        if complete:
            result.put(84,{'bid':bd,'ask':ad},'USD within ±10 bps','Snapshot depth; cancelable orders are not executed demand',**args)
            result.calc(89,lambda:divide(bd-ad,bd+ad),'ratio','Same venue, symmetric ±10 bps band',**args)
        else:
            result.missing(84,'500-level book does not cover ±10 bps; complete depth unknown')
            result.missing(89,'Incomplete depth band')
    if stream:
        trades=stream.trade_window(symbol,start,end)
        if trades:
            buy=sum(p*q for t,p,q,s in trades if s=='buy')
            sell=sum(p*q for t,p,q,s in trades if s=='sell')
            delta=sum(q*(1 if s=='buy' else -1) for t,p,q,s in trades)
            kw=dict(source='Kraken WebSocket aggressor trades',as_of=iso(end),frequency='5m',sample=len(trades),status='ok')
            result.calc(90,lambda:divide(buy-sell,buy+sell),'ratio','USD aggressor imbalance; continuous connection throughout window',**kw)
            result.put(91,delta,f'{symbol} base units','CVD reset at each 5m boundary; not all-day CVD',**kw)
        else:
            for i in (90,91): result.missing(i,'No complete nonempty 5m trade stream; warm-up, reconnect, or buffer gap')
    result.missing(85,'Realized slippage requires your actual fills and pre-trade midpoint; hypothetical book walk is not realized execution')
    result.missing(86,'Post-trade price impact needs timestamped executions and matched future midpoints; adapter not connected')
    result.missing(88,'Kyle regression needs aligned signed-flow and midpoint history; adapter not connected')
    result.missing(93,'Composite requires real slippage, impact and prior standardized liquidity history; incomplete components')


def add_onchain(result, rows):
    if not rows:return
    eligible=[x for x in rows if age(x['time'])>=86400]
    if not eligible:return
    row=max(eligible,key=lambda x:x['time'])
    end=datetime.fromisoformat(row['time'].replace('Z','+00:00')).timestamp()+86400
    kw=dict(source='Coin Metrics Community; asset-specific definitions',as_of=iso(end),frequency='1d',sample=1,
            status='ok' if time.time()-end<=3*86400 else 'stale')
    def value(key):
        v=float(row[key])
        if not math.isfinite(v) or v<0:raise ValueError('Invalid network metric')
        return v
    for i,field,unit in [(113,'CapRealUSD','USD'),(119,'TxTfrValAdjUSD','USD/day'),(120,'AdrActCnt','addresses/day'),(121,'TxCnt','transactions/day')]:
        if row.get(field) is not None:result.calc(i,lambda f=field:value(f),unit,f'Published series: {field}; observed daily, not five-minute data',**kw)
    for i,a,b,note in [(114,'CapMrktCurUSD','CapRealUSD','MVRV using matched Coin Metrics cap series'),
                       (118,'CapMrktCurUSD','TxTfrValAdjUSD','Daily NVT using matched CM adjusted transfer value'),
                       (122,'TxTfrValAdjUSD','CapMrktCurUSD','Daily velocity; reciprocal of selected NVT')]:
        if row.get(a) is not None and row.get(b) is not None:result.calc(i,lambda a=a,b=b:divide(value(a),value(b)),'ratio',note,**kw)
    # Metric 117 explicitly selects Glassnode in the book; never relabel a CM reconstruction as that series.


def add_derivatives(result,data):
    kw=dict(source='OKX single USDT perpetual; oiUsd as published',as_of=data['as_of'],frequency='snapshot',sample=1,
            status='ok' if age(data['as_of'])<180 else 'stale')
    result.put(94,data['oi_usd'],'USD','Single instrument, not aggregate market OI',**kw)
    prior=data.get('previous')
    if prior and 240<=age(prior['as_of'])-age(data['as_of'])<=420:
        result.calc(95,lambda:divide(data['oi_usd'],prior['oi_usd'])-1,'fraction','Change since previous scan; actual timestamps retained',**kw)
    else:result.missing(95,'Needs a previous OI observation 4–7 minutes earlier; process restart resets history')
    if 'funding' in data:
        hours=data['funding_hours']
        fk=kw|{'as_of':data['funding_as_of'],'frequency':f'{hours:g}h','status':'ok' if age(data['funding_as_of'])<hours*3600+3600 else 'stale'}
        result.put(97,data['funding'],'fraction/interval','Published realized funding; OKX convention, not reconstructed Binance formula',**fk)
        result.calc(98,lambda:math.expm1(math.log1p(data['funding'])*8760/hours),'annualized fraction',
                    'MODEL EXTRAPOLATION: constant last realized rate, compounding, interval inferred from two settlements',**(fk|{'status':'model' if fk['status']=='ok' else 'stale'}))


def add_universe(results,histories,coins,window):
    eligible=[c for c in coins if c in histories and len(histories[c])>=max(window+1,61)]
    # Fixed watchlist: missing constituents invalidate aggregate breadth instead of silently changing the universe.
    if len(eligible)!=len(coins) or len(coins)<2:return
    frames=[histories[c] for c in coins]
    if len({f.attrs['source'] for f in frames})!=1 or len({int(f.index[-1]) for f in frames})!=1:return
    close=pd.concat({c:histories[c].close for c in coins},axis=1).dropna().tail(window+1)
    if len(close)<window+1 or np.any(np.diff(close.index)!=86400):return
    returns=close.pct_change(fill_method=None).dropna()
    day=returns.iloc[-1]
    momentum=(close.iloc[-1]/close.iloc[-31]-1)
    ranks=(rankdata(momentum,method='average')-1)/(len(coins)-1)*100
    cutoff=int(close.index[-1])+86400
    meta=dict(source=frames[0].attrs['source']+'; configured watchlist',as_of=iso(cutoff),frequency='1d',sample=len(coins),
              status='ok' if time.time()-cutoff<93600 else 'stale')
    note='Fixed current watchlist, NOT whole-market or survivorship-controlled historical breadth: '+','.join(coins)
    for coin,rank in zip(coins,ranks):
        r=results[coin]
        r.calc(130,lambda:divide(int((day>0).sum())-int((day<0).sum()),int((day!=0).sum())),'ratio',note,**meta)
        r.put(131,float((close.iloc[-1]>close.tail(50).mean()).mean()*100),'percent',note+'; SMA50',**meta)
        r.put(132,float(day.std(ddof=1)),'daily fraction',note,**meta)
        r.put(134,float(rank),'percentile',note+'; 30 daily returns; ties average ranked',**meta)
    benchmark=histories.get('BTC')
    if benchmark is not None and benchmark.attrs['source']==frames[0].attrs['source']:
        aligned=pd.concat({'BTC':benchmark.close.pct_change(fill_method=None),**{c:returns[c] for c in coins if c!='BTC'}},axis=1).dropna().tail(window)
        others=[c for c in coins if c!='BTC']
        if len(others)>=2 and len(aligned)==window:
            correlations=[aligned[c].corr(aligned.BTC) for c in others]
            for r in results.values():
                r.put(136,{'sd':np.std(correlations,ddof=1),'median':np.median(correlations),
                           'iqr':np.quantile(correlations,.75)-np.quantile(correlations,.25)},'correlation',note+'; BTC itself excluded',**meta)


def load_external(path,results):
    """Explicit normalized vendor-file bridge; no network fetching of arbitrary URLs.

    Source, method, period and timestamp are mandatory. Imported values stay marked
    external until operator validates vendor/account definitions.
    """
    if not path:return []
    errors=[]
    try:
        raw=Path(path).read_text(encoding='utf-8')
        if len(raw)>2_000_000:raise ValueError('External file exceeds 2 MB')
        rows=json.loads(raw)
        if not isinstance(rows,list) or len(rows)>2720:raise ValueError('Expected at most 2720 observations')
        for row in rows:
            try:
                coin=row['coin'].upper(); i=int(row['metric_id'])
                if coin not in results or not 1<=i<=136:raise ValueError('Unknown coin or metric ID')
                for field in ('source','as_of','unit','frequency','method','note'):
                    if not isinstance(row.get(field),str) or not row[field].strip():raise ValueError(f'Missing {field}')
                timestamp=datetime.fromisoformat(row['as_of'].replace('Z','+00:00'))
                if timestamp.tzinfo is None:raise ValueError('as_of must include a UTC offset')
                seconds=age(row['as_of'])
                ttl=int(row['ttl_seconds'])
                if not 1<=ttl<=7*86400 or seconds< -30:raise ValueError('Invalid freshness period/future timestamp')
                finite(row['value'])
                status='external' if seconds<=ttl else 'stale'
                results[coin].put(i,row['value'],row['unit'],f"Imported vendor observation; not independently verified. Method: {row['method']}. {row['note']}",
                                  source=row['source'],as_of=row['as_of'],frequency=row['frequency'],sample=row.get('sample'),status=status)
            except (ValueError,KeyError,TypeError):errors.append('Rejected external observation: invalid identity, provenance, value or timestamp')
    except (OSError,ValueError,TypeError):errors.append('External metrics file unreadable or invalid')
    return errors
