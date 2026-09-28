"""Public read-only adapters. A restricted service is disabled, never proxied.

No secret is included in error text; retry budgets and host throttles are shared.
"""
import asyncio
import json
import hashlib
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
import numpy as np
import pandas as pd
import websockets
from .models import iso


class DataError(Exception):
    pass


class PublicHTTP:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=12, headers={'User-Agent':'Atlas136/1.0 research contact: local operator'}, follow_redirects=False)
        self.locks = defaultdict(asyncio.Lock)
        self.last = defaultdict(float)
        self.blocked = {}
        self.cache = {}
        self.health = {}

    async def get(self, url, params=None, ttl=0, headers=None):
        host=urlsplit(url).hostname
        # Credentials stay in headers, never cache keys, report payloads or exception text.
        scope=hashlib.sha256(json.dumps(headers or {},sort_keys=True).encode()).hexdigest()
        key=(url,tuple(sorted((params or {}).items())),scope)
        now=time.time()
        if key in self.cache and now-self.cache[key][0]<ttl:
            return self.cache[key][1]
        if self.blocked.get(host,0)>now:
            raise DataError(f'{host}: access blocked; cooling down for one hour')
        error='network error'
        for attempt in range(3):
            try:
                async with self.locks[host]:
                    if self.blocked.get(host,0)>time.time():
                        raise DataError(f'{host}: access blocked; cooling down for one hour')
                    await asyncio.sleep(max(0,1.05-(time.monotonic()-self.last[host])))
                    self.last[host]=time.monotonic()
                    response=await self.client.get(url,params=params,headers=headers)
                if response.status_code in (401,403,418,451):
                    self.blocked[host]=time.time()+3600
                    raise DataError(f'{host}: HTTP {response.status_code}; entitlement or regional access restriction')
                if response.status_code==429:
                    try: wait=min(max(float(response.headers.get('Retry-After','5')),1),30)
                    except ValueError: wait=5
                    await asyncio.sleep(wait)
                    error='rate limited (429)'
                    continue
                if response.status_code>=500:
                    error=f'HTTP {response.status_code}'
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code!=200:
                    raise DataError(f'{host}: HTTP {response.status_code}; unsupported request')
                data=response.json()
                if isinstance(data,dict) and data.get('error'):
                    raise DataError(f'{host}: provider rejected request')
                if len(self.cache)>=1000:
                    self.cache.pop(next(iter(self.cache)))
                if ttl>0:self.cache[key]=(time.time(),data)
                self.health[host]={'status':'ok','checked_at':iso()}
                return data
            except (httpx.HTTPError,ValueError):
                error='timeout/network/invalid JSON'
                await asyncio.sleep(2**attempt)
            except DataError as exc:
                self.health[host]={'status':'unavailable','checked_at':iso(),'reason':str(exc)}
                raise
        self.health[host]={'status':'unavailable','checked_at':iso(),'reason':error}
        raise DataError(f'{host}: {error} after bounded retries')

    async def close(self):
        await self.client.aclose()


def validated_candles(rows, source, period, now=None):
    now=time.time() if now is None else now
    frame=pd.DataFrame(rows)
    if frame.empty: raise DataError('No candle observations')
    frame=frame.set_index('time').sort_index()
    if frame.index.has_duplicates: raise DataError('Duplicate candle timestamps')
    frame=frame.loc[frame.index+period <= now]
    if frame.empty: raise DataError('No closed candles')
    fields=['open','high','low','close','volume']
    if not np.isfinite(frame[fields].to_numpy()).all(): raise DataError('Non-finite candle data')
    if (frame[['open','high','low','close']]<=0).any().any() or (frame.volume<0).any(): raise DataError('Invalid candle prices/volume')
    if (frame.high<frame[['open','close','low']].max(axis=1)).any() or (frame.low>frame[['open','close','high']].min(axis=1)).any():
        raise DataError('Invalid OHLC price bounds')
    # Retain only the latest contiguous run. Missing observations must not turn multi-day returns into daily returns.
    breaks=np.where(np.diff(frame.index.to_numpy()) != period)[0]
    if len(breaks):frame=frame.iloc[int(breaks[-1])+1:]
    if (frame.index % period !=0).any(): raise DataError('Candles not aligned to UTC boundaries')
    frame.attrs={'source':source,'period':period,'fetched_at':iso(now)}
    return frame


