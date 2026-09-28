"""Optional aggregator context, kept separate from venue observations and book metrics."""
import asyncio
import copy
import time
from .models import iso
from .markets import num
from .providers import DataError


class Aggregators:
    def __init__(self,http,settings):
        self.http,self.settings=http,settings
        self.cache={}

    async def cmc(self):
        key=self.settings.cmc_api_key
        root='https://pro-api.coinmarketcap.com'+('' if key else '/public-api')
        headers={'X-CMC_PRO_API_KEY':key} if key else None
        records=[];seen=set();complete=False
        # No arbitrary top-100 limit: walk the provider catalog; disclose the safety cap.
        for start in range(1,50001,1000):
            raw=await self.http.get(root+'/v3/cryptocurrency/listings/latest',
                                    {'start':start,'limit':1000,'convert':'USD'},headers=headers)
            if str(raw.get('status',{}).get('error_code','0'))!='0':raise DataError('CoinMarketCap rejected request or plan limit')
            page=raw.get('data')
            if not isinstance(page,list):raise DataError('CoinMarketCap response schema unavailable')
            for x in page:
                identity=x['id']
                if identity in seen:raise DataError('CoinMarketCap repeated pagination; incomplete catalog')
                seen.add(identity)
                quotes=x.get('quote',[])
                q=next((q for q in quotes if q.get('symbol')=='USD'),{}) if isinstance(quotes,list) else quotes.get('USD',{})
                records.append(dict(id=identity,symbol=x['symbol'],name=x.get('name'),slug=x.get('slug'),
                                    price_usd=num(q.get('price')),market_cap_usd=num(q.get('market_cap')),
                                    volume_24h_usd=num(q.get('volume_24h')),rank=x.get('cmc_rank'),
                                    change_24h_percent=num(q.get('percent_change_24h')),as_of=q.get('last_updated') or x.get('last_updated')))
            if len(page)<1000:complete=True;break
        return dict(status='ok' if complete else 'partial',complete=complete,records=records,
                    fetched_at=iso(),mode='keyed API' if key else 'keyless public API',
                    note='Provider listings context; CMC IDs retained. Not automatically joined to exchange symbols because ticker collisions exist. Refresh is hourly by default; source timestamps remain in each row.')

    async def coinglass(self):
        rows=[];seen=set();complete=False
        for page in range(1,51):
            raw=await self.http.get('https://open-api-v4.coinglass.com/api/futures/coins-markets',
                {'exchange_list':'Binance,OKX,Bybit,Bitget','per_page':100,'page':page},headers={'CG-API-KEY':self.settings.coinglass_api_key})
            if str(raw.get('code'))!='0':raise DataError('CoinGlass rejected request; check API key and endpoint plan entitlement')
            items=raw.get('data')
            if not isinstance(items,list):raise DataError('CoinGlass response schema unavailable')
            for x in items:
                symbol=x['symbol']
                if symbol in seen:raise DataError('CoinGlass repeated pagination; incomplete catalog')
                seen.add(symbol)
                # Preserve documented names and units; no guessed percent/fraction conversions.
                values={k:num(v) for k,v in x.items() if k!='symbol' and num(v) is not None}
                rows.append(dict(symbol=symbol,values=values,as_of=None,
                                 timestamp_kind='provider observation time not supplied; fetched_at is retrieval only'))
            if len(items)<100:complete=True;break
        return dict(status='ok' if complete else 'partial',complete=complete,records=rows,fetched_at=iso(),
                    note='CoinGlass aggregate for Binance, OKX, Bybit and Bitget, subject to provider support. Native field units retained. No per-exchange OI sums or unverified symbol joins. Endpoint requires eligible paid access; retrieval time is not an observation time.')

    async def scan(self):
        async def load(name,enabled,fn):
            if not enabled:return dict(status='not_configured',records=[],note='Set COINGLASS_API_KEY with coins-markets entitlement' if name=='coinglass' else 'ENABLE_CMC=false')
            cached=self.cache.get(name)
            if cached and time.time()-cached[0]<self.settings.vendor_refresh_seconds:return copy.deepcopy(cached[1])
            try:
                async with asyncio.timeout(90):result=await fn()
                self.cache[name]=(time.time(),result)
                return copy.deepcopy(result)
            except (TimeoutError,DataError,ValueError,KeyError,TypeError) as exc:
                result=copy.deepcopy(cached[1]) if cached else {'records':[]}
                result.update(status='stale' if result['records'] else 'unavailable',complete=False,
                              error=str(exc)[:150] if isinstance(exc,DataError) else type(exc).__name__+'; vendor collection incomplete')
                # Avoid paid/API-error retry storms. Retain the original fetched_at.
                self.cache[name]=(time.time(),result)
                return result
        cmc,cg=await asyncio.gather(load('coinmarketcap',self.settings.enable_cmc,self.cmc),
                                   load('coinglass',bool(self.settings.coinglass_api_key),self.coinglass))
        return dict(coinmarketcap=cmc,coinglass=cg,tradingview=dict(status='chart_links_only',records=[],
            note='No public market-data/indicator API. Search links are available for each market; chart availability is not guaranteed.'))
