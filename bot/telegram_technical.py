"""Technical-study menus share the completed scan; no extra provider calls."""
from urllib.parse import urlencode
from .technical import TIMEFRAMES, compact
from .telegram_controls import button
from .markets import age


def choose(coins):
    rows=[[button(c,f'tech:{c}:5m') for c in coins[i:i+3]] for i in range(0,len(coins),3)]
    rows.append([button('Controls','ctl:home'),button('136 book metrics','research:coins')])
    return 'TECHNICAL DASHBOARD\nRSI · MFI · OBV · EMA · MACD\nChoose a research coin, then 5m or 1d candles. OI remains separate derivatives context.',{'inline_keyboard':rows}


def page(report,settings,symbol,timeframe='5m',indicator=None):
    if timeframe not in TIMEFRAMES:raise ValueError('Use 5m or 1d')
    coin=next((c for c in (report or {}).get('coins',[]) if c['symbol']==symbol),None)
    pack=coin.get('technical',{}).get(timeframe) if coin else None
    rows=[[button('5 min candles',f'tech:{symbol}:5m'),button('Daily candles',f'tech:{symbol}:1d')]]
    if not pack:text=f'{symbol}: waiting for a completed technical scan. Add it to the research watchlist in Controls.'
    else:
        text=f"{symbol} · TECHNICAL · {timeframe} SPOT USD\n{report['mode']}\nLast report: {report['generated_at']}\nClosed candle end: {pack['as_of'] or 'unavailable'}\nSource: {pack['source'] or 'unavailable'} · {pack['bars']} bars\n"
        effective_stale=pack['status']=='stale' or age(pack['as_of'])>pack['period_seconds']+(7200 if timeframe=='1d' else 120)
        if effective_stale and pack['as_of']:text+='STALE CANDLES — previous observations only\n'
        found=False
        for row in pack['rows']:
            if indicator and row['id']!=indicator:continue
            found=True;status='stale' if row['status']=='ok' and effective_stale else row['status']
            text+=f"\n{row['name']}: {compact(row)} [{status}]\n{row['reading']}\n"
            if indicator:text+=f"\nComponents: {row['value']}\nUnit: {row['unit']}\nMethod: {row['method']}\n{row['note']}\nHistory starts: {pack['history_start']}\n"
        if indicator and not found:raise ValueError('Unknown indicator')
        if not indicator:
            rows.extend([[button(row['name'],f"tech:{symbol}:{timeframe}:{row['id']}") for row in pack['rows'][i:i+2]] for i in range(0,5,2)])
        else:rows.append([button('← All five',f'tech:{symbol}:{timeframe}')])
        text+='\nCalculated from closed spot candles; OI belongs to a separate perpetual contract. No automatic buy/sell instruction.'
    rows.append([button('Choose coin','tech:coins'),button('OI book metric',f'research:metric:{symbol}:94')])
    rows.append([button('Perpetual markets','markets:perpetual:0'),button('Controls','ctl:home')])
    if settings.public_url:
        url=settings.public_url+'/#'+urlencode({'view':'technical','coin':symbol,'tf':timeframe,'indicator':indicator or 'rsi'})
        rows.append([{'text':'🌐 Open technical dashboard','url':url}])
    return text,{'inline_keyboard':rows}


def access(report):
    lines=['CONNECTION HEALTH · LAST COMPLETED SCAN']
    if not report:lines.append('No completed scan yet. Use /status.')
    else:
        lines.append('Report: '+report['generated_at'])
        for host in ('api.binance.com','fapi.binance.com','dapi.binance.com','api.bitget.com','api.bybit.com','www.okx.com','api.kraken.com','api.exchange.coinbase.com'):
            state=report.get('provider_health',{}).get(host)
            lines.append(f"\n{host}: {state['status']}\nChecked: {state['checked_at']}\n{state.get('reason','Endpoint responded; access can change.')}" if state else f'\n{host}: not checked in this process')
    lines.append('\nRequests originate from the bot host, not your uptime monitor. HTTP 403/451 can indicate restricted access; the bot holds that host for one hour and keeps other feeds running. A responding endpoint is not proof of account/product eligibility.')
    return '\n'.join(lines),{'inline_keyboard':[[button('Controls','ctl:home'),button('Technical indicators','tech:coins')]]}