def pair(symbol):
    return {'BTC':'XBT','DOGE':'XDG'}.get(symbol,symbol)+'USD'


class Spot:
    def __init__(self,http): self.http=http

    async def candles(self,symbol,period=86400):
        ttl=900 if period==86400 else 45
        try:
            result=(await self.http.get('https://api.kraken.com/0/public/OHLC',{'pair':pair(symbol),'interval':period//60},ttl))['result']
            raw=next(v for k,v in result.items() if k!='last')
            # Last Kraken row is ALWAYS uncommitted, even if local clock says otherwise.
            rows=[dict(time=int(x[0]),open=float(x[1]),high=float(x[2]),low=float(x[3]),close=float(x[4]),
                       volume=float(x[6]),quote_volume=float(x[5])*float(x[6])) for x in raw[:-1]]
            return validated_candles(rows,'Kraken spot USD',period)
        except (DataError,KeyError,ValueError,StopIteration):
            raw=await self.http.get(f'https://api.exchange.coinbase.com/products/{symbol}-USD/candles',{'granularity':period},ttl)
            rows=[dict(time=int(x[0]),low=float(x[1]),high=float(x[2]),open=float(x[3]),close=float(x[4]),volume=float(x[5])) for x in raw]
            return validated_candles(rows,'Coinbase spot USD',period)

    async def book(self,symbol):
        data=(await self.http.get('https://api.kraken.com/0/public/Depth',{'pair':pair(symbol),'count':500},20))['result']
        raw=next(iter(data.values()))
        bids=[(float(x[0]),float(x[1])) for x in raw['bids']]
        asks=[(float(x[0]),float(x[1])) for x in raw['asks']]
        if not bids or not asks or bids[0][0]>asks[0][0] or min(p for p,q in bids+asks)<=0 or min(q for p,q in bids+asks)<0:
            raise DataError('Invalid or crossed book')
        return {'bids':bids,'asks':asks,'as_of':iso(),'source':'Kraken REST book; timestamp is retrieval time'}

    async def quote(self,symbol):
        raw=await self.http.get(f'https://api.exchange.coinbase.com/products/{symbol}-USD/ticker',ttl=20)
        ts=datetime.fromisoformat(raw['time'].replace('Z','+00:00')).timestamp()
        price=float(raw['price'])
        if price<=0 or ts>time.time()+30: raise DataError('Invalid quote')
        return {'price':price,'as_of':iso(ts),'source':'Coinbase last trade','age_seconds':max(0,time.time()-ts)}


class QuoteStream:
    """One multiplexed WebSocket for 20 symbols; reconnect and stale detection.

    Only quotes are streamed. Candle aggregates remain REST-verified closed bars.
    """
    def __init__(self,coins):
        self.coins=coins
        self.quotes={}
        self.status='starting'
        self.trades=defaultdict(deque)
        self.trade_since={}
        self.dropped_before={}

    async def run(self):
        delay=1
        while True:
            try:
                async with websockets.connect('wss://ws.kraken.com/v2',ping_interval=20,ping_timeout=20,open_timeout=15,max_size=2**20) as socket:
                    self.trade_since.clear()
                    self.trades.clear()
                    self.dropped_before.clear()
                    await socket.send(json.dumps({'method':'subscribe','params':{'channel':'ticker','symbol':[f'{c}/USD' for c in self.coins],'snapshot':True,'event_trigger':'bbo'}}))
                    await socket.send(json.dumps({'method':'subscribe','params':{'channel':'trade','symbol':[f'{c}/USD' for c in self.coins],'snapshot':False}}))
                    self.status='connected'
                    delay=1
                    while True:
                        raw=await asyncio.wait_for(socket.recv(),timeout=45)
                        message=json.loads(raw)
                        if message.get('success') is False:self.status='subscription partially rejected; REST recovery active'
                        if message.get('success') is True and message.get('result',{}).get('channel')=='trade':
                            sym=message['result']['symbol'].split('/')[0]
                            self.trade_since[sym]=time.time()
                        if message.get('channel')=='trade' and message.get('type')=='update':
                            for trade in message.get('data',[]):
                                sym=trade['symbol'].split('/')[0]
                                ts=datetime.fromisoformat(trade['timestamp'].replace('Z','+00:00')).timestamp()
                                price,qty=float(trade['price']),float(trade['qty'])
                                if price<=0 or qty<=0 or trade['side'] not in ('buy','sell'):continue
                                self.trades[sym].append((ts,price,qty,trade['side']))
                                while len(self.trades[sym])>15000 or (self.trades[sym] and self.trades[sym][0][0]<time.time()-900):
                                    self.dropped_before[sym]=self.trades[sym].popleft()[0]
                            continue
                        if message.get('channel')!='ticker':continue
                        for item in message.get('data',[]):
                            symbol=item['symbol'].split('/')[0]
                            bid,ask=float(item['bid']),float(item['ask'])
                            if not 0<bid<=ask:continue
                            # BBO midpoint is NOT labelled a last-trade price.
                            self.quotes[symbol]={'price':(bid+ask)/2,'bid':bid,'ask':ask,'as_of':iso(),
                                                 'received':time.time(),'source':'Kraken WebSocket BBO midpoint; receipt timestamp'}
            except asyncio.CancelledError:raise
            except Exception:
                self.status='disconnected; REST recovery active'
                self.trade_since.clear()
                await asyncio.sleep(delay)
                delay=min(delay*2,60)

    def latest(self,symbol):
        q=self.quotes.get(symbol)
        if q and time.time()-q['received']<=60:return q|{'age_seconds':time.time()-q['received']}
        return None

    def trade_window(self,symbol,start,end):
        if self.trade_since.get(symbol,float('inf'))>start or self.dropped_before.get(symbol,0)>=start:
            return None
        return [t for t in self.trades[symbol] if start<=t[0]<end]


CM_FIELDS='CapMrktCurUSD,CapRealUSD,TxTfrValAdjUSD,AdrActCnt,TxCnt'


class ContextFeeds:
    def __init__(self,http):
        self.http=http
        self.previous_oi={}

    async def onchain(self,symbol):
        start=iso((int(time.time())//86400-7)*86400)[:10]
        data=await self.http.get('https://community-api.coinmetrics.io/v4/timeseries/asset-metrics',
                                {'assets':symbol.lower(),'metrics':CM_FIELDS,'frequency':'1d','start_time':start,
                                 'page_size':100,'ignore_forbidden_errors':'true','ignore_unsupported_errors':'true'},3600)
        return data.get('data',[])

    async def derivatives(self,symbol):
        instrument=f'{symbol}-USDT-SWAP'
        oi=await self.http.get('https://www.okx.com/api/v5/public/open-interest',{'instType':'SWAP','instId':instrument},30)
        history=await self.http.get('https://www.okx.com/api/v5/public/funding-rate-history',{'instId':instrument,'limit':2},300)
        if oi.get('code')!='0' or history.get('code')!='0' or not oi.get('data'):raise DataError('OKX instrument unavailable')
        current=oi['data'][0]
        normalized={'oi_usd':float(current['oiUsd']),'as_of':iso(int(current['ts'])/1000),'previous':self.previous_oi.get(symbol)}
        self.previous_oi[symbol]={'oi_usd':normalized['oi_usd'],'as_of':normalized['as_of']}
        funding=history.get('data',[])
        if len(funding)>=2:
            hours=(int(funding[0]['fundingTime'])-int(funding[1]['fundingTime']))/3600000
            rate=funding[0].get('realizedRate')
            if rate not in ('',None) and hours>0:
                normalized.update(funding=float(rate),funding_hours=hours,funding_as_of=iso(int(funding[0]['fundingTime'])/1000))
        return normalized

    async def stablecoins(self):
        data=await self.http.get('https://stablecoins.llama.fi/stablecoincharts/all',ttl=3600)
        closed=[x for x in data if int(x['date'])<=time.time()-86400]
        if len(closed)<31:raise DataError('Stablecoin history too short')
        return closed[-31:]
