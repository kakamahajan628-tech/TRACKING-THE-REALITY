import asyncio
import copy
import time
from collections import Counter
import numpy as np
import pandas as pd
from .config import Settings
from .models import iso, Results, CATALOG
from .providers import PublicHTTP, Spot, QuoteStream, ContextFeeds, DataError, validated_candles
from .indicators import analyze_prices
from .markets import MarketUniverse
from .vendors import Aggregators
from .enrichment import add_microstructure, add_onchain, add_derivatives, add_universe, load_external, age
from .technical import technical_pack


class Scanner:
    def __init__(self,settings:Settings):
        self.settings=settings
        self.http=PublicHTTP()
        self.spot=Spot(self.http)
        self.feeds=ContextFeeds(self.http)
        self.stream=QuoteStream(settings.coins) if settings.enable_ws and not settings.demo else None
        self.lock=asyncio.Lock()
        self.latest=None
        self.last_error=None
        self.last_attempt=None
        self.skipped=0
        self._analysis_cache={}
        self.market_universe=MarketUniverse(self.http,settings)
        self.aggregators=Aggregators(self.http,settings)
        self._stream_task=None

    async def stop_stream(self):
        if self._stream_task:
            self._stream_task.cancel()
            await asyncio.gather(self._stream_task,return_exceptions=True)
            self._stream_task=None
        self.stream=None

    async def configure(self,state):
        # Controller calls this only between scans; lock also protects direct scanner users.
        async with self.lock:
            changed=self.settings.coins!=state.coins
            if changed:await self.stop_stream()
            self.settings.coins=list(state.coins)
            self.settings.interval=state.interval;self.settings.window=state.window
            self.settings.enable_market_universe=state.all_markets
            self.settings.market_exchanges=list(state.exchanges);self.settings.market_types=list(state.market_types)
            self._analysis_cache={c:v for c,v in self._analysis_cache.items() if c in state.coins}
            if self.settings.enable_ws and not self.settings.demo:
                if not self.stream:self.stream=QuoteStream(list(state.coins))
                if self._stream_task is None or self._stream_task.done():self._stream_task=asyncio.create_task(self.stream.run())

    async def collect(self,coins):
        async def safely(coro):
            try:return await coro
            except (DataError,ValueError,KeyError,IndexError,TypeError) as exc:return {'error':str(exc)[:200]}
        work={}
        for coin in coins:
            work[coin,'daily']=asyncio.create_task(safely(self.spot.candles(coin)))
            if coin in self.settings.coins:
                for name,coro in [('intraday',self.spot.candles(coin,300)),('book',self.spot.book(coin)),('reference_quote',self.spot.quote(coin))]:
                    work[coin,name]=asyncio.create_task(safely(coro))
                if self.settings.enable_coinmetrics:work[coin,'onchain']=asyncio.create_task(safely(self.feeds.onchain(coin)))
                if self.settings.enable_okx:work[coin,'derivatives']=asyncio.create_task(safely(self.feeds.derivatives(coin)))
        if self.settings.enable_defillama:work['GLOBAL','stablecoins']=asyncio.create_task(safely(self.feeds.stablecoins()))
        try:
            done,pending=await asyncio.wait(work.values(),timeout=210)
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            return {key:task.result() if task in done else {'error':'Scan collection deadline (210s) reached'} for key,task in work.items()}
        except BaseException:
            for task in work.values():task.cancel()
            await asyncio.gather(*work.values(),return_exceptions=True)
            raise

    async def scan(self):
        if self.lock.locked():
            self.skipped+=1
            return None
        async with self.lock:
            self.last_attempt=iso()
            started=time.time()
            boundary=int(started)//300*300
            coins=list(dict.fromkeys(self.settings.coins+['BTC','ETH']))
            universe={'enabled':False,'markets':[]}
            aggregators={}
            if self.settings.demo:
                data=demo_data(coins,boundary)
            else:
                async def broad():
                    return await self.market_universe.scan() if self.settings.enable_market_universe else universe
                data,universe,aggregators=await asyncio.gather(self.collect(coins),broad(),self.aggregators.scan())
            histories={c:data[c,'daily'] for c in coins if isinstance(data.get((c,'daily')),pd.DataFrame)}
            results={}
            for coin in self.settings.coins:
                # Cache daily estimation by the actual input bytes, not the five-minute report clock.
                signature=(self.settings.window,self.settings.rf_annual,tuple((c,f.attrs['source'],int(pd.util.hash_pandas_object(f,index=True).sum())) for c,f in histories.items() if c in (coin,'BTC','ETH')))
                if coin in self._analysis_cache and self._analysis_cache[coin][0]==signature:
                    result=copy.deepcopy(self._analysis_cache[coin][1])
                    for row in result.rows.values():
                        if row.as_of and age(row.as_of)>93600 and row.status in ('ok','model'):row.status='stale'
                else:
                    result=await asyncio.to_thread(analyze_prices,coin,histories,self.settings.window,self.settings.rf_annual,started)
                    self._analysis_cache[coin]=(signature,copy.deepcopy(result))
                results[coin]=result
                bars=data.get((coin,'intraday')); book=data.get((coin,'book'))
                add_microstructure(result,bars if isinstance(bars,pd.DataFrame) else None,
                                   book if isinstance(book,dict) and 'bids' in book else None,self.stream,coin,boundary)
                chain=data.get((coin,'onchain'))
                if isinstance(chain,list):add_onchain(result,chain)
                derivative=data.get((coin,'derivatives'))
                if isinstance(derivative,dict) and 'oi_usd' in derivative:add_derivatives(result,derivative)
            add_universe(results,histories,self.settings.coins,self.settings.window)
            stable=data.get(('GLOBAL','stablecoins'))
            if isinstance(stable,list) and len(stable)>=31:
                try:
                    current=float(stable[-1]['totalCirculatingUSD']['peggedUSD'])
                    prior=float(stable[0]['totalCirculatingUSD']['peggedUSD'])
                    stamp=iso(int(stable[-1]['date']))
                    if current>0 and prior>0:
                        for result in results.values():
                            result.put(128,{'supply_usd':current,'growth_30d':current/prior-1},'USD / fraction',
                                       'GLOBAL USD-pegged stablecoin context, not coin-specific demand; provider scope',
                                       source='DefiLlama stablecoins',as_of=stamp,frequency='1d',sample=31,
                                       status='ok' if age(stamp)<3*86400 else 'stale')
                except (KeyError,TypeError,ValueError):pass
            errors=load_external(self.settings.external_file,results)
            assets=[]
            for coin in self.settings.coins:
                result=results[coin]
                q=self.stream.latest(coin) if self.stream else None
                ref=data.get((coin,'reference_quote'))
                if not (isinstance(ref,dict) and 'price' in ref):ref=None
                book=data.get((coin,'book'))
                if q is None and isinstance(book,dict) and 'bids' in book:
                    q={'price':(book['bids'][0][0]+book['asks'][0][0])/2,'as_of':book['as_of'],
                       'source':'Kraken REST BBO midpoint; retrieval timestamp','age_seconds':max(0,age(book['as_of']))}
                if q is None:q=ref
                if q is None and coin in histories:
                    frame=histories[coin]
                    q={'price':float(frame.close.iloc[-1]),'as_of':iso(int(frame.index[-1])+86400),
                       'source':frame.attrs['source']+' closed daily candle; NOT LIVE', 'age_seconds':started-(int(frame.index[-1])+86400)}
                if q:
                    q=copy.deepcopy(q)
                    q['age_seconds']=max(0,age(q['as_of']))
                    q['status']='stale' if q['age_seconds']>120 else 'ok'
                metrics=result.list()
                counts=dict(Counter(m['status'] for m in metrics))
                insights=practical_insights(result,q,ref)
                technical=technical_pack(data.get((coin,'daily')),data.get((coin,'intraday')),started)
                assets.append({'symbol':coin,'quote':q,'reference_quote':ref,'metrics':metrics,'coverage':counts,'checks':insights,'technical':technical,
                               'sparkline':histories[coin].close.tail(30).tolist() if coin in histories else []})
            issues=[{'coin':c,'feed':f,'reason':v['error']} for (c,f),v in data.items() if isinstance(v,dict) and 'error' in v]
            report={'schema_version':3,'name':'ATLAS 136','scan_id':str(int(started*1000)),'scan_boundary':iso(boundary),'generated_at':iso(),
                    'duration_seconds':round(time.time()-started,2),'interval_seconds':self.settings.interval,'lookback_days':self.settings.window,
                    'mode':'DEMO — SYNTHETIC FIXTURES, NOT MARKET DATA' if self.settings.demo else 'LIVE PROVIDER RESPONSES',
                    'coins':assets,'catalog':CATALOG,'provider_health':copy.deepcopy(self.http.health),'issues':issues,
                    'external_errors':errors,'stream_status':self.stream.status if self.stream else 'disabled',
                    'market_universe':universe,'aggregators':aggregators,
                    'notes':['One scan contains 136 metric records per coin. Unavailable is not zero.',
                             'Configurable publication cadence; daily, snapshot, scenario and model horizons stay separate.',
                             'No composite buy/sell score, trade execution or guaranteed correctness.',
                             'Coverage counts are completeness, not confidence. Imported values are separate from connected feeds.',
                             'All-market spot/perpetual snapshots are separate from the 136-record research watchlist.']}
            self.latest=report
            self.last_error=None
            return report

    async def close(self):
        await self.stop_stream()
        await self.http.close()


