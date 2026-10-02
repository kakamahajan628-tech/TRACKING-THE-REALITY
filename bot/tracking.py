"""Shared watchlist policy. USD and USDT are never treated as interchangeable."""
from datetime import datetime, timezone, timedelta
import re

MAX_COINS = 50
TIMEFRAMES = {'5m':300, '1h':3600, '4h':14400, '1d':86400}
EXCHANGES = ('okx','bybit','bitget','gate','mexc','kucoin','bingx','htx','phemex','coinex')
IST = timezone(timedelta(hours=5, minutes=30))


def ist(value):
    if not value:return 'unavailable'
    try:
        date = datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else datetime.fromtimestamp(value,timezone.utc)
        if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
        return date.astimezone(IST).strftime('%d %b %Y %H:%M:%S IST')
    except (ValueError,TypeError,OverflowError):return 'unavailable'


def validate_routes(routes, coins):
    if not isinstance(routes,dict):raise ValueError('Routes must be a mapping.')
    result={}
    for coin,route in routes.items():
        if coin not in coins:continue
        if not isinstance(route,dict) or set(route)-{'exchange','market'}:raise ValueError('Invalid route.')
        exchange=route.get('exchange','auto');market=route.get('market','auto')
        if exchange not in ('auto',*EXCHANGES) or market not in ('auto','spot','perpetual'):
            raise ValueError('Choose an integrated exchange and auto, spot or perp. Binance is excluded.')
        result[coin]={'exchange':exchange,'market':market}
    return result


def symbol_valid(symbol):
    return isinstance(symbol,str) and bool(re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,23}',symbol))
