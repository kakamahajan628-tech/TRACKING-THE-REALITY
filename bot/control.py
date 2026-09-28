"""Single-owner preferences and a serial, wakeable scanner scheduler.

Desired preferences never mutate an in-flight scan. Persistence succeeds before
changes are accepted. Redis outages fail closed instead of overwriting saved state.
"""
import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import time
from dataclasses import asdict,dataclass,replace
from pathlib import Path
import httpx
from .models import iso


class ControlError(ValueError):pass


@dataclass
class Preferences:
    coins: list[str]
    interval: int = 300
    window: int = 180
    paused: bool = False
    all_markets: bool = True
    exchanges: list[str] = None
    market_types: list[str] = None
    send_zip: bool = True
    revision: int = 0
    version: int = 1

    def validate(self):
        if not isinstance(self.coins,list) or any(not isinstance(c,str) for c in self.coins):raise ControlError('Coins must be a list of symbols.')
        self.coins=list(dict.fromkeys(c.strip().upper() for c in self.coins))
        if not 1<=len(self.coins)<=20 or any(not re.fullmatch(r'[A-Z0-9]{2,12}',c) for c in self.coins):
            raise ControlError('Use 1–20 coin symbols, e.g. BTC,SOL,DOGE. Keep one coin, or pause the loop.')
        if type(self.interval) is not int or not 300<=self.interval<=86400 or self.interval%60:
            raise ControlError('Scan interval must be whole minutes between 5 and 1440.')
        if type(self.window) is not int or not 90<=self.window<=365:raise ControlError('Research window must be 90–365 days.')
        if any(type(x) is not bool for x in (self.paused,self.all_markets,self.send_zip)):raise ControlError('Invalid switch value.')
        for items,allowed in [(self.exchanges,{'binance','bitget','bybit','okx'}),(self.market_types,{'spot','perpetual'})]:
            if not isinstance(items,list) or not items or any(not isinstance(x,str) or x not in allowed for x in items) or len(items)!=len(set(items)):
                raise ControlError('Select at least one supported exchange and product type.')
        if type(self.revision) is not int or self.revision<0 or type(self.version) is not int or self.version!=1:raise ControlError('Unsupported settings version.')
        return self

    @classmethod
    def from_settings(cls,s):
        return cls(list(s.coins),s.interval,s.window,all_markets=s.enable_market_universe,
                   exchanges=list(s.market_exchanges),market_types=list(s.market_types)).validate()

    @classmethod
    def decode(cls,value):
        try:
            if not isinstance(value,dict) or set(value)!=set(cls.__dataclass_fields__):raise ControlError('Invalid backup schema.')
            return cls(**value).validate()
        except (TypeError,AttributeError) as exc:raise ControlError('Invalid settings data.') from exc


class PreferenceStore:
    def __init__(self,settings):
        self.path=Path(settings.control_state_file)
        self.url=settings.control_redis_url
        self.token=settings.control_redis_token
        identity=settings.telegram_token.split(':')[0]+':'+settings.telegram_chat
        self.key='atlas136:preferences:'+hashlib.sha256(identity.encode()).hexdigest()[:20]
        self.client=httpx.AsyncClient(timeout=12,follow_redirects=False)
        self.mode='durable Redis' if self.url else 'local file (ephemeral on Render free)'

    async def redis(self,command):
        try:
            response=await self.client.post(self.url,json=command,headers={'Authorization':'Bearer '+self.token})
            raw=response.json()
            if response.status_code!=200 or not isinstance(raw,dict) or 'error' in raw or 'result' not in raw:raise ControlError('Settings storage rejected the request.')
            return raw['result']
        except (httpx.HTTPError,ValueError) as exc:
            if isinstance(exc,ControlError):raise
            raise ControlError('Settings storage unavailable; credentials are not logged.') from None

    async def load(self):
        try:
            if self.url:raw=await self.redis(['GET',self.key])
            else:
                def read():
                    if not self.path.exists():return None
                    if self.path.stat().st_size>32768:raise ControlError('Saved settings exceed the size limit.')
                    return self.path.read_text(encoding='utf-8')
                raw=await asyncio.to_thread(read)
            if raw is None:return None
            if not isinstance(raw,str) or len(raw)>32768:raise ControlError('Invalid saved settings.')
            return Preferences.decode(json.loads(raw))
        except (OSError,ValueError) as exc:
            if isinstance(exc,ControlError):raise
            raise ControlError('Saved settings could not be read; automatic scans are held.') from None

    async def save(self,prefs):
        raw=json.dumps(asdict(prefs.validate()),separators=(',',':'))
        if self.url:
            if await self.redis(['SET',self.key,raw])!='OK':raise ControlError('Settings save was not acknowledged.')
            return
        def write():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            with temporary.open('w',encoding='utf-8') as handle:
                handle.write(raw);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,self.path)
        try:await asyncio.to_thread(write)
        except OSError:raise ControlError('Settings could not be saved; change was not accepted.') from None

    async def close(self):await self.client.aclose()


