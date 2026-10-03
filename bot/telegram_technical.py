"""Concise, source-aware technical views of the latest completed tracking scan."""
from urllib.parse import urlencode
from .technical import compact
from .tracking import TIMEFRAMES, ist
from .telegram_controls import button
from .markets import age


def choose(coins):
    rows=[[button(c,f'tech:{c}:1d') for c in coins[i:i+3]] for i in range(0,len(coins),3)]
    rows.append([button('Controls','ctl:home'),button('136 metrics','research:coins')])
    return 'Choose a tracked coin. 5m / 1h / 4h / 24h. Eight candle studies + native funding/OI. Daily beta stays in 136 metrics.',{'inline_keyboard':rows}


def page(report,settings,symbol,timeframe='5m',indicator=None):
    if timeframe not in TIMEFRAMES:raise ValueError('Use 5m, 1h, 4h or 1d')
    coin=next((c for c in (report or {}).get('coins',[]) if c['symbol']==symbol),None)
    pack=coin.get('technical',{}).get(timeframe) if coin else None
    rows=[[button('24h' if tf=='1d' else tf,f'tech:{symbol}:{tf}') for tf in TIMEFRAMES]]
    if not pack:text=symbol+': waiting for the selected instrument. Use /status.'
    else:
        route=coin.get('route') or {}
        text=f"{symbol} | {timeframe} | USDT\n{route.get('exchange','unavailable')} {route.get('symbol','')} | {route.get('market','')}\nCandle close: {ist(pack['as_of'])}\nScan: {ist(report['generated_at'])}\n"
        if 'SYNTHETIC' in report['mode']:text+='DEMO - SYNTHETIC DATA\n'
        stale=age(pack['as_of'])>pack['period_seconds']+(7200 if timeframe=='1d' else 120)
        selected=[r for r in pack['rows'] if not indicator or r['id']==indicator]
        if not selected:raise ValueError('Unknown study')
        for row in selected:
            status=row['status']
            if stale and status=='ok' and row.get('frequency')!='native snapshot':status='stale'
            if row.get('frequency')=='native snapshot' and age(row['as_of'])>600 and status=='ok':status='stale'
            unit='per exchange interval' if row['id']=='funding' else row['unit']
            text+=f"\n{row['name']}: {compact(row) if status=='ok' else status.upper()} {unit}"
            if row.get('frequency')=='native snapshot':text+=' [native snapshot]'
            if indicator:
                text+=f"\nAs of: {ist(row['as_of'])}\n{row['reading']}\nMethod: {row['method']}\n{row['note']}"
                if row.get('daily'):
                    d=row['daily'];text+=f"\nClosed daily evidence: {d['status']} | {d.get('value')} {d.get('unit','')}\nDaily end: {ist(d.get('end'))}"
        if not indicator:
            rows.extend([[button(r['name'],f"tech:{symbol}:{timeframe}:{r['id']}") for r in pack['rows'][i:i+2]] for i in range(0,len(pack['rows']),2)])
        else:rows.append([button('All studies',f'tech:{symbol}:{timeframe}')])
    rows.append([button('Choose coin','tech:coins'),button('Daily beta',f'research:metric:{symbol}:3:1d')])
    if settings.public_url:
        url=settings.public_url+'/#'+urlencode({'view':'technical','coin':symbol,'tf':timeframe,'indicator':indicator or 'rsi'})
        rows.append([{'text':'Open dashboard','url':url}])
    return text,{'inline_keyboard':rows}


def access(report):
    lines=['TRACKING SOURCE HEALTH']
    if not report:lines.append('No completed scan yet. Use /status.')
    else:
        lines.append('Last scan: '+ist(report['generated_at']))
        for host,state in report.get('provider_health',{}).items():
            lines.append(f"\n{host}: {state['status']}\n{ist(state['checked_at'])} | {state.get('reason','responded')}")
        for row in report.get('issues',[])[:5]:lines.append(f"{row['coin']}: {row['reason']}")
        lines.append('\nExchange access is measured from the bot host. Missing sources do not produce fabricated values.')
    return '\n'.join(lines),{'inline_keyboard':[[button('Controls','ctl:home'),button('Studies','tech:coins')]]}
