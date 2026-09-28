"""Human-readable, descriptive research triage across three distinct horizons.

Attention thresholds are operator controls, NOT fitted trading rules or probabilities.
Nothing in this module places orders or labels a coin safe to buy.
"""
from dataclasses import dataclass
from datetime import datetime
import math
import time
from .models import iso

CORE_IDS = (34, 3, 24, 26, 40, 60, 81, 83, 84, 90, 91, 92, 94, 95, 97,
            114, 119, 120, 121, 128, 130, 131, 134)


def seconds_old(timestamp, now):
    try:
        date=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        if date.tzinfo is None:return float('inf')
        return now-date.timestamp()
    except (ValueError,TypeError,AttributeError):return float('inf')


def timeframes(symbol, histories, bars, boundary):
    """Helper diagnostics supplement the 136 IDs; they do not replace book definitions."""
    daily=histories.get(symbol)
    output={'intraday':{'status':'unavailable','reason':'Closed five-minute candles missing'},
            'swing':{'status':'unavailable','reason':'At least 51 closed daily candles required'},
            'long_term':{'status':'unavailable','reason':'At least 91 closed daily candles required'}}
    if bars is not None and len(bars)>=14:
        bars=bars.loc[bars.index+300<=boundary]
        if len(bars)>=14:
            end=int(bars.index[-1])+300
            volume='quote_volume' if 'quote_volume' in bars else 'volume'
            prior=bars[volume].iloc[-13:-1].mean()
            output['intraday']={'status':'ok' if boundary-end<=300 else 'stale','as_of':iso(end),
                                'source':bars.attrs.get('source','unknown'),'return_5m':float(bars.close.iloc[-1]/bars.close.iloc[-2]-1),
                                'return_1h':float(bars.close.iloc[-1]/bars.close.iloc[-13]-1),
                                'volume_ratio':float(bars[volume].iloc[-1]/prior) if prior>0 else None,
                                'volume_method':'Last closed 5m volume / mean of prior 12 closed 5m bars; latest bar excluded'}
    if daily is None or len(daily)<51:return output
    last=daily.close.iloc[-1]
    sma20=daily.close.tail(20).mean();sma50=daily.close.tail(50).mean()
    status='ok' if boundary-(int(daily.index[-1])+86400)<=93600 else 'stale'
    state='above' if last>sma20 and last>sma50 else 'below' if last<sma20 and last<sma50 else 'mixed'
    common={'status':status,'as_of':iso(int(daily.index[-1])+86400),'source':daily.attrs.get('source','unknown')}
    output['swing']=common|{'trend':state,'return_7d':float(last/daily.close.iloc[-8]-1),
                           'sma20':float(sma20),'sma50':float(sma50),
                           'method':'Last closed daily price vs SMA20/SMA50; descriptive trend, not entry timing'}
    if len(daily)>=91:
        output['long_term']=common|{'return_30d':float(last/daily.close.iloc[-31]-1),'return_90d':float(last/daily.close.iloc[-91]-1),
                                  'reason':'Price performance only; token value capture, unlocks and custody still require independent evidence'}
    return output


def metric(coin,identifier,now):
    row=next((m for m in coin['metrics'] if m['id']==identifier),None)
    if not row or row['status']!='ok':return None
    frequency=row.get('frequency','')
    ttl=93600 if frequency=='1d' else 660 if frequency=='5m' else 180
    if identifier in (113,114,118,119,120,121,122,128):ttl=3*86400
    if identifier==97:
        try:ttl=float(frequency.removesuffix('h'))*3600+3600
        except ValueError:return None
    age=seconds_old(row.get('as_of'),now)
    if not -30<=age<=ttl:return None
    return row


def profile(coin,name,now):
    item=coin.get('timeframes',{}).get(name,{})
    ttl=660 if name=='intraday' else 93600
    return item if item.get('status')=='ok' and -30<=seconds_old(item.get('as_of'),now)<=ttl else None


def number(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):return float(value)
    return None


def pct(value):return f'{value*100:+.2f}%' if number(value) is not None else 'N/A'


