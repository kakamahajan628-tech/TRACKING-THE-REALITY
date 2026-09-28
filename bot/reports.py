import csv
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from .markets import market_digest
from .technical import compact

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
            .replace('/*APP*/',(ROOT/'web/app.js').read_text(encoding='utf-8'))
            .replace('/*MARKETS*/',(ROOT/'web/markets.js').read_text(encoding='utf-8'))
            .replace('/*TECHNICAL*/',(ROOT/'web/technical.js').read_text(encoding='utf-8'))
            .replace('/*CONTROLS*/',(ROOT/'web/control.js').read_text(encoding='utf-8')))


def csv_report(report):
    buf=io.StringIO(newline='')
    writer=csv.writer(buf)
    writer.writerow(['scan_id','coin','metric_id','name','status','value','unit','source','as_of','frequency','sample','note'])
    names={m['id']:m['name'] for m in report['catalog']}
    def safe(value):
        if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):value="'"+value
        return value
    for coin in report['coins']:
        for m in coin['metrics']:
            writer.writerow([safe(x) for x in [report['scan_id'],coin['symbol'],m['id'],names[m['id']],m['status'],m['value'],m['unit'],m['source'],m['as_of'],m['frequency'],m['sample'],m['note']]])
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
        z.writestr('READ-ME.txt','Open report.html in a browser. metrics.csv contains 136 records per research-watchlist coin.\ntechnical.csv contains five additional studies on 5m and daily closed spot candles per watchlist coin.\nmarkets.csv contains all discovered exchange spot/perpetual snapshots, NOT 136 metrics per instrument.\naggregators.json contains separately sourced CMC/CoinGlass context when configured.\nMissing is not zero. Source timestamps differ. Scenarios and models are not market observations.\n'+report['mode'])
    return buf.getvalue()


def summary(report,only_coin=None):
    title=f"ATLAS 136 | {report['scan_boundary'][:16].replace('T',' ')} UTC"
    lines=[title,report['mode']]
    if not only_coin and report.get('market_universe',{}).get('enabled'):
        lines.extend([market_digest(report['market_universe']),''])
    lines.extend([f"DEEP RESEARCH: {len(report['coins'])} watchlist coins · timer {report.get('interval_seconds',300)//60} min · {report['lookback_days']}d window",''])
    for coin in report['coins']:
        if only_coin and coin['symbol']!=only_coin:continue
        q=coin['quote']; metrics={m['id']:m for m in coin['metrics']}
        price=(f"${q['price']:,.2f}" if q['price']>=10 else f"${q['price']:,.4f}") if q else 'price N/A'
        if q and q['status']=='stale':price+=' [STALE]'
        daily=metrics[34]
        move=f"{daily['value']*100:+.2f}% 1d" if daily['status']=='ok' else '1d N/A'
        beta=metrics[3]
        exposure=f"β {beta['value']:.2f}" if beta['status']=='ok' else 'β N/A'
        lines.append(f"{coin['symbol']}  {price} | {move} | {exposure} | {coin['coverage'].get('ok',0)}/136 current")
        tech=coin.get('technical',{}).get('5m')
        if tech:
            lines.append('  TA 5m: '+' · '.join(f"{m['id'].upper()} {compact(m) if m['status']=='ok' else m['status'].upper()}" for m in tech['rows']))
    lines+=['','Current = available observations/calculations, not accuracy.','Models, scenarios, imported and missing data are separate in the full report.',
            'Sources and timestamps: Metrics / Open dashboard; /report downloads the complete ZIP.','No trading orders or guaranteed directional signals.']
    if report['issues']:lines.append(f"Feed issues: {len(report['issues'])}; details in report.")
    return '\n'.join(lines)


def technical_csv(report):
    buf=io.StringIO(newline='');writer=csv.writer(buf)
    writer.writerow(['scan_id','coin','timeframe','indicator','status','value','unit','source','as_of','history_start','bars','reading','method'])
    for coin in report['coins']:
        for tf,pack in coin.get('technical',{}).items():
            for row in pack['rows']:
                values=[report['scan_id'],coin['symbol'],tf,row['id'],row['status'],json.dumps(row['value'],allow_nan=False),row['unit'],pack['source'],pack['as_of'],pack['history_start'],pack['bars'],row['reading'],row['method']]
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
