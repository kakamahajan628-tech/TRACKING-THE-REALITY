"""Selected-instrument scans, daily benchmarks and four closed-candle views."""
import asyncio
import copy
import hashlib
import time
from datetime import datetime
from collections import Counter
import numpy as np
import pandas as pd
from .models import CATALOG, iso
from .providers import PublicHTTP, ContextFeeds, DataError, validated_candles
from .routing import ExchangeRouter
from .tracking import TIMEFRAMES, MAX_COINS
from .technical import studies
from .timeframe_metrics import price_metrics, derivative_context, add_derivative_studies
from .enrichment import add_onchain, load_external
from .fixtures import demo_data
from .selected_context import SelectedContext


def empty_entry(reason=''):
    return {'frames':{},'benchmarks':{},'route':None,'quote':None,'derivatives':None,'issues':[reason] if reason else []}


class WatchlistScanner:
    def __init__(self,settings):
        self.settings=settings;self.http=PublicHTTP();self.feeds=ContextFeeds(self.http)
        self.router=ExchangeRouter(settings,self.http)
        self.context=SelectedContext(self.http,settings)
        self.lock=asyncio.Lock();self.latest=None;self.last_error=None;self.last_attempt=None;self.skipped=0
        self.stream=None;self._stream_task=None;self._analysis_cache={};self.coin_gate=asyncio.Semaphore(5)
        self._cursor=0

    async def stop_stream(self):pass

    async def configure(self,state):
        async with self.lock:
            self.settings.coins=list(state.coins);self.settings.interval=state.interval;self.settings.window=state.window
            self.settings.routes=copy.deepcopy(state.routes);self.settings.enable_market_universe=False
            self.router.selected={c:r for c,r in self.router.selected.items() if c in state.coins}
            self._analysis_cache={k:v for k,v in self._analysis_cache.items() if k[0] in state.coins}

    async def collect_coin(self,coin):
        result=empty_entry()
        async with self.coin_gate:
            try:
                route,frame,issues=await self.router.resolve(coin,self.settings.routes.get(coin))
                result['route']=route;result['frames']['5m']=frame;result['issues'].extend(issues)
                async def frame_for(tf):
                    try:result['frames'][tf]=await self.router.candles(route,tf)
                    except DataError as exc:result['issues'].append(str(exc))
                async def benchmark(symbol):
                    try:
                        if symbol==coin:result['benchmarks'][symbol]=await self.router.candles(route,'1d');return
                        async for match in self.router.candidates(symbol,{'exchange':route['exchange'],'market':route['market']}):
                            result['benchmarks'][symbol]=await self.router.candles(match,'1d');return
                        result['issues'].append(symbol+' benchmark unavailable on selected venue/product')
                    except DataError as exc:result['issues'].append(str(exc))
                async def quote():
                    try:result['quote']=await self.router.quote(route)
                    except DataError as exc:result['issues'].append(str(exc))
                async def derivative():result['derivatives']=await self.router.derivatives(route)
                async def book():
                    try:result['book']=await self.router.book(route)
                    except DataError as exc:result['issues'].append(str(exc))
                tasks=[frame_for(tf) for tf in ('1h','4h','1d')]+[benchmark(s) for s in ('BTC','ETH')]+[quote(),derivative(),book()]
                async def context():result['context']=await self.context.get(coin)
                tasks.append(context())
                if self.settings.enable_coinmetrics:
                    async def chain():
                        try:result['onchain']=await self.feeds.onchain(coin)
                        except (DataError,ValueError,KeyError):pass
                    tasks.append(chain())
                outcomes=await asyncio.gather(*tasks,return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome,Exception):result['issues'].append('Provider task failed: '+type(outcome).__name__)
            except DataError as exc:result['issues'].append(str(exc))
        return result

    def demonstration(self,coin,now,data):
        route={'exchange':'DEMO','market':'perpetual','symbol':coin+'/USDT:USDT','base':coin,'quote':'USDT','adapter':'demo','id':coin+'USDT','contract_size':1}
        result=empty_entry();result['route']=route
        for symbol in set((coin,'BTC','ETH')):
            f=data[symbol,'daily'].copy();f.attrs.update(source='DEMO / perpetual / USDT',quote='USDT',market='perpetual',symbol=symbol+'/USDT:USDT',volume_unit=symbol)
            if symbol==coin:result['frames']['1d']=f
            result['benchmarks'][symbol]=f
        for tf,period in TIMEFRAMES.items():
            if tf=='1d':continue
            rng=np.random.default_rng(int(hashlib.sha256((coin+tf).encode()).hexdigest()[:8],16));n=280
            close=float(result['frames']['1d'].close.iloc[-1])*np.exp(np.cumsum(rng.normal(0,.005,n)));start=int(now)//period*period-n*period
            raw=[{'time':start+i*period,'open':x,'high':x*1.005,'low':x*.995,'close':x,'volume':100+i} for i,x in enumerate(close)]
            f=validated_candles(raw,'DEMO / perpetual / USDT',period,now)
            f.attrs.update(quote='USDT',market='perpetual',symbol=route['symbol'],volume_unit=coin);result['frames'][tf]=f
        return result

    async def collect(self,started):
        if self.settings.demo:
            fixture=demo_data(list(dict.fromkeys(self.settings.coins+['BTC','ETH'])),int(started)//300*300)
            return {c:self.demonstration(c,started,fixture) for c in self.settings.coins}
        await self.router.prepare()
        coins=self.settings.coins;offset=self._cursor%len(coins)
        self._cursor=(offset+5)%len(coins)
        work={c:asyncio.create_task(self.collect_coin(c)) for c in coins[offset:]+coins[:offset]}
        try:
            done,pending=await asyncio.wait(work.values(),timeout=240)
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            data={}
            for coin in coins:
                task=work[coin]
                if task in done and not task.exception():data[coin]=task.result()
                else:data[coin]=empty_entry('Collection deadline or provider error; cached inputs will be reused next scan.')
            return data
        except BaseException:
            for task in work.values():task.cancel()
            await asyncio.gather(*work.values(),return_exceptions=True);raise

    async def analyze(self,coin,entry,started):
        metrics={};technical={};route=entry['route']
        derivative=derivative_context(entry['derivatives'] or {'market':route['market'] if route else 'unavailable',
            'source':route['exchange']+' '+route['symbol'] if route else '', 'funding':None,'open_interest':None,
            'issues':['No native derivative observation collected.']},started)
        for tf in TIMEFRAMES:
            frame=entry['frames'].get(tf);histories={coin:frame} if frame is not None else {}
            if tf=='1d':histories={**entry['benchmarks'],**histories}
            signature=(self.settings.window,self.settings.rf_annual,tuple((s,h.attrs.get('source'),h.attrs.get('symbol'),int(pd.util.hash_pandas_object(h,index=True).sum())) for s,h in sorted(histories.items())))
            cached=self._analysis_cache.get((coin,tf))
            if cached and cached[0]==signature:
                r=copy.deepcopy(cached[1])
                for row in r.rows.values():
                    if row.as_of and row.status in ('ok','model') and started-datetime.fromisoformat(row.as_of).timestamp()>TIMEFRAMES[tf]+(7200 if tf=='1d' else 120):row.status='stale'
            else:
                r=await asyncio.to_thread(price_metrics,coin,histories,self.settings.window,self.settings.rf_annual,started,tf)
                self._analysis_cache[coin,tf]=(signature,copy.deepcopy(r))
            metrics[tf]=r;technical[tf]=studies(frame,tf,started);add_derivative_studies(technical[tf],derivative,started)
        daily=metrics['1d']
        book=entry.get('book')
        if book:
            bids,asks=book['bids'],book['asks'];mid=(bids[0][0]+asks[0][0])/2
            meta=dict(source=book['source'],as_of=book['as_of'],frequency='native snapshot',sample=len(bids)+len(asks),
                      status='ok' if started-datetime.fromisoformat(book['as_of']).timestamp()<=180 else 'stale')
            daily.put(83,(asks[0][0]-bids[0][0])/mid*10000,'bps','Selected instrument quoted spread',**meta)
            if bids[-1][0]<=mid*.999 and asks[-1][0]>=mid*1.001:
                bid=sum(p*q for p,q in bids if p>=mid*.999);ask=sum(p*q for p,q in asks if p<=mid*1.001)
                daily.put(84,{'bid':bid,'ask':ask},'USDT within +/-10 bps','Snapshot orders; not executed volume',**meta)
                if bid+ask>0:daily.put(89,(bid-ask)/(bid+ask),'ratio','Symmetric complete +/-10 bps depth band',**meta)
            else:
                for identifier in (84,89):daily.missing(identifier,'Returned order-book depth does not cover the entire +/-10 bps band')
        if isinstance(entry.get('onchain'),list):add_onchain(daily,entry['onchain'])
        for identifier,key in [(94,'open_interest'),(97,'funding')]:
            observation=derivative.get(key)
            if observation:
                study=next(s for s in technical['1d']['rows'] if s['id']==('oi' if key=='open_interest' else 'funding'))
                daily.put(identifier,observation['value'],observation['unit'],'Selected contract; native snapshot, not a daily candle calculation',
                    source=derivative['source'],as_of=observation['as_of'],frequency='native snapshot',sample=1,status=study['status'])
        daily_oi=derivative['daily_oi']
        if daily_oi['status']=='ok':daily.put(95,daily_oi['change_fraction'],'fraction','Exact closed daily OI change',source=derivative['source'],as_of=daily_oi['end'],frequency='1d',sample=2)
        return metrics,technical,derivative

    async def scan(self):
        if self.lock.locked():self.skipped+=1;return None
        async with self.lock:
            started=time.time();self.last_attempt=iso(started);data=await self.collect(started)
            prepared={};results={};observed=time.time()
            for coin,entry in data.items():
                prepared[coin]=await self.analyze(coin,entry,observed);results[coin]=prepared[coin][0]['1d']
            external_errors=load_external(self.settings.external_file,results)
            assets=[];all_issues=[]
            for coin,entry in data.items():
                metrics,technical,derivative=prepared[coin];daily=metrics['1d'].list();route=entry['route'];quote=entry['quote']
                if quote is None and '5m' in entry['frames']:
                    frame=entry['frames']['5m'];stamp=int(frame.index[-1])+300
                    quote={'price':float(frame.close.iloc[-1]),'unit':'USDT','source':frame.attrs['source']+' '+frame.attrs['symbol']+' closed 5m candle',
                           'as_of':iso(stamp),'age_seconds':max(0,started-stamp),'status':'closed candle','timestamp_kind':'candle close, not live trade'}
                entry['issues'].extend(derivative.get('issues',[]))
                all_issues.extend({'coin':coin,'feed':'route/data','reason':reason} for reason in entry['issues'])
                checks=[{'name':'Selected instrument','value':route['symbol'] if route else 'Unavailable','note':route['exchange']+' / '+route['market'] if route else 'No eligible accessible USDT market'},
                        {'name':'Daily beta','value':str(metrics['1d'].rows[3].value),'note':metrics['1d'].rows[3].note}]
                assets.append({'symbol':coin,'route':route,'quote':quote,'reference_quote':None,'metrics':daily,
                    'context':entry.get('context',{}),
                    'metric_frames':{tf:r.list() for tf,r in metrics.items()},'technical':technical,'derivatives':derivative,
                    'coverage':dict(Counter(m['status'] for m in daily)),'checks':checks,
                    'sparkline':entry['frames']['1d'].close.tail(30).tolist() if '1d' in entry['frames'] else [],'issues':entry['issues']})
            report={'schema_version':4,'name':'ATLAS TRACKING','scan_id':str(int(started*1000)),'scan_boundary':iso(int(started)//300*300),
                'generated_at':iso(),'duration_seconds':round(time.time()-started,2),'interval_seconds':self.settings.interval,
                'lookback_days':self.settings.window,'mode':'DEMO - SYNTHETIC FIXTURES, NOT MARKET DATA' if self.settings.demo else 'LIVE PROVIDER RESPONSES',
                'coins':assets,'catalog':CATALOG,'timeframes':list(TIMEFRAMES),'display_timezone':'Asia/Kolkata','price_quote':'USDT','max_coins':MAX_COINS,
                'provider_health':{**self.http.health,**self.router.health},'issues':all_issues,'external_errors':external_errors,
                'stream_status':'REST snapshots; selected instruments only','routing':copy.deepcopy(self.router.ranking),
                'market_universe':{'enabled':False,'markets':[]},'aggregators':{},
                'notes':['Only tracked instruments plus matching BTC/ETH daily benchmarks are measured.',
                         '5m/1h/4h/1d boundaries remain exchange UTC boundaries; all display times are IST. UTC daily close = 05:30 IST.',
                         'Beta is daily-only. Other metrics keep native frequency. Funding/OI are not fabricated for candle timeframes.',
                         'No claimed parity across venues or guaranteed accuracy. Missing evidence remains unavailable.']}
            self.latest=report;self.last_error=None;return report

    async def close(self):
        await self.router.close();await self.http.close()