def attention(coin,settings,now=None):
    now=time.time() if now is None else now
    events=[]
    def add(kind,reason,stamp):events.append({'key':f"{coin['symbol']}:{kind}",'coin':coin['symbol'],'kind':kind,'reason':reason,'as_of':stamp})
    q=coin.get('quote')
    if not q or q.get('status')!='ok' or not -30<=seconds_old(q.get('as_of'),now)<=120:
        add('data','Fresh price missing; is reading par market conclusion pending rakho.',q.get('as_of','') if q else '')
    intra=profile(coin,'intraday',now)
    if intra:
        move=number(intra.get('return_1h'));ratio=number(intra.get('volume_ratio'))
        if move is not None and abs(move)>=settings.alert_move_pct/100:
            add('move',f"Closed 1h move {pct(move)}; configured attention level {settings.alert_move_pct:g}%.",intra['as_of'])
        if ratio is not None and ratio>=settings.alert_volume_ratio:
            add('volume',f"5m volume {ratio:.1f}x prior-hour average; participation badhi, direction prove nahi hoti.",intra['as_of'])
    spread=metric(coin,83,now)
    if spread and number(spread['value']) is not None and spread['value']>=settings.alert_spread_bps:
        add('liquidity',f"Spread {spread['value']:.1f} bps; execution cost review karo.",spread['as_of'])
    return events


def overview(report,settings,now=None):
    now=time.time() if now is None else now
    demo=report['mode'].startswith('DEMO')
    lines=['ATLAS | AUTOMATIC RESEARCH DESK',f"{report['scan_boundary'][:16].replace('T',' ')} UTC | {len(report['coins'])} coins | scan {report['interval_seconds']//60} min"]
    if demo:lines.append('DEMO: synthetic example, current market nahi.')
    if seconds_old(report['generated_at'],now)>settings.interval*2:lines.append('REPORT PURANI HAI — scanner status check karo.')
    flags=[event for c in report['coins'] for event in attention(c,settings,now)]
    groups={name:[] for name in ('data','move','volume','liquidity')}
    for e in flags:groups[e['kind']].append(e['coin'])
    lines+=['','ABHI KIS PAR DHYAN DENA HAI']
    for kind,label in [('data','Data check'),('liquidity','Execution-cost check'),('move','Price-move review'),('volume','Volume-change review')]:
        if groups[kind]:lines.append(f"• {label}: {', '.join(groups[kind])}")
    if not flags:lines.append('• Configured attention levels par koi flag nahi. Iska matlab buy approval nahi.')
    intra=[(c,profile(c,'intraday',now)) for c in report['coins']]
    valid=[(c,p) for c,p in intra if p]
    leaders=sorted(valid,key=lambda cp:abs(cp[1]['return_1h']),reverse=True)[:4]
    lines+=['','INTRADAY — closed 5m / 1h']
    lines.append('1h changes: '+(' | '.join(f"{c['symbol']} {pct(p['return_1h'])}" for c,p in leaders) or 'N/A'))
    lines.append(f'Usable intraday candles: {len(valid)}/{len(intra)}. Volume, spread, depth aur flow context coin card mein.')
    swing={k:[] for k in ('above','below','mixed','missing')}
    for c in report['coins']:
        p=profile(c,'swing',now);swing[p['trend'] if p else 'missing'].append(c['symbol'])
    lines+=['','SWING — closed daily candles']
    for state,label in [('above','SMA20/50 ke upar'),('below','SMA20/50 ke neeche'),('mixed','Mixed trend'),('missing','Data missing')]:
        if swing[state]:lines.append(f"{label}: {', '.join(swing[state])}")
    long=[(c,profile(c,'long_term',now)) for c in report['coins']]
    leaders=sorted([(c,p) for c,p in long if p],key=lambda cp:cp[1]['return_30d'],reverse=True)[:4]
    mvrv=sum(metric(c,114,now) is not None for c in report['coins'])
    network=sum(metric(c,120,now) is not None or metric(c,121,now) is not None for c in report['coins'])
    lines+=['','LONG-TERM — slower context',
            '30d performance: '+(' | '.join(f"{c['symbol']} {pct(p['return_30d'])}" for c,p in leaders) or 'N/A'),
            f'MVRV coverage {mvrv}/{len(long)} | network activity {network}/{len(long)}. Performance se fair value decide nahi hoti.',
            '', 'NEXT: flagged coins ki wajah dekho; thesis, invalidation aur affordable loss clear hone par hi apna decision lo.',
            'Auto summary refresh hoti hai. Naye sustained flags par alag alert. Saare 136 indicators buttons se available hain.',
            'Thresholds research filters hain; tested entry/exit signals nahi.']
    return '\n'.join(lines)


