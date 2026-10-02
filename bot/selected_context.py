"""Optional provider identity/context for selected symbols; never candle inputs."""
import asyncio
import time
from .models import iso
from .providers import DataError
from .routing import number


class SelectedContext:
    def __init__(self,http,settings):
        self.http,self.settings=http,settings
        self.cache={}

    async def get(self,coin):
        if coin in self.cache and time.time()-self.cache[coin][0]<21600:return self.cache[coin][1]
        async def cmc():
            if not self.settings.enable_cmc or not self.settings.cmc_api_key:
                return {'status':'not configured','note':'Optional CMC_API_KEY; core exchange scans do not need it.'}
            data=await self.http.get('https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest',
                {'symbol':coin,'convert':'USDT'},ttl=21600,headers={'X-CMC_PRO_API_KEY':self.settings.cmc_api_key})
            if data.get('status',{}).get('error_code') not in (None,0):raise DataError('CoinMarketCap rejected request or plan entitlement')
            rows=data.get('data',{}).get(coin,[])
            if isinstance(rows,dict):rows=[rows]
            if len(rows)!=1 or rows[0].get('symbol','').upper()!=coin:
                return {'status':'ambiguous / unavailable','note':'No unique provider symbol; no automatic identity join.'}
            row=rows[0];quote=row.get('quote',{}).get('USDT',{})
            return {'status':'reference only','id':row.get('id'),'name':row.get('name'),'symbol':row.get('symbol'),
                    'rank':row.get('cmc_rank'),'market_cap_usdt':number(quote.get('market_cap')),
                    'circulating_supply':number(row.get('circulating_supply')),'as_of':quote.get('last_updated'),
                    'note':'CoinMarketCap token context. Verify name/ID against your contract. Not substituted for the selected exchange price or indicators.'}
        async def gecko():
            if not self.settings.coingecko_api_key:
                return {'status':'not configured','note':'Optional COINGECKO_API_KEY (Demo API) for identity lookup and derivatives venue ranking.'}
            data=await self.http.get('https://api.coingecko.com/api/v3/search',{'query':coin},ttl=21600,
                headers={'x-cg-demo-api-key':self.settings.coingecko_api_key})
            rows=[r for r in data.get('coins',[]) if r.get('symbol','').upper()==coin]
            return {'status':'reference only' if len(rows)==1 else 'ambiguous / unavailable',
                    'matches':[{'id':r.get('id'),'name':r.get('name'),'rank':r.get('market_cap_rank')} for r in rows[:5]],
                    'as_of':iso(),'note':'Identity search; timestamp is retrieval. Multiple symbol matches are not merged. No price or indicator replacement.'}
        results=await asyncio.gather(cmc(),gecko(),return_exceptions=True)
        out={name:({'status':'unavailable','note':str(result) if isinstance(result,DataError) else type(result).__name__}
                   if isinstance(result,Exception) else result) for name,result in zip(('coinmarketcap','coingecko'),results)}
        self.cache[coin]=(time.time(),out)
        return out
