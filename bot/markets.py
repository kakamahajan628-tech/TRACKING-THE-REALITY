"""Bulk public market discovery. An instrument is a venue/product/symbol, not a coin.

No symbol-prefix stripping, USD pegs, funding-period assumptions or cross-venue OI sums.
All enabled active spot/perpetual listings are retained, including missing tickers.
"""
import asyncio
import copy
import math
import time
from collections import Counter
from datetime import datetime
from urllib.parse import quote
from .models import iso
from .providers import DataError


LANES = {'binance': ['spot','linear','inverse'], 'bybit': ['spot','linear','inverse'],
         'bitget': ['SPOT','USDT-FUTURES','COIN-FUTURES','USDC-FUTURES'], 'okx': ['SPOT','SWAP']}


def num(value, positive=False):
    try:
        if isinstance(value,bool):return None
        value=float(value)
        return value if math.isfinite(value) and (not positive or value>0) else None
    except (ValueError,TypeError):return None


def ms(value):
    value=num(value,True)
    if value is None:return None
    try:return iso(value/1000)
    except (ValueError,OverflowError,OSError):return None


def age(stamp, now=None):
    try:return (time.time() if now is None else now)-datetime.fromisoformat(stamp).timestamp()
    except (ValueError,TypeError):return float('inf')


def ratio(last, prior):
    a,b=num(last,True),num(prior,True)
    return a/b-1 if a is not None and b is not None else None


def chart_url(exchange, symbol, kind):
    # A search link avoids claiming that TradingView carries a native contract ID.
    query=f'{exchange.upper()}:{symbol}' + (' perpetual' if kind=='perpetual' else '')
    return 'https://www.tradingview.com/symbols/?query='+quote(query,safe='')


def instrument(exchange, category, symbol, base, quote_asset, **extra):
    if not all(isinstance(x,str) and x for x in (symbol,base,quote_asset)):
        raise DataError('Malformed instrument identity')
    kind='spot' if category.lower()=='spot' else 'perpetual'
    return dict(id=f'{exchange}:{category}:{symbol}',exchange=exchange,category=category,
                symbol=symbol,base=base,quote=quote_asset,kind=kind,
                tradingview_url=chart_url(exchange,symbol,kind),**extra)


def snapshot(m, *, stamp=None, last=None, change=None, bid=None, ask=None,
             volume=None, volume_unit=None, mark=None, index=None, **extra):
    last,bid,ask=num(last,True),num(bid,True),num(ask,True)
    spread=(ask-bid)/((ask+bid)/2)*10000 if bid and ask and ask>=bid else None
    return dict(m,last=last,price_unit=m['quote'],change_24h=num(change),
                volume_24h=num(volume),volume_unit=volume_unit,
                bid=bid,ask=ask,spread_bps=spread,mark_price=num(mark,True),index_price=num(index,True),
                as_of=stamp,fetched_at=iso(),timestamp_kind='provider snapshot',
                status='ok' if last and -30<=age(stamp)<=600 else 'stale' if last else 'unavailable',
                funding_rate=None,funding_interval_hours=m.get('funding_interval_hours'),funding_as_of=None,
                next_funding_at=None,funding_kind=None,open_interest=None,open_interest_unit=None,
                open_interest_value=None,oi_value_unit=None,oi_as_of=None,**extra)