def practical_insights(r,q,reference):
    checks=[]
    counts=Counter(m.status for m in r.rows.values())
    checks.append({'name':'Evidence coverage','value':f"{counts['ok']}/136 current observations or calculations",'note':'Completeness is not a probability of correctness. Model, scenario and imported rows are counted separately.'})
    checks.append({'name':'Price freshness at scan','value':'Current' if q and q['age_seconds']<=120 else 'Stale or missing','note':q['source']+'; '+q['as_of'] if q else 'No quote source available'})
    beta=r.rows[3]; residual=r.rows[26]; fit=r.rows[24]
    if beta.status=='ok' and fit.status=='ok':checks.append({'name':'Market exposure','value':f'BTC beta {beta.value:.2f} · R² {fit.value:.2f}','note':'A higher return can reflect higher beta. Low fit limits market attribution.'})
    if residual.status=='ok':checks.append({'name':'Unexplained daily move','value':f'{residual.value*100:+.2f}%','note':'Prior-fit BTC residual; not proof of informed buying or manipulation.'})
    if q and reference and q['source'].startswith('Kraken') and q['age_seconds']<=60 and reference['age_seconds']<=60:
        divergence=abs(q['price']/reference['price']-1)*10000
        checks.append({'name':'Cross-venue price difference','value':f'{divergence:.1f} bps','note':'Kraken midpoint vs Coinbase recent trade, USD pairs. Timing and spread can explain differences; not arbitrage profit.'})
    else:checks.append({'name':'Cross-venue price difference','value':'Unavailable','note':'Needs two fresh, comparable quotes; one source does not independently verify itself.'})
    checks.extend([{'name':'Execution readiness','value':'Needs your order details','note':'Real fees, realized slippage, size and fill quality cannot be inferred from a chart.'},
                   {'name':'Survival and discipline','value':'Manual review','note':'Check essential capital, invalidation and urgency. The bot cannot observe your finances or psychology.'}])
    return checks


