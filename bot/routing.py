"""Read-only USDT instrument routing; no bulk ticker scans or trading API keys."""
import asyncio
import copy
import math
import time
from collections import defaultdict
import ccxt.async_support as ccxt
import pandas as pd
from .models import iso
from .providers import DataError, validated_candles
from .tracking import EXCHANGES, TIMEFRAMES

CG_IDS={'okx':'okex_swap','bybit':'bybit','bitget':'bitget_futures','gate':'gate_futures',
        'mexc':'mxc_futures','kucoin':'kucoin_futures','bingx':'bingx_futures',
        'htx':'huobi_dm','phemex':'phemex','coinex':'coinex_futures'}


def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None


def eligible(market,kind):
    return (market.get('active') is not False and market.get('quote')=='USDT'
            and (market.get('spot') is True if kind=='spot' else
                 market.get('swap') is True and market.get('linear') is True and market.get('settle')=='USDT'))


class ExchangeRouter:
    def __init__(self,settings,http):
        self.settings,self.http=settings,http
        self.clients={};self.catalog_times={};self.catalog_failed={};self.catalog_locks=defaultdict(asyncio.Lock)
        self.locks=defaultdict(asyncio.Lock);self.gate=asyncio.Semaphore(5)
        self.frames={};self.fetch_times={};self.selected={};self.health={};self.blocked={}
        self.priority=list(EXCHANGES);self.rank_time=0
        self.ranking={'source':'Configured fallback priority; not a live quality ranking','as_of':None,'order':self.priority}
        self.calls=0

    def client(self,venue,kind):
        adapter='kucoinfutures' if venue=='kucoin' and kind=='perpetual' else venue
        if adapter not in self.clients:
            client=getattr(ccxt,adapter)({'enableRateLimit':True,'timeout':10000,
                                        'options':{'defaultType':'swap' if kind=='perpetual' else 'spot'}})
            client.has['fetchCurrencies']=False
            self.clients[adapter]=client
        return self.clients[adapter]

    async def call(self,venue,client,method,*args,**kwargs):
        if self.blocked.get(client.id,0)>time.time():raise DataError(f'{venue}: access cooldown; source not used')
        try:
            async with self.gate:
                self.calls+=1
                value=await asyncio.wait_for(getattr(client,method)(*args,**kwargs),25)
            self.health[venue]={'status':'ok','checked_at':iso(),'reason':method+' responded'}
            return value
        except asyncio.CancelledError:raise
        except Exception as exc:
            # Error bodies/URLs may contain gateway tokens; expose only the class.
            reason=type(exc).__name__
            if isinstance(exc,(ccxt.PermissionDenied,ccxt.AuthenticationError,ccxt.DDoSProtection)):
                self.blocked[client.id]=time.time()+3600
            elif isinstance(exc,ccxt.RateLimitExceeded):self.blocked[client.id]=time.time()+60
            self.health[venue]={'status':'unavailable','checked_at':iso(),'reason':method+': '+reason}
            raise DataError(f'{venue}: {method} {reason}') from None

    async def catalog(self,venue,kind):
        client=self.client(venue,kind)
        async with self.catalog_locks[client.id]:
            if time.time()-self.catalog_times.get(client.id,0)>21600:
                if time.time()-self.catalog_failed.get(client.id,0)<300:raise DataError(venue+': catalog unavailable; retry next scan')
                try:await self.call(venue,client,'load_markets',True)
                except DataError:
                    self.catalog_failed[client.id]=time.time();raise
                # Metadata is for resolving selected symbols; no prices for the universe are fetched.
                kept=[m for m in client.markets.values() if eligible(m,'spot') or eligible(m,'perpetual')]
                client.set_markets(kept)
                self.catalog_times[client.id]=time.time()
        return client

    async def prepare(self):
        key=getattr(self.settings,'coingecko_api_key','')
        if time.time()-self.rank_time<21600:return
        self.rank_time=time.time()
        if key:
            try:
                rows=await self.http.get('https://api.coingecko.com/api/v3/derivatives/exchanges',
                    {'order':'open_interest_btc_desc','per_page':100,'page':1},ttl=21600,headers={'x-cg-demo-api-key':key})
                ranks={r['id']:i for i,r in enumerate(rows)}
                self.priority=sorted(EXCHANGES,key=lambda x:ranks.get(CG_IDS[x],1000+EXCHANGES.index(x)))
                self.ranking={'source':'CoinGecko derivatives open-interest ranking; not an accuracy score',
                              'as_of':iso(),'order':list(self.priority)}
            except (DataError,TypeError,KeyError):pass

    async def candidates(self,coin,preference):
        venues=[preference['exchange']] if preference.get('exchange','auto')!='auto' else self.priority
        kinds=[preference['market']] if preference.get('market','auto')!='auto' else ['perpetual','spot']
        for kind in kinds:
            async def lookup(venue):
                try:
                    client=await self.catalog(venue,kind)
                    matches=[m for m in client.markets.values() if m.get('base')==coin and eligible(m,kind)]
                    if len(matches)!=1:return None
                    m=matches[0]
                    return {'exchange':venue,'market':kind,'symbol':m['symbol'],'id':m['id'],
                            'base':m['base'],'quote':'USDT','settle':m.get('settle'),
                            'contract_size':number(m.get('contractSize')),'adapter':client.id}
                except DataError:return None
            results=await asyncio.gather(*(lookup(v) for v in venues))
            for row in results:
                if row:yield row

    async def candles(self,route,tf,now=None):
        now=time.time() if now is None else now;period=TIMEFRAMES[tf]
        key=(route['adapter'],route['symbol'],tf);boundary=int(now)//period*period
        async with self.locks[key]:
            cached=self.frames.get(key)
            if cached is not None and int(cached.index[-1])+period==boundary:return cached.copy()
            client=self.clients[route['adapter']]
            if tf not in client.timeframes:raise DataError(f"{route['exchange']}: {tf} candles not supported")
            count=max(260,self.settings.window+1) if tf=='1d' else 260
            since=int((boundary-(count+4)*period)*1000)
            all_rows={}
            if cached is not None:
                all_rows={int(t):dict(time=int(t),**r.to_dict()) for t,r in cached.iterrows()}
                since=int(cached.index[-1]*1000)
            source=f"{route['exchange']} / {route['market']} / USDT"
            if route['exchange']=='htx' and tf=='1d':
                # HTX native daily sessions are UTC+8. Build UTC daily bars from
                # six complete 4h candles; never shift a native daily timestamp.
                raw=await self.call('htx',client,'fetch_ohlcv',route['symbol'],'4h',None,min(2000,(count+2)*6),
                                    {'useHistoricalEndpointForSpot':False})
                sub=validated_candles([dict(time=int(t)//1000,open=o,high=h,low=l,close=c,volume=v) for t,o,h,l,c,v,*_ in raw],source,14400,now)
                grouped=[]
                for day,group in sub.groupby(sub.index//86400*86400):
                    if len(group)!=6 or int(group.index[0])!=day or day+86400>now:continue
                    grouped.append(dict(time=int(day),open=group.open.iloc[0],high=group.high.max(),low=group.low.min(),close=group.close.iloc[-1],volume=group.volume.sum()))
                frame=validated_candles(grouped,source,86400,now).tail(count)
                frame.attrs.update(symbol=route['symbol'],exchange='htx',market=route['market'],quote='USDT',volume_unit=route['base'],
                    contract_size=route['contract_size'],derivation='UTC daily OHLCV aggregated from six complete native 4h bars; differs from HTX UTC+8 daily chart.')
                self.frames[key]=frame
                return frame.copy()
            for _ in range(3):
                latest_only=route['exchange']=='htx' or (route['exchange']=='phemex' and route['market']=='spot')
                if route['exchange']=='coinex':
                    # Native v2 fields: volume is base units; value is quote turnover.
                    method='v2PublicGetFuturesKline' if route['market']=='perpetual' else 'v2PublicGetSpotKline'
                    response=await self.call('coinex',client,method,{'market':route['id'],'period':client.timeframes[tf],'limit':count+4})
                    if response.get('code')!=0:raise DataError('CoinEx candle request rejected')
                    raw=[[r['created_at'],number(r['open']),number(r['high']),number(r['low']),number(r['close']),number(r['volume'])] for r in response.get('data',[])]
                    latest_only=True
                else:
                    limit=1000 if route['exchange']=='phemex' and latest_only else count+4 if latest_only else 200
                    raw=await self.call(route['exchange'],client,'fetch_ohlcv',route['symbol'],tf,None if latest_only else since,limit)
                if not raw:break
                largest=since
                for row in raw:
                    if len(row)<6:continue
                    stamp=int(row[0]);largest=max(largest,stamp)
                    volume=number(row[5])
                    if route['market']=='perpetual' and route['exchange'] in ('gate','mexc','kucoin'):
                        size=route['contract_size']
                        if not size or size<=0:raise DataError('Contract volume size unavailable; calculation withheld')
                        volume=volume*size if volume is not None else None
                    all_rows[stamp//1000]=dict(time=stamp//1000,open=row[1],high=row[2],low=row[3],close=row[4],volume=volume)
                if latest_only or largest>=boundary*1000 or largest<since:break
                next_since=largest+period*1000
                if next_since<=since:break
                since=next_since
            frame=validated_candles(list(all_rows.values()),source,period,now).tail(count)
            frame.attrs.update(symbol=route['symbol'],exchange=route['exchange'],market=route['market'],
                               quote='USDT',volume_unit=route['base'],contract_size=route['contract_size'])
            self.frames[key]=frame
            if len(self.frames)>600:
                self.frames.pop(next(iter(self.frames)))
            return frame.copy()

    async def book(self,route):
        client=self.clients[route['adapter']]
        row=await self.call(route['exchange'],client,'fetch_order_book',route['symbol'],100)
        size=route['contract_size'] if route['market']=='perpetual' else 1
        if size is None or size<=0:raise DataError('Order-book contract size is missing')
        sides={}
        for side in ('bids','asks'):
            sides[side]=[[float(p),float(q)*size] for p,q,*_ in row.get(side,[]) if number(p) and number(q) and float(q)>0]
            if not sides[side]:raise DataError('Empty order book')
        if sides['bids'][0][0]>=sides['asks'][0][0]:raise DataError('Crossed or locked order book')
        stamp=number(row.get('timestamp'))
        return {**sides,'source':route['exchange']+' '+route['symbol'],'as_of':iso(stamp/1000 if stamp else time.time()),'quote':'USDT'}

    async def resolve(self,coin,preference=None):
        preference=preference or {};previous=self.selected.get(coin)
        issues=[]
        # Keep the selected eligible perpetual stable. An automatic spot fallback is
        # rechecked for newly accessible perpetuals rather than permanently pinned.
        if previous and previous['market']=='perpetual' and preference.get('exchange','auto') in ('auto',previous['exchange']) and preference.get('market','auto') in ('auto',previous['market']):
            try:
                frame=await self.candles(previous,'5m')
                if time.time()-(int(frame.index[-1])+300)<=420:return previous,frame,issues
            except DataError as exc:issues.append(str(exc))
        async for route in self.candidates(coin,preference):
            try:
                frame=await self.candles(route,'5m')
                if time.time()-(int(frame.index[-1])+300)>420:raise DataError('Latest 5m candle is stale')
                route['selected_at']=iso()
                if previous and (route['exchange'],route['symbol'])!=(previous['exchange'],previous['symbol']):
                    route['change_note']=f"Source changed from {previous['exchange']} {previous['symbol']}; histories were not joined"
                self.selected[coin]=route
                return route,frame,issues
            except DataError as exc:issues.append(str(exc))
        raise DataError('No accessible exact-symbol USDT instrument with fresh closed candles. '+('; '.join(issues[-3:]) or 'Try /route with a supported venue; multiplied tokens such as 1000PEPE require their exact symbol.'))

    async def quote(self,route):
        client=self.clients[route['adapter']]
        row=await self.call(route['exchange'],client,'fetch_ticker',route['symbol'])
        price=number(row.get('last'));stamp=number(row.get('timestamp'))
        if price is None or price<=0:raise DataError('Ticker has no valid last trade price')
        now=time.time()
        return {'price':price,'unit':'USDT','as_of':iso(stamp/1000 if stamp else now),'fetched_at':iso(now),
                'timestamp_kind':'exchange' if stamp else 'retrieved; exchange timestamp absent',
                'source':route['exchange']+' '+route['symbol'],'status':'ok' if not stamp or 0<=now-stamp/1000<=180 else 'stale',
                'age_seconds':max(0,now-stamp/1000) if stamp else 0}

    async def derivatives(self,route):
        result={'market':route['market'],'source':route['exchange']+' '+route['symbol'],
                'funding':None,'open_interest':None,'funding_history':[],'oi_history':[], 'issues':[]}
        if route['market']!='perpetual':
            result['issues']=['Spot instrument: funding and perpetual OI do not apply.'];return result
        client=self.clients[route['adapter']];now=time.time()
        async def fetch(name,*args):
            if name=='fetchOpenInterestHistory' and route['exchange']=='okx':
                result['issues'].append('OKX unified OI history is currency-wide, not this contract; daily contract OI withheld.')
                return None
            if not client.has.get(name):return None
            try:return await self.call(route['exchange'],client,name,*args)
            except DataError as exc:result['issues'].append(str(exc));return None
        oi_args=(route['symbol'],{'interval':'5m'}) if route['exchange']=='bybit' else (route['symbol'],)
        funding,oi=await asyncio.gather(fetch('fetchFundingRate',route['symbol']),fetch('fetchOpenInterest',*oi_args))
        if funding:
            value=number(funding.get('fundingRate'))
            if value is not None:
                result['funding']={'value':value,'unit':'fraction / exchange funding interval',
                    'as_of':iso(number(funding.get('timestamp'))/1000) if number(funding.get('timestamp')) else iso(now),
                    'timestamp_kind':'exchange' if funding.get('timestamp') else 'retrieval',
                    'interval':funding.get('interval'),'funding_at':iso(funding['fundingTimestamp']/1000) if funding.get('fundingTimestamp') else None,
                    'next_funding_at':iso(funding['nextFundingTimestamp']/1000) if funding.get('nextFundingTimestamp') else None,
                    'kind':'Last reported funding rate from BingX; not a predicted rate or candle return' if route['exchange']=='bingx' else 'Exchange current/indicated rate; not a settled 5m/1h/4h return'}
        def normalize_oi(row):
            if not row:return None
            amount=number(row.get('openInterestAmount'));value=number(row.get('openInterestValue'));venue=route['exchange']
            # Preserve actual native units. Do not relabel venue USD values as USDT.
            if amount is not None:
                unit='contracts' if venue in ('okx','gate','kucoin','htx') else route['base'] if venue in ('bybit','bitget','phemex') else 'exchange-native amount'
                observation={'value':amount,'unit':unit}
            elif value is not None:
                observation={'value':value,'unit':'USDT' if venue=='bingx' else 'USD (vendor value; not converted)'}
            else:return None
            stamp=number(row.get('timestamp'))
            observation.update(as_of=iso(stamp/1000 if stamp else now),timestamp_kind='exchange' if stamp else 'retrieval')
            return observation
        result['open_interest']=normalize_oi(oi)
        # These are native historical observations, fetched hourly, not generated
        # by multiplying a current rate or treating today's OI as yesterday's.
        history_key=(route['adapter'],route['symbol'],'derivative-history')
        if now-self.fetch_times.get(history_key,0)>3600:
            rates,interests=await asyncio.gather(fetch('fetchFundingRateHistory',route['symbol'],int((now-3*86400)*1000),100),
                fetch('fetchOpenInterestHistory',route['symbol'],'1h',int((now-3*86400)*1000),100))
            histories={'funding_history':[], 'oi_history':[]}
            for row in rates or []:
                val=number(row.get('fundingRate'));stamp=number(row.get('timestamp'))
                if val is not None and stamp and stamp/1000<=now:histories['funding_history'].append({'value':val,'as_of':iso(stamp/1000),'timestamp':stamp/1000})
            for row in interests or []:
                normalized=normalize_oi(row)
                if normalized and number(row.get('timestamp')) and row['timestamp']/1000<=now:histories['oi_history'].append(normalized)
            self.frames[history_key]=histories;self.fetch_times[history_key]=now
        result.update(copy.deepcopy(self.frames.get(history_key,{})))
        return result

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients.values()),return_exceptions=True)
