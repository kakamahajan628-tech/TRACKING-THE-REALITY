from dataclasses import dataclass, field
import os
import re
from urllib.parse import urlsplit

DEFAULT_COINS = 'BTC,ETH,SOL,XRP,ADA,DOGE,LINK,AVAX,DOT,LTC'


def telegram_credentials():
    """Select a complete credential pair, never mix credentials across versions."""
    modern = tuple(os.getenv(key, '').strip() for key in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'))
    legacy = tuple(os.getenv(key, '').strip() for key in ('TELEGRAM_TOKEN', 'USER_CHAT_ID'))
    selected = modern if any(modern) else legacy
    if bool(selected[0]) != bool(selected[1]):
        names = 'TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID' if any(modern) else 'TELEGRAM_TOKEN and USER_CHAT_ID'
        raise ValueError('Set both ' + names + ', or leave both empty')
    return selected


@dataclass
class Settings:
    coins: list[str] = field(default_factory=lambda: DEFAULT_COINS.split(','))
    interval: int = 300
    window: int = 180
    telegram_token: str = ''
    telegram_chat: str = ''
    dashboard_token: str = ''
    enable_ws: bool = True
    enable_okx: bool = False
    enable_coinmetrics: bool = True
    enable_defillama: bool = True
    demo: bool = False
    rf_annual: float = 0.0
    external_file: str = ''
    enable_market_universe: bool = True
    market_exchanges: list[str] = field(default_factory=lambda: ['binance','bitget','bybit','okx'])
    market_types: list[str] = field(default_factory=lambda: ['spot','perpetual'])
    market_detail_batch: int = 10
    enable_cmc: bool = True
    cmc_api_key: str = ''
    coinglass_api_key: str = ''
    vendor_refresh_seconds: int = 3600
    public_url: str = ''
    telegram_owner_id: str = ''
    control_state_file: str = 'runtime/control.json'
    control_redis_url: str = ''
    control_redis_token: str = field(default='',repr=False)

    def __post_init__(self):
        self.coins = list(dict.fromkeys(c.upper().strip() for c in self.coins))
        if not 1 <= len(self.coins) <= 20 or any(not re.fullmatch(r'[A-Z0-9]{2,12}', c) for c in self.coins):
            raise ValueError('COINS must contain 1–20 unique asset symbols, e.g. BTC,ETH,SOL')
        if type(self.interval) is not int or not 300<=self.interval<=86400 or self.interval%60 or not 90 <= self.window <= 365:
            raise ValueError('SCAN_SECONDS must be whole minutes from 300 to 86400; LOOKBACK_DAYS must be 90–365')
        if bool(self.telegram_token) != bool(self.telegram_chat):
            raise ValueError('Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or neither')
        if self.rf_annual <= -1:
            raise ValueError('RISK_FREE_ANNUAL must be greater than -1')
        self.market_exchanges=list(dict.fromkeys(x.strip().lower() for x in self.market_exchanges))
        self.market_types=list(dict.fromkeys(x.strip().lower() for x in self.market_types))
        if not self.market_exchanges or set(self.market_exchanges)-{'binance','bitget','bybit','okx'}:
            raise ValueError('MARKET_EXCHANGES: binance,bitget,bybit,okx')
        if not self.market_types or set(self.market_types)-{'spot','perpetual'}:
            raise ValueError('MARKET_TYPES: spot,perpetual')
        if not 0<=self.market_detail_batch<=20 or self.vendor_refresh_seconds<900:
            raise ValueError('MARKET_DETAIL_BATCH must be 0–20; VENDOR_REFRESH_SECONDS must be >=900')
        if self.telegram_owner_id and not re.fullmatch(r'[1-9][0-9]*',self.telegram_owner_id):
            raise ValueError('TELEGRAM_OWNER_ID must be a positive numeric user ID')
        if bool(self.control_redis_url)!=bool(self.control_redis_token):
            raise ValueError('Set both CONTROL_REDIS_URL and CONTROL_REDIS_TOKEN, or neither')
        for name,value in [('PUBLIC_BASE_URL',self.public_url),('CONTROL_REDIS_URL',self.control_redis_url)]:
            if not value:continue
            u=urlsplit(value)
            if not u.hostname or u.username or u.password or u.query or u.fragment or u.scheme!='https' or u.path not in ('','/'):
                raise ValueError(name+' must be an HTTPS origin without credentials, query or path')
        self.public_url=self.public_url.rstrip('/')
        self.control_redis_url=self.control_redis_url.rstrip('/')

    @classmethod
    def env(cls):
        yes = lambda key, default: os.getenv(key, default).lower() == 'true'
        token, chat = telegram_credentials()
        return cls(coins=os.getenv('COINS', DEFAULT_COINS).split(','),
                   interval=int(os.getenv('SCAN_SECONDS', '300')), window=int(os.getenv('LOOKBACK_DAYS', '180')),
                   telegram_token=token, telegram_chat=chat,
                   dashboard_token=os.getenv('DASHBOARD_TOKEN', ''),
                   enable_ws=yes('ENABLE_WEBSOCKET', 'true'), enable_okx=yes('ENABLE_OKX', 'false'),
                   enable_coinmetrics=yes('ENABLE_COINMETRICS', 'true'), enable_defillama=yes('ENABLE_DEFILLAMA', 'true'),
                   demo=yes('DEMO_MODE', 'false'), rf_annual=float(os.getenv('RISK_FREE_ANNUAL', '0')),
                   external_file=os.getenv('EXTERNAL_METRICS_FILE', ''),
                   enable_market_universe=yes('ENABLE_MARKET_UNIVERSE','true'),
                   market_exchanges=os.getenv('MARKET_EXCHANGES','binance,bitget,bybit,okx').split(','),
                   market_types=os.getenv('MARKET_TYPES','spot,perpetual').split(','),
                   market_detail_batch=int(os.getenv('MARKET_DETAIL_BATCH','10')),
                   enable_cmc=yes('ENABLE_CMC','true'),cmc_api_key=os.getenv('CMC_API_KEY',''),
                   coinglass_api_key=os.getenv('COINGLASS_API_KEY',''),
                   vendor_refresh_seconds=int(os.getenv('VENDOR_REFRESH_SECONDS','3600')),
                   public_url=os.getenv('PUBLIC_BASE_URL','') or os.getenv('RENDER_EXTERNAL_URL',''),
                   telegram_owner_id=os.getenv('TELEGRAM_OWNER_ID',''),
                   control_state_file=os.getenv('CONTROL_STATE_FILE','runtime/control.json'),
                   control_redis_url=os.getenv('CONTROL_REDIS_URL',''),control_redis_token=os.getenv('CONTROL_REDIS_TOKEN',''))
