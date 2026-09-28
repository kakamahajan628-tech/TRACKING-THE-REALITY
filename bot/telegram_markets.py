"""Bounded Telegram menus: all rows remain reachable without a giant chat message."""
from .markets import age


def menu():
    return {'inline_keyboard':[[{'text':'All markets','callback_data':'markets:all:0'},
                                {'text':'Perpetuals','callback_data':'markets:perpetual:0'}],
                               [{'text':'Spot','callback_data':'markets:spot:0'},
                                {'text':'Full report ZIP','callback_data':'marketzip'}]]}


def page(report,kind='all',number=0):
    if kind not in ('all','spot','perpetual'):raise ValueError('Invalid market type')
    universe=report.get('market_universe',{})
    rows=[(i,r) for i,r in enumerate(universe.get('markets',[])) if kind=='all' or r['kind']==kind]
    pages=max(1,(len(rows)+7)//8);number=max(0,min(number,pages-1))
    lines=[f'ALL EXCHANGES · {kind.upper()} · page {number+1}/{pages}',
           f"Scan: {report['generated_at']}",f'{len(rows)} instruments · Tap an instrument for details.']
    if age(report['generated_at'])>600:lines.append('STALE SNAPSHOT — service has not published a recent scan.')
    if not universe.get('complete'):lines.append('Coverage incomplete; failed or stale sources are shown in the full report.')
    keyboard=[]
    for i,r in rows[number*8:number*8+8]:
        state='stale' if r['status']=='ok' and age(r.get('as_of'))>600 else r['status']
        move=f"{r['change_24h']*100:+.2f}%" if r.get('change_24h') is not None else 'N/A'
        lines.append(f"{r['exchange']} · {r['symbol']} [{r['category']}]\n{r['last'] if r['last'] is not None else 'N/A'} {r['quote']} · 24h {move} · {state}")
        keyboard.append([{'text':f"{r['exchange']} {r['symbol']} {r['category']}"[:60],
                          'callback_data':f"instrument:{report['scan_id']}:{i}"}])
    nav=[]
    if number:nav.append({'text':'← Previous','callback_data':f'markets:{kind}:{number-1}'})
    if number+1<pages:nav.append({'text':'Next →','callback_data':f'markets:{kind}:{number+1}'})
    if nav:keyboard.append(nav)
    keyboard.extend(menu()['inline_keyboard'])
    return '\n\n'.join(lines),{'inline_keyboard':keyboard}


def detail(report,scan_id,index):
    if scan_id!=report['scan_id']:return 'A newer scan is available. Tap All markets to reload the list.',menu()
    rows=report.get('market_universe',{}).get('markets',[])
    if not 0<=index<len(rows):raise ValueError('Invalid instrument index')
    r=rows[index]
    def value(k):return r.get(k) if r.get(k) is not None else 'Unavailable'
    def pct(k):return f"{r[k]*100:+.4f}%" if r.get(k) is not None else 'Unavailable'
    lines=[f"{r['exchange'].upper()} · {r['symbol']} · {r['category']}",
           f"Snapshot: {value('as_of')}",f"State: {'stale' if age(r.get('as_of'))>600 else value('status')}",
           f"Last: {value('last')} {r['price_unit']} · 24h {pct('change_24h')}",
           f"Volume 24h: {value('volume_24h')} {value('volume_unit')}",f"Spread: {value('spread_bps')} bps",
           f"Mark / index: {value('mark_price')} / {value('index_price')} {r['quote']}"]
    if r['kind']=='perpetual':
        lines.extend([f"Funding: {pct('funding_rate')} · period {value('funding_interval_hours')} hours",
                      f"Funding time: {value('funding_as_of')} · {'stale' if age(r.get('funding_as_of'))>600 else value('funding_status')}",
                      f"Meaning: {value('funding_kind')}",f"Next funding: {value('next_funding_at')}",
                      f"OI: {value('open_interest')} {value('open_interest_unit')}",
                      f"OI value: {value('open_interest_value')} {value('oi_value_unit')}",
                      f"OI time: {value('oi_as_of')} · {'stale' if age(r.get('oi_as_of'))>600 else value('oi_status')}"])
    lines.append('Native units; no guaranteed trade signal. Missing funding period is not assumed to be 8 hours.')
    keyboard=menu()
    keyboard['inline_keyboard'].insert(0,[{'text':'Find on TradingView','url':r['tradingview_url']}])
    return '\n'.join(lines),keyboard
