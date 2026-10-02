import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from .technical import compact
from .tracking import ist
from decimal import Decimal

ROOT=Path(__file__).parent
HANDBOOK=json.loads((ROOT/'data/handbook.json').read_text(encoding='utf-8')) if (ROOT/'data/handbook.json').exists() else []


def json_text(report):
    return json.dumps(report,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def html_report(report=None,live=False):
    template=(ROOT/'web/index.html').read_text(encoding='utf-8')
    def safe_json(value):
        return json.dumps(value,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return (template.replace('/*CSS*/',(ROOT/'web/style.css').read_text(encoding='utf-8'))
            .replace('/*BOOT*/','const INITIAL='+safe_json(report)+'; const HANDBOOK='+safe_json(HANDBOOK)+'; const LIVE='+str(live).lower()+';')
            .replace('/*APP*/',(ROOT/'web/app.js').read_text(encoding='utf-8')))


def csv_report(report):
    buf=io.StringIO(newline='')
    writer=csv.writer(buf)
    writer.writerow(['scan_id','coin','view_timeframe','metric_id','name','status','value','unit','source','as_of_IST','frequency','sample','note'])
    names={m['id']:m['name'] for m in report['catalog']}
    def safe(value):
        if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):value="'"+value
        return value
    for coin in report['coins']:
        for tf,rows in coin.get('metric_frames',{'1d':coin['metrics']}).items():
            for m in rows:
                writer.writerow([safe(x) for x in [report['scan_id'],coin['symbol'],tf,m['id'],names[m['id']],m['status'],m['value'],m['unit'],m['source'],ist(m['as_of']),m['frequency'],m['sample'],m['note']]])
    return '\ufeff'+buf.getvalue()


def report_bundle(report):
    buf=io.BytesIO()
    with ZipFile(buf,'w',ZIP_DEFLATED) as z:
        z.writestr('report.html',html_report(report))
        z.writestr('metrics.csv',csv_report(report))
        z.writestr('report.json',json_text(report))
        if any(c.get('technical') for c in report['coins']):z.writestr('technical.csv',technical_csv(report))
        if report.get('market_universe',{}).get('enabled'):
            z.writestr('markets.csv',markets_csv(report))
            z.writestr('aggregators.json',json.dumps(report.get('aggregators',{}),ensure_ascii=False,allow_nan=False))
        z.writestr('READ-ME.txt','Open report.html. Selected USDT instruments only. metrics.csv includes 136 IDs per coin per view; unsupported frequencies remain unavailable. Technical: 8 closed-candle studies plus 2 native derivative observations. Beta daily only. Display times IST; candle boundaries retain exchange UTC conventions. USD observations are never silently converted to USDT.\n'+report['mode'])
    return buf.getvalue()


def summary(report,only_coin=None):
    coins=[c for c in report['coins'] if not only_coin or c['symbol']==only_coin]
    lines=['ATLAS | '+ist(report['generated_at']), f"{len(report['coins'])} tracked | USDT | {report.get('interval_seconds',300)//60} min loop"]
    if time.time()-datetime.fromisoformat(report['generated_at']).timestamp()>report.get('interval_seconds',300)*2:lines.append('OLD REPORT - last completed snapshot')
    if not only_coin and len(coins)>8:
        def movement(coin):
            row=next((r for r in coin.get('metric_frames',{}).get('5m',[]) if r['id']==34),{})
            return abs(row['value']) if row.get('status')=='ok' else -1
        coins=sorted(coins,key=movement,reverse=True)
        lines.append('Top 8 by absolute last closed 5m move')
    if 'SYNTHETIC' in report['mode']:lines.append('DEMO - SYNTHETIC DATA')
    if not coins:return 'No completed report for this coin.'
    for c in coins[:8]:
        q=c.get('quote');route=c.get('route') or {};m={r['id']:r for r in c['metrics']}
        price=format(Decimal(str(q['price'])),'f')+' USDT' if q else 'price unavailable'
        state=' ['+q['status']+']' if q and q['status']!='ok' else ''
        lines+=['',c['symbol']+' | '+price+state,route.get('exchange','no source')+' | '+route.get('market','unavailable')]
        moves=[]
        for tf,rows in c.get('metric_frames',{}).items():
            row=next((r for r in rows if r['id']==34),{})
            moves.append(tf+(' '+f"{row['value']*100:+.2f}%" if row.get('status')=='ok' else ' N/A'))
        lines.append(' | '.join(moves))
        lines.append('Daily beta BTC '+(f"{m[3]['value']:.2f}" if m[3]['status']=='ok' else 'N/A')+' / ETH '+(f"{m[4]['value']:.2f}" if m[4]['status']=='ok' else 'N/A'))
        if only_coin:
            lines.append('Quote time: '+ist(q.get('as_of') if q else None))
            lines.append('Daily close: '+ist(m[3].get('as_of')))
    if len(coins)>8:lines.append(f"\nShowing 8/{len(coins)}. /coin SYMBOL or /website for the rest.")
    lines+=['','Moves = last closed bar return, not live price change.','/technical COIN 1h | /metrics COIN | /website']
    if report.get('issues'):lines.append(f"{len(report['issues'])} data issues - /access")
    return '\n'.join(lines)


def technical_csv(report):
    buf=io.StringIO(newline='');writer=csv.writer(buf)
    writer.writerow(['scan_id','coin','timeframe','indicator','status','value','unit','source','as_of_IST','history_start_IST','bars','reading','method'])
    for coin in report['coins']:
        for tf,pack in coin.get('technical',{}).items():
            for row in pack['rows']:
                values=[report['scan_id'],coin['symbol'],tf,row['id'],row['status'],json.dumps(row['value'],allow_nan=False),row['unit'],row['source'],ist(row['as_of']),ist(pack['history_start']),pack['bars'],row['reading'],row['method']]
                writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    return '\ufeff'+buf.getvalue()


def markets_csv(report):
    rows=report.get('market_universe',{}).get('markets',[])
    fields=['id','exchange','category','symbol','base','quote','kind','status','last','price_unit',
            'change_24h','volume_24h','volume_unit','bid','ask','spread_bps','mark_price','index_price',
            'as_of','fetched_at','timestamp_kind','funding_rate','funding_interval_hours','funding_kind',
            'funding_as_of','funding_status','next_funding_at','open_interest','open_interest_unit',
            'open_interest_value','oi_value_unit','oi_as_of','oi_status','change_since_previous_snapshot',
            'comparison_seconds','tradingview_url']
    buf=io.StringIO(newline='');writer=csv.writer(buf);writer.writerow(fields)
    for row in rows:
        values=[row.get(k) for k in fields]
        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    return '\ufeff'+buf.getvalue()