class Controller:
    minimum_scan_spacing=60

    def __init__(self,settings,scanner,store=None):
        self.settings,self.scanner=settings,scanner
        self.state=Preferences.from_settings(settings)
        self.store=store or PreferenceStore(settings)
        self.lock=asyncio.Lock();self.wake=asyncio.Event()
        self.ready=False;self.storage_error=None
        self.pending=False;self.manual_pending=False
        self.in_progress=False;self.active_revision=None
        self.last_started=None;self.next_due=None;self.last_manual=None

    async def initialize(self):
        async with self.lock:
            try:
                saved=await self.store.load()
                if saved:self.state=saved
                else:await self.store.save(self.state)
                self.ready=True;self.storage_error=None
                self.pending=not self.state.paused;self.wake.set()
            except ControlError as exc:
                self.ready=False;self.storage_error=str(exc)

    def is_owner(self,sender):
        owner=self.settings.telegram_owner_id
        if not owner and self.settings.telegram_chat.isdigit():owner=self.settings.telegram_chat
        return bool(owner) and str(sender)==owner

    async def change(self,operation,value=None,expected_revision=None):
        async with self.lock:
            if not self.ready:raise ControlError('Settings storage is not ready. Retry after storage recovers.')
            if expected_revision is not None and expected_revision!=self.state.revision:
                raise ControlError('This control menu is old. Open Controls again to use the latest settings.')
            new=copy.deepcopy(self.state)
            if operation in ('add','remove','replace'):
                coins=value if isinstance(value,list) else re.split(r'[,\s]+',str(value).strip().upper())
                if not coins or any(not re.fullmatch(r'[A-Z0-9]{2,12}',c) for c in coins):raise ControlError('Send symbols such as SOL,DOGE (without USDT pair suffixes).')
                if operation=='add':new.coins=list(dict.fromkeys(new.coins+coins))
                elif operation=='remove':new.coins=[c for c in new.coins if c not in coins]
                else:new.coins=coins
            elif operation in ('interval','window','paused','send_zip','all_markets'):setattr(new,operation,value)
            elif operation in ('exchange','type'):
                attr='exchanges' if operation=='exchange' else 'market_types'
                items=getattr(new,attr)
                setattr(new,attr,[x for x in items if x!=value] if value in items else items+[value])
            elif operation=='restore':new=Preferences.decode(value)
            else:raise ControlError('Unknown control.')
            new.validate()
            if asdict(replace(new,revision=self.state.revision))==asdict(self.state):return 'Already set; no change.'
            new.revision=self.state.revision+1
            try:await self.store.save(new)
            except ControlError as exc:
                # A remote SET timeout can be ambiguous. Reload before any further writes/scans.
                self.ready=False;self.storage_error=str(exc);self.wake.set()
                raise ControlError('Save was not confirmed. Scans are held while settings are reloaded; check Controls.') from None
            self.state=new
            if not new.paused:self.pending=True
            else:self.pending=False;self.manual_pending=False
            self.wake.set()
            return 'Saved. '+('Automatic scans paused; an active scan may finish without sending a report.' if new.paused else 'Changes apply to the next scan; an active scan is kept separate.')

    async def scan_now(self):
        async with self.lock:
            if not self.ready:raise ControlError('Settings storage is not ready.')
            if self.in_progress:return 'A scan is already running. No duplicate scan started.'
            now=time.monotonic()
            if self.last_manual is not None and now-self.last_manual<60:return 'Scan now has a 60-second cooldown.'
            self.last_manual=now;self.manual_pending=True;self.wake.set()
            return 'One scan queued. '+('Automatic loop stays paused.' if self.state.paused else 'The next automatic scan follows your timer.')

    def snapshot(self):
        now=time.monotonic();due=self.next_due
        if self.pending or self.manual_pending:due=max(now,(self.last_started or -1e12)+self.minimum_scan_spacing)
        next_at=iso(time.time()+max(0,due-now)) if self.ready and not self.in_progress and due is not None and (not self.state.paused or self.manual_pending) else None
        report=self.scanner.latest
        return dict(**asdict(self.state),ready=self.ready,storage=self.store.mode,storage_error=self.storage_error,
                    in_progress=self.in_progress,active_revision=self.active_revision,next_scan_at=next_at,
                    settings_pending=not report or report.get('control_revision')!=self.state.revision,
                    report_generated_at=report['generated_at'] if report else None,
                    public_url=self.settings.public_url)

    def backup(self):
        raw=json.dumps(asdict(self.state),separators=(',',':')).encode()
        return base64.urlsafe_b64encode(raw).decode()

    @staticmethod
    def decode_backup(encoded):
        if len(encoded)>6000:raise ControlError('Backup is too large.')
        try:return asdict(Preferences.decode(json.loads(base64.b64decode(encoded,altchars=b'-_',validate=True))))
        except (ValueError,TypeError,UnicodeError):raise ControlError('Invalid backup. Copy the complete /restore command from /backup.') from None

    async def wait(self,seconds=30):
        try:await asyncio.wait_for(self.wake.wait(),timeout=max(.01,min(seconds,30)))
        except TimeoutError:pass

    async def run(self,publish):
        while True:
            self.wake.clear()
            if not self.ready:
                await self.scanner.stop_stream()
                await self.initialize()
                if not self.ready:await self.wait();continue
            async with self.lock:
                state=copy.deepcopy(self.state);now=time.monotonic()
                due=self.last_started is None or self.next_due is None or now>=self.next_due
                wants=self.manual_pending or (not state.paused and (self.pending or due))
                earliest=(self.last_started or -1e12)+self.minimum_scan_spacing
                start=wants and now>=earliest
                if start:
                    manual=self.manual_pending
                    self.manual_pending=False;self.pending=False
                    self.in_progress=True;self.active_revision=state.revision;self.last_started=now
            if not start:
                if state.paused:await self.scanner.stop_stream()
                await self.wait(earliest-now if wants else (self.next_due-now if not state.paused and self.next_due else 30))
                continue
            try:
                await self.scanner.configure(state)
                report=await self.scanner.scan()
                if report:
                    report['control_revision']=state.revision
                    report['send_zip']=state.send_zip
                    if self.ready and self.state.revision==state.revision and (manual or not self.state.paused):await publish(report)
            except asyncio.CancelledError:raise
            except Exception as exc:self.scanner.last_error='Scan failed: '+type(exc).__name__+'; previous snapshot retained'
            finally:
                self.in_progress=False
                self.active_revision=None
                self.next_due=max(self.last_started+state.interval,time.monotonic()+1)
                if self.state.paused:await self.scanner.stop_stream()

    async def close(self):await self.store.close()