class MarketUniverse:
    def __init__(self,http,settings):
        self.http,self.settings=http,settings
        self.catalogs={}
        self.previous={}
        self.cursors={}

    async def api(self,exchange,url,params=None,ttl=0):
        data=await self.http.get(url,params,ttl)
        if exchange=='binance':
            if isinstance(data,dict) and isinstance(data.get('code'),int) and data['code']<0:
                raise DataError('Binance rejected request')
        elif exchange=='bybit':
            if data.get('retCode')!=0:raise DataError('Bybit rejected request')
        elif str(data.get('code'))!=('00000' if exchange=='bitget' else '0'):
            raise DataError(f'{exchange} rejected request')
        return data

    async def catalog(self,exchange,category):
        key=exchange,category
        cached=self.catalogs.get(key)
        if cached and time.time()-cached[0]<3600:return cached[1],iso(cached[0])
        markets=[]
        if exchange=='binance':
            root,path=self.binance_root(category)
            raw=await self.api(exchange,root+path+'/exchangeInfo')
            for x in raw['symbols']:
                if x.get('status',x.get('contractStatus'))!='TRADING':continue
                if category!='spot' and x.get('contractType')!='PERPETUAL':continue
                if category=='spot' and x.get('isSpotTradingAllowed') is False:continue
                markets.append(instrument(exchange,category,x['symbol'],x['baseAsset'],x['quoteAsset'],
                                          settle=x.get('marginAsset'),contract_size=num(x.get('contractSize'))))
        elif exchange=='bybit':
            cursor='';seen=set()
            for _ in range(100):
                params={'category':category,'status':'Trading'}
                if category!='spot':params.update(limit=1000,cursor=cursor)
                raw=await self.api(exchange,'https://api.bybit.com/v5/market/instruments-info',params)
                for x in raw['result']['list']:
                    if x.get('status')!='Trading' or x.get('isPreListing'):continue
                    if category!='spot' and x.get('contractType') not in ('LinearPerpetual','InversePerpetual'):continue
                    if x.get('underlyingTicker') or x.get('marketRegion'):continue
                    interval=num(x.get('fundingInterval'),True)
                    markets.append(instrument(exchange,category,x['symbol'],x['baseCoin'],x['quoteCoin'],
                                              settle=x.get('settleCoin'),funding_interval_hours=interval/60 if interval else None))
                cursor=raw['result'].get('nextPageCursor','')
                if category=='spot' or not cursor:break
                if cursor in seen:raise DataError('Bybit repeated pagination cursor; catalog incomplete')
                seen.add(cursor)
            else:raise DataError('Bybit pagination safety limit; catalog incomplete')
        elif exchange=='bitget':
            raw=await self.api(exchange,'https://api.bitget.com/api/v3/market/instruments',{'category':category})
            for x in raw['data']:
                if x.get('status') not in ('online','limit_open','limit_close'):continue
                if x.get('symbolType') not in ('crypto',None,''):continue
                if str(x.get('isReality','')).lower()=='yes':continue
                if category!='SPOT' and x.get('type')!='perpetual':continue
                markets.append(instrument(exchange,category,x['symbol'],x['baseCoin'],x['quoteCoin'],
                                          trading_state=x['status'],funding_interval_hours=num(x.get('fundInterval'),True)))
        else:
            raw=await self.api(exchange,'https://www.okx.com/api/v5/public/instruments',{'instType':category})
            for x in raw['data']:
                if x.get('state')!='live':continue
                # SWAP baseCcy/quoteCcy may be empty; uly identifies the actual underlying.
                underlying=x.get('uly','').split('-')
                base=x.get('baseCcy') or (underlying[0] if len(underlying)==2 else '')
                quote_asset=x.get('quoteCcy') or (underlying[1] if len(underlying)==2 else '')
                markets.append(instrument(exchange,category,x['instId'],base,quote_asset,
                                          settle=x.get('settleCcy'),contract_type=x.get('ctType'),
                                          contract_size=num(x.get('ctVal')),contract_value_currency=x.get('ctValCcy')))
        unique={x['id']:x for x in markets}
        if len(unique)!=len(markets):raise DataError('Duplicate instruments in provider catalog')
        markets=list(unique.values())
        self.catalogs[key]=(time.time(),markets)
        return markets,iso(self.catalogs[key][0])

    @staticmethod
    def binance_root(category):
        return {'spot':('https://api.binance.com','/api/v3'),
                'linear':('https://fapi.binance.com','/fapi/v1'),
                'inverse':('https://dapi.binance.com','/dapi/v1')}[category]

    async def tickers(self,exchange,category,markets):
        if exchange=='binance':
            root,path=self.binance_root(category)
            data=await self.api(exchange,root+path+'/ticker/24hr')
            raw={x['symbol']:x for x in data}
        elif exchange=='bybit':
            data=await self.api(exchange,'https://api.bybit.com/v5/market/tickers',{'category':category})
            raw={x['symbol']:x for x in data['result']['list']}
        elif exchange=='bitget':
            data=await self.api(exchange,'https://api.bitget.com/api/v3/market/tickers',{'category':category})
            raw={x['symbol']:x for x in data['data']}
        else:
            data=await self.api(exchange,'https://www.okx.com/api/v5/market/tickers',{'instType':category})
            raw={x['instId']:x for x in data['data']}
        rows=[]
        for m in markets:
            x=raw.get(m['symbol'])
            if not x:
                rows.append(snapshot(m,note='Active catalog listing has no ticker in this response'))
                continue
            if exchange=='binance':
                pct=num(x.get('priceChangePercent'))
                row=snapshot(m,stamp=ms(x.get('closeTime')),last=x.get('lastPrice'),change=pct/100 if pct is not None else None,
                             bid=x.get('bidPrice'),ask=x.get('askPrice'),volume=x.get('baseVolume') if category=='inverse' else x.get('quoteVolume'),
                             volume_unit=m['base'] if category=='inverse' else m['quote'])
            elif exchange in ('bybit','bitget'):
                row=snapshot(m,stamp=ms(x.get('ts') if exchange=='bitget' else data.get('time')),last=x.get('lastPrice'),
                             change=num(x.get('price24hPcnt')),bid=x.get('bid1Price'),ask=x.get('ask1Price'),
                             volume=x.get('turnover24h'),volume_unit=m['base'] if exchange=='bybit' and category=='inverse' else m['quote'],
                             mark=x.get('markPrice'),index=x.get('indexPrice'))
                if m['kind']=='perpetual':
                    interval=num(x.get('fundingIntervalHour'),True) or m.get('funding_interval_hours')
                    row.update(funding_rate=num(x.get('fundingRate')),funding_as_of=row['as_of'],
                               funding_kind='published current rate; not a realized payment',funding_interval_hours=interval,
                               next_funding_at=ms(x.get('nextFundingTime')),open_interest=num(x.get('openInterest')),
                               open_interest_unit=(m['quote'] if category=='inverse' else m['base']) if exchange=='bybit' else 'provider native units (unconverted)',
                               open_interest_value=num(x.get('openInterestValue')) if exchange=='bybit' else None,
                               oi_value_unit=(m['base'] if category=='inverse' else m['quote']) if exchange=='bybit' else None,oi_as_of=row['as_of'])
            else:
                row=snapshot(m,stamp=ms(x.get('ts')),last=x.get('last'),change=ratio(x.get('last'),x.get('open24h')),
                             bid=x.get('bidPx'),ask=x.get('askPx'),volume=x.get('volCcy24h'),
                             volume_unit=m['base'] if category=='SWAP' else m['quote'])
            rows.append(row)
        return rows

    def batch(self,key,rows):
        ordered=sorted((r for r in rows if r['kind']=='perpetual'),key=lambda x:x['id'])
        if not ordered:return []
        start=self.cursors.get(key,0)%len(ordered)
        count=min(len(ordered),self.settings.market_detail_batch)
        self.cursors[key]=(start+count)%len(ordered)
        return [ordered[(start+i)%len(ordered)] for i in range(count)]

    async def derivatives(self,exchange,category,rows):
        issues=[]
        if category.lower()=='spot' or not rows:return issues
        by_symbol={r['symbol']:r for r in rows}
        async def attempt(label,work):
            try:await work()
            except (DataError,ValueError,KeyError,TypeError,IndexError):issues.append(label+' unavailable')
        if exchange=='binance':
            root,path=self.binance_root(category)
            async def premium():
                raw=await self.api(exchange,root+path+'/premiumIndex')
                for x in raw:
                    row=by_symbol.get(x['symbol'])
                    if row is None:continue
                    row.update(mark_price=num(x.get('markPrice'),True),index_price=num(x.get('indexPrice'),True),
                               funding_rate=num(x.get('lastFundingRate')),funding_as_of=ms(x.get('time')),
                               funding_kind='provider lastFundingRate',next_funding_at=ms(x.get('nextFundingTime')))
            await attempt('Bulk mark/funding',premium)
            for row in self.batch((exchange,category),rows):
                async def oi():
                    x=await self.api(exchange,root+path+'/openInterest',{'symbol':row['symbol']})
                    row.update(open_interest=num(x.get('openInterest')),open_interest_unit='contracts' if category=='inverse' else row['base'],oi_as_of=ms(x.get('time')))
                    if category=='inverse' and row.get('contract_size') and row['open_interest'] is not None:
                        row.update(open_interest_value=row['open_interest']*row['contract_size'],oi_value_unit=row['quote'])
                await attempt('Rotating open interest '+row['symbol'],oi)
        elif exchange=='okx':
            async def oi():
                raw=await self.api(exchange,'https://www.okx.com/api/v5/public/open-interest',{'instType':'SWAP'})
                for x in raw['data']:
                    row=by_symbol.get(x['instId'])
                    if row is not None:row.update(open_interest=num(x.get('oi')),open_interest_unit='contracts',
                        open_interest_value=num(x.get('oiUsd')),oi_value_unit='USD',oi_as_of=ms(x.get('ts')))
            await attempt('Bulk open interest',oi)
            for row in self.batch((exchange,category),rows):
                async def funding():
                    raw=await self.api(exchange,'https://www.okx.com/api/v5/public/funding-rate',{'instId':row['symbol']})
                    x=raw['data'][0]
                    row.update(funding_rate=num(x.get('fundingRate')),funding_as_of=ms(x.get('ts')),
                               funding_kind='current period forecast; not settled',next_funding_at=ms(x.get('fundingTime')))
                    # Do not infer a funding interval from a forecast of a different period.
                await attempt('Rotating funding '+row['symbol'],funding)
        return issues

    async def lane(self,exchange,category):
        markets,catalog_at=await self.catalog(exchange,category)
        rows=await self.tickers(exchange,category,markets)
        issues=await self.derivatives(exchange,category,rows)
        now=time.time();old={r['id']:r for r in self.previous.get((exchange,category),{}).get('markets',[])}
        for row in rows:
            prior=old.get(row['id'])
            # Retain slow rotating observations with their original timestamps, never refresh the date.
            if prior and row['kind']=='perpetual':
                for field,keys in [('funding_rate',['funding_rate','funding_as_of','funding_kind','next_funding_at','funding_interval_hours']),
                                   ('open_interest',['open_interest','open_interest_unit','open_interest_value','oi_value_unit','oi_as_of'])]:
                    if row[field] is None and age(prior.get('funding_as_of' if field=='funding_rate' else 'oi_as_of'),now)<86400:
                        for k in keys:row[k]=prior.get(k)
            row['funding_status']='ok' if row.get('funding_rate') is not None and -30<=age(row.get('funding_as_of'),now)<=600 else 'stale' if row.get('funding_rate') is not None else 'unavailable'
            row['oi_status']='ok' if row.get('open_interest') is not None and -30<=age(row.get('oi_as_of'),now)<=600 else 'stale' if row.get('open_interest') is not None else 'unavailable'
            row['change_since_previous_snapshot']=None
            row['comparison_seconds']=None
            if prior and prior.get('status')=='ok' and row['status']=='ok':
                elapsed=age(prior['as_of'],now)-age(row['as_of'],now)
                if 0<elapsed<=self.settings.interval*2:
                    row['change_since_previous_snapshot']=ratio(row['last'],prior['last'])
                    row['comparison_seconds']=round(elapsed,2)
        current=sum(r['status']=='ok' for r in rows)
        if current<len(rows):issues.append(f'{len(rows)-current} active listings have missing or stale ticker observations')
        result=dict(exchange=exchange,category=category,status='partial' if issues else 'ok',catalog_as_of=catalog_at,
                    discovered=len(rows),current=current,issues=issues,markets=rows)
        self.previous[exchange,category]=result
        return result

    async def scan(self):
        keys=[(e,c) for e in self.settings.market_exchanges for c in LANES[e]
              if ('spot' if c.lower()=='spot' else 'perpetual') in self.settings.market_types]
        async def bounded(key):
            try:
                async with asyncio.timeout(120):return await self.lane(*key)
            except (TimeoutError,DataError,ValueError,KeyError,TypeError,IndexError) as exc:
                result=copy.deepcopy(self.previous.get(key,dict(exchange=key[0],category=key[1],markets=[],discovered=0,catalog_as_of=None)))
                result.update(status='unavailable',current=0,issues=[str(exc)[:150] if isinstance(exc,DataError) else type(exc).__name__+'; market collection incomplete'])
                for row in result['markets']:
                    row['status']='stale'
                    row['funding_status']='stale' if row.get('funding_rate') is not None else 'unavailable'
                    row['oi_status']='stale' if row.get('open_interest') is not None else 'unavailable'
                    row['change_since_previous_snapshot']=None;row['comparison_seconds']=None
                return result
        lanes=await asyncio.gather(*(bounded(k) for k in keys))
        rows=sorted([r for lane in lanes for r in lane['markets']],key=lambda x:x['id'])
        current=[r for r in rows if r['status']=='ok']
        return dict(enabled=True,generated_at=iso(),scope='Active spot and perpetual instruments on configured exchanges; catalogs refresh hourly',
                    lanes=[{k:v for k,v in lane.items() if k!='markets'} for lane in lanes],markets=rows,
                    counts=dict(instruments=len(rows),current=len(current),spot=sum(r['kind']=='spot' for r in rows),
                                perpetual=sum(r['kind']=='perpetual' for r in rows),
                                base_symbols=len({r['base'] for r in rows}),alt_base_symbols=len({r['base'] for r in rows if r['base']!='BTC'})),
                    complete=all(l['status']=='ok' and l['current']==l['discovered'] for l in lanes),
                    notes=['Base-symbol count is not a verified unique-token count. Venue identities and contract multipliers stay separate.',
                           'All discovered listings are retained; no top-N filter. TradFi exclusions use provider metadata where supplied.',
                           'Bulk screening is not the 136-metric watchlist calculation. Exchange access failures leave coverage incomplete.',
                           'Binance OI and OKX funding rotate through a bounded batch. Old observations keep their timestamp and stale status.',
                           'Volume and OI units are native; USDT and USDC are not relabelled USD. Funding rates are not normalized to 8h.',
                           'TradingView links open symbol search; no TradingView price/indicator API is connected.'])


def market_digest(universe):
    if not universe or not universe.get('enabled'):return 'All-market scan disabled.'
    c=universe['counts'];rows=universe['markets']
    lines=[f"ALL-MARKET SCAN · {c['current']}/{c['instruments']} current instruments",
           f"Spot {c['spot']} · Perpetual {c['perpetual']} · {c['alt_base_symbols']} altcoin ticker labels",
           'Coverage: '+('complete for configured catalogs' if universe['complete'] else 'PARTIAL — see provider issues')]
    eligible=[r for r in rows if r['status']=='ok' and r.get('change_24h') is not None and r['base']!='BTC']
    lines.append('Largest absolute 24h moves (includes thin markets; not entry signals):')
    for r in sorted(eligible,key=lambda x:abs(x['change_24h']),reverse=True)[:5]:
        lines.append(f"{r['exchange']} {r['symbol']} {r['kind']} · {r['change_24h']*100:+.2f}%")
    lines.append('Full spot/perpetual list in markets.csv; /markets for browsing. 136-metric research covers the configured watchlist.')
    return '\n'.join(lines)
