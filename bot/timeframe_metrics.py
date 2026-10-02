"""Frequency-specific metrics; daily beta is never relabelled as an intraday beta."""
import copy
from datetime import datetime
from .indicators import analyze_prices
from .models import iso
from .tracking import TIMEFRAMES


def price_metrics(coin,histories,window,rf,now,tf):
    r=analyze_prices(coin,histories,window,rf,now,period=TIMEFRAMES[tf])
    frame=histories.get(coin)
    symbol=frame.attrs.get('symbol',coin+'/USDT') if frame is not None else coin+'/USDT'
    for row in r.rows.values():
        if row.value is not None:
            row.source+='; instrument '+symbol
            if frame is not None and frame.attrs.get('derivation'):row.note+=' '+frame.attrs['derivation']
            row.frequency=tf
            row.unit=row.unit.replace('USD','USDT') if frame is not None and frame.attrs.get('quote')=='USDT' else row.unit
            if tf!='1d':
                row.note=row.note.replace('daily',tf+' bar').replace('Daily',tf+' bar').replace('one-day','one-bar').replace('Next-day','Next-bar').replace('day lag','bar lag').replace('days','bars')
                row.unit=row.unit.replace('daily',tf+' bar').replace('days','bars')
                if isinstance(row.value,dict) and 'next_day_volatility' in row.value:
                    row.value['next_bar_volatility']=row.value.pop('next_day_volatility')
        elif tf!='1d':
            if row.id<=33 or row.id in (54,55,56,57,58,59,75,76,77):
                row.note='Daily benchmark analysis only. Select 24h / 1d for aligned BTC/ETH beta.'
            elif row.id>=81:
                row.note='Requires native external/snapshot observations; not derived from this candle timeframe. See daily/native context.'
            else:row.note=row.note.replace('UTC daily',tf).replace('daily',tf+' bar')
    return r


def derivative_context(result,now):
    """Summaries retain their real measurement windows and native units."""
    out=copy.deepcopy(result)
    boundary=int(now)//86400*86400;start=boundary-86400
    events={e['timestamp']:e for e in out.get('funding_history',[]) if start<e['timestamp']<=boundary}
    interval=(out.get('funding') or {}).get('interval')
    hours=None
    try:
        if isinstance(interval,str) and interval.endswith('h'):hours=float(interval[:-1])
    except ValueError:pass
    complete=bool(hours and hours>0 and 24/hours==len(events))
    if complete:
        stamps=sorted(events)
        complete=all(abs(b-a-hours*3600)<1 for a,b in zip(stamps,stamps[1:])) and abs(stamps[-1]-boundary)<1
    out['daily_funding']={'start':iso(start),'end':iso(boundary),'events':len(events),
        'value':sum(e['value'] for e in events.values()) if complete else None,
        'status':'ok' if complete else 'unavailable','unit':'fraction / settled 24h window',
        'note':'Sum of settled funding events; withheld unless the reported interval covers the entire closed UTC day.'}
    snapshots=[]
    for e in out.get('oi_history',[]):
        try:snapshots.append((datetime.fromisoformat(e['as_of']).timestamp(),e))
        except (ValueError,TypeError):pass
    left=[e for t,e in snapshots if abs(t-start)<1];right=[e for t,e in snapshots if abs(t-boundary)<1]
    daily={'value':None,'change_fraction':None,'unit':None,'start':iso(start),'end':iso(boundary),'status':'unavailable',
           'note':'Requires exact start/end daily OI observations on this instrument with matching units.'}
    if left and right and left[-1]['unit']==right[-1]['unit'] and left[-1]['value']>0:
        daily.update(value=right[-1]['value'],change_fraction=right[-1]['value']/left[-1]['value']-1,unit=right[-1]['unit'],status='ok')
    out['daily_oi']=daily
    return out


def add_derivative_studies(pack,derivative,now):
    for key,name in [('funding','Funding rate'),('open_interest','Open interest')]:
        row=derivative.get(key);daily=derivative.get('daily_funding' if key=='funding' else 'daily_oi',{})
        study={'id':'funding' if key=='funding' else 'oi','name':name,'value':row['value'] if row else None,
               'status':'unavailable','unit':row['unit'] if row else 'native','source':derivative['source'],
               'as_of':row.get('as_of') if row else None,'frequency':'native snapshot',
               'reading':'Not applicable on spot' if derivative['market']=='spot' else row.get('kind','Native observation; not a candle-derived value') if row else 'Native observation unavailable',
               'method':'Exchange-reported observation for this exact instrument. No multiplication to manufacture another timeframe.',
               'note':'Daily evidence is shown separately; unavailable historical data is not inferred.', 'daily':daily}
        if row:
            age=now-datetime.fromisoformat(row['as_of']).timestamp()
            study['status']='ok' if -5<=age<=600 else 'stale'
        pack['rows'].append(study)