def demo_data(coins,boundary):
    rng=np.random.default_rng(136)
    end=boundary//86400*86400
    times=np.arange(end-366*86400,end,86400)
    common=rng.normal(.0008,.025,len(times)-1)
    data={}
    bases={'BTC':64000,'ETH':3200,'SOL':140,'XRP':.6,'ADA':.45,'DOGE':.12,'LINK':15,'AVAX':32,'DOT':6,'LTC':80}
    for idx,c in enumerate(coins):
        changes=common if c=='BTC' else common*(.8+idx*.08)+rng.normal(0,.015,len(common))
        prices=bases.get(c,25)*np.r_[1,np.cumprod(1+changes)]
        rows=[dict(time=int(t),open=float(p),high=float(p*1.02),low=float(p*.98),close=float(p),volume=10000.,quote_volume=float(p*10000)) for t,p in zip(times,prices)]
        data[c,'daily']=validated_candles(rows,'SYNTHETIC demonstration',86400,now=boundary)
        intraday_rng=np.random.default_rng(13600+idx)
        itimes=np.arange(boundary-300*300,boundary,300)
        iprices=prices[-1]*np.cumprod(1+intraday_rng.normal(0,.002,len(itimes)))
        irows=[dict(time=int(t),open=float(p),high=float(p*1.003),low=float(p*.997),close=float(p),volume=float(v))
               for t,p,v in zip(itimes,iprices,intraday_rng.uniform(100,10000,len(itimes)))]
        data[c,'intraday']=validated_candles(irows,'SYNTHETIC demonstration',300,now=boundary)
        data[c,'reference_quote']={'price':float(prices[-1]),'as_of':iso(boundary),'source':'SYNTHETIC demonstration','age_seconds':0}
    return data