def coin_brief(report,coin,settings,now=None):
    now=time.time() if now is None else now
    lines=[f"{coin['symbol']} | INTRADAY + SWING + LONG-TERM",f"Scan {report['scan_boundary'][:16].replace('T',' ')} UTC"]
    if report['mode'].startswith('DEMO'):lines.append('DEMO: synthetic fixtures')
    q=coin.get('quote')
    if q:lines.append(f"Quote ${q['price']:,.4f} | {q['as_of'][:19]} UTC | {q['source']}")
    flags=attention(coin,settings,now)
    lines+=['','DHYAN DENE KI WAJAH']+[f"• {e['reason']}" for e in flags]
    if not flags:lines.append('• Koi configured flag nahi; entry automatic approve nahi hoti.')
    def reading(i):
        m=metric(coin,i,now)
        return m['value'] if m else None
    p=profile(coin,'intraday',now)
    lines+=['','INTRADAY']
    if p:
        ratio=p.get('volume_ratio')
        lines.append(f"5m {pct(p['return_5m'])} | 1h {pct(p['return_1h'])} | 5m volume {f'{ratio:.1f}x' if ratio is not None else 'N/A'}")
    else:lines.append('Closed 5m/1h candles missing or stale.')
    spread=reading(83);depth=reading(84);flow=reading(90)
    lines.append(f"Spread {f'{spread:.2f} bps' if number(spread) is not None else 'N/A'}; narrow spread bhi fill guarantee nahi.")
    if isinstance(depth,dict):lines.append(f"±10 bps depth: bids ${depth['bid']:,.0f} / asks ${depth['ask']:,.0f}; quotes cancel ho sakte hain.")
    lines.append(f"Executed buy/sell imbalance: {f'{flow:+.2f}' if number(flow) is not None else 'N/A — complete stream needed'}")
    oi=reading(95);funding=reading(97)
    lines.append(f'OI change {pct(oi)} | realized funding {pct(funding)} per interval (optional feed).')
    p=profile(coin,'swing',now)
    lines+=['','SWING']
    if p:lines.append(f"Daily trend: { {'above':'SMA20/50 ke upar','below':'SMA20/50 ke neeche','mixed':'mixed'}[p['trend']] }; 7d {pct(p['return_7d'])}")
    b=reading(3);fit=reading(24)
    lines.append(f"BTC beta {f'{b:.2f}' if number(b) is not None else 'N/A'} | R² {f'{fit:.2f}' if number(fit) is not None else 'N/A'}")
    lines.append(f"Prior-fit unexplained daily return {pct(reading(26))}; informed buying ka proof nahi.")
    lines.append(f"Daily volatility {pct(reading(40))}; historical daily VaR95 {pct(reading(60))}. Loss is estimate se bada ho sakta hai.")
    p=profile(coin,'long_term',now)
    lines+=['','LONG-TERM']
    if p:lines.append(f"30d {pct(p['return_30d'])} | 90d {pct(p['return_90d'])}")
    mvrv=reading(114);activity=reading(120);tx=reading(121)
    lines.append(f"MVRV {f'{mvrv:.2f}' if number(mvrv) is not None else 'N/A'} | active addresses {f'{activity:,.0f}' if number(activity) is not None else 'N/A'} | transactions {f'{tx:,.0f}' if number(tx) is not None else 'N/A'}")
    lines+=['Network readings daily hain; users/holders ki exact ginti nahi. Token value capture, unlocks aur custody independent check maangte hain.',
            '',f"Coverage: {coin['coverage'].get('ok',0)}/136 at scan; completeness, confidence nahi.",
            'Entry/SL/target is build mein validated nahi. Useful metrics ke button par sources aur timestamps milenge.']
    return '\n'.join(lines)


@dataclass
class AlertState:
    active: dict
    pending: dict
    last_scan: str|None=None


class AlertTracker:
    """Alert on new conditions, not every unchanged scan. Two distinct observations
    confirm market flags. Two clear scans re-arm. Data failures alert immediately.
    State is in-memory; a restart performs a new baseline, documented to the operator.
    """
    def __init__(self):self.state=AlertState({}, {})

    def update(self,report,settings,now=None):
        if self.state.last_scan==report['scan_id']:return []
        self.state.last_scan=report['scan_id']
        events={e['key']:e for c in report['coins'] for e in attention(c,settings,now)}
        emitted=[]
        for key,state in list(self.state.active.items()):
            state['clear']=0 if key in events else state['clear']+1
            if state['clear']>=2:del self.state.active[key]
        for key in list(self.state.pending):
            if key not in events:del self.state.pending[key]
        for key,event in events.items():
            if key in self.state.active:continue
            state=self.state.pending.setdefault(key,{'count':0,'stamp':None})
            stamp=event['as_of']
            if stamp!=state['stamp'] or event['kind']=='data':state['count']+=1;state['stamp']=stamp
            if event['kind']=='data' or state['count']>=2:
                emitted.append(event);self.state.active[key]={'clear':0};del self.state.pending[key]
        return emitted
