"""Telegram control menus and complete book-metric browsing."""
import json
from urllib.parse import urlencode
from .markets import age
from .models import CATALOG


def dashboard_url(settings,coin=None,metric=None):
    origin=settings.public_url
    if not origin:return None
    if coin is None:return origin+'/'
    return origin+'/#'+urlencode({'view':'metrics','coin':coin,'metric':metric or 3})


def button(text,data):return {'text':text,'callback_data':data}


def home_buttons(settings):
    rows=[[button('⚙ Controls','ctl:home'),button('📊 136 metrics','research:coins')]]
    rows.append([button('📈 Technical indicators','tech:coins'),button('Connection health','access')])
    url=dashboard_url(settings)
    if url:rows.append([{'text':'🌐 Open dashboard','url':url}])
    return rows


def menu(controller,section='home'):
    s=controller.snapshot();revision=s['revision'];rows=[]
    lines=['ATLAS · TELEGRAM CONTROLS',
           f"Loop: {'PAUSED' if s['paused'] else 'ACTIVE'} · Timer: {s['interval']//60} min",
           f"Research coins ({len(s['coins'])}/20): "+', '.join(s['coins']),
           f"Next scan: {s['next_scan_at'] or ('collecting' if s['in_progress'] else 'not scheduled')}",
           'Settings: '+('waiting for a matching scan' if s['settings_pending'] else 'reflected in latest report'),
           f"Save: {s['storage']}"]
    if not s['ready']:lines.append('STORAGE ERROR: '+str(s['storage_error']))
    if section=='coins':
        lines.append('Tap − to remove a research coin. Add accepts symbols, e.g. SOL,DOGE. Global market coverage is controlled separately.')
        rows=[[button('− '+c,f'ctl:remove:{c}:{revision}') for c in s['coins'][i:i+3]] for i in range(0,len(s['coins']),3)]
        rows.append([button('＋ Add coins','ctl:input:add'),button('Replace list','ctl:input:replace')])
    elif section=='timer':
        lines.append('The timer changes report/scan cadence; daily metrics still use daily candles.')
        rows=[[button(f'{m} min',f'ctl:set:interval:{m*60}:{revision}') for m in group] for group in ([5,10,15],[30,60])]
        rows.append([button('Custom minutes','ctl:input:interval')])
    elif section=='coverage':
        lines.append('Research list: 136 records per selected coin. Whole-market mode also scans exchange listings, including coins outside that list.')
        rows.append([button(('✓ ' if s['all_markets'] else '')+'Whole market',f'ctl:set:all_markets:1:{revision}'),
                     button(('✓ ' if not s['all_markets'] else '')+'Research only',f'ctl:set:all_markets:0:{revision}')])
        rows.append([button(('✓ ' if e in s['exchanges'] else '')+e,f'ctl:exchange:{e}:{revision}') for e in ('binance','bitget')])
        rows.append([button(('✓ ' if e in s['exchanges'] else '')+e,f'ctl:exchange:{e}:{revision}') for e in ('bybit','okx')])
        rows.append([button(('✓ ' if t in s['market_types'] else '')+t,f'ctl:type:{t}:{revision}') for t in ('spot','perpetual')])
    elif section=='research':
        lines.append(f"Daily research window: {s['window']} days")
        rows.append([button(f'{d} days',f'ctl:set:window:{d}:{revision}') for d in (90,180,365)])
        rows.append([button('Auto ZIP: '+('ON' if s['send_zip'] else 'OFF'),f"ctl:set:send_zip:{int(not s['send_zip'])}:{revision}")])
        lines.append('When auto ZIP is off, the digest still arrives; Full report ZIP downloads on demand.')
    else:
        rows=[[button('＋ / − Coins','ctl:coins'),button('⏱ Timer','ctl:timer')],
              [button('▶ Resume' if s['paused'] else '⏸ Pause',f"ctl:set:paused:{int(not s['paused'])}:{revision}"),button('↻ Scan now','ctl:scan')],
              [button('Market coverage','ctl:coverage'),button('Research / ZIP','ctl:research')],
              [button('Backup settings','ctl:backup'),button('📊 136 metrics','research:coins')]]
    if section!='home':rows.append([button('← Controls','ctl:home')])
    else:rows.append([button('📈 Technical indicators','tech:coins'),button('Connection health','access')])
    url=dashboard_url(controller.settings)
    if url:rows.append([{'text':'🌐 Open dashboard','url':url}])
    return '\n\n'.join(lines),{'inline_keyboard':rows}


def metric_coins(coins):
    rows=[[button(c,f'research:list:{c}:0') for c in coins[i:i+3]] for i in range(0,len(coins),3)]
    rows.append([button('← Controls','ctl:home')])
    return 'Choose a research coin. All 136 IDs are browsable; missing data stays labelled.',{'inline_keyboard':rows}


def metric_list(report,symbol,page=0):
    coin=next((c for c in report.get('coins',[]) if c['symbol']==symbol),None) if report else None
    if coin is None:return f'{symbol}: waiting for its first completed research scan.',{'inline_keyboard':[[button('↻ Scan now','ctl:scan'),button('Choose coin','research:coins')]]}
    page=max(0,min(int(page),16));items=coin['metrics'][page*8:page*8+8]
    names={d['id']:d['name'] for d in CATALOG}
    lines=[f"{symbol} · ALL 136 METRICS · page {page+1}/17",'Last report: '+report['generated_at']]
    if age(report['generated_at'])>report.get('interval_seconds',300)*2:lines.append('OLD REPORT — values below are the last completed snapshot.')
    keyboard=[]
    for m in items:
        value=json.dumps(m['value'],ensure_ascii=False) if isinstance(m['value'],(dict,list)) else str(m['value'])
        if len(value)>100:value='Components: tap for details'
        lines.append(f"{m['id']:03} {names[m['id']]}\n{m['status']} · {value} {m['unit']}")
        keyboard.append([button(f"{m['id']:03} {names[m['id']]}"[:55],f"research:metric:{symbol}:{m['id']}")])
    nav=[]
    if page:nav.append(button('← Previous',f'research:list:{symbol}:{page-1}'))
    if page<16:nav.append(button('Next →',f'research:list:{symbol}:{page+1}'))
    keyboard.extend([nav,[button('Choose coin','research:coins'),button('Controls','ctl:home')]])
    return '\n\n'.join(lines),{'inline_keyboard':keyboard}


def metric_detail(report,settings,symbol,identifier):
    coin=next((c for c in report.get('coins',[]) if c['symbol']==symbol),None) if report else None
    if coin is None:return metric_list(report,symbol)
    if not 1<=identifier<=136:raise ValueError('Metric ID must be 1–136')
    row=next(m for m in coin['metrics'] if m['id']==identifier);definition=CATALOG[identifier-1]
    text=f"{symbol} · {identifier:03} {definition['name']}\nLast report: {report['generated_at']}\nState at scan: {row['status']}\nValue: {row['value']} {row['unit']}\nAs of: {row['as_of']}\nSource: {row['source']}\n{row['note']}\n\nMethod: {definition['method']}\nBook page {definition['page']}"
    if age(report['generated_at'])>report.get('interval_seconds',300)*2:text='OLD REPORT — last completed snapshot\n'+text
    rows=[[button('← Metrics',f'research:list:{symbol}:{(identifier-1)//8}'),button('Choose coin','research:coins')]]
    url=dashboard_url(settings,symbol,identifier)
    if url:rows.append([{'text':'🌐 Open this metric on website','url':url}])
    return text,{'inline_keyboard':rows}
