import asyncio
import time
import json
import httpx
from .reports import summary, report_bundle
from . import telegram_markets, telegram_controls as controls
from .control import ControlError
from . import telegram_technical


class Telegram:
    def __init__(self,token,chat_id,scanner,controller=None):
        self.token=token
        self.chat_id=str(chat_id)
        self.scanner=scanner
        self.client=httpx.AsyncClient(timeout=45)
        self.offset=0
        self.last_sent=None
        self.status='not configured' if not token else 'starting'
        self.send_lock=asyncio.Lock()
        self.command_at=0
        self.controller=controller
        self.pending_inputs={}

    def navigation(self):
        markup=telegram_markets.menu()
        if self.controller:markup['inline_keyboard']=controls.home_buttons(self.scanner.settings)+markup['inline_keyboard']
        return markup

    def eligible(self,report):
        return not self.controller or (self.controller.ready and report.get('control_revision')==self.controller.state.revision)

    async def request(self,method,data=None,files=None):
        # Exception text from httpx can include the bot token in its URL. Never log it.
        for attempt in range(3):
            try:
                response=await self.client.post(f'https://api.telegram.org/bot{self.token}/{method}',data=data,files=files)
                body=response.json()
                if body.get('ok'):
                    self.status='connected'
                    return body.get('result')
                if body.get('error_code')==429:
                    await asyncio.sleep(min(body.get('parameters',{}).get('retry_after',5),60))
                    continue
                self.status=f"Telegram rejected {method}: code {body.get('error_code',response.status_code)}"
                return None
            except (httpx.HTTPError,ValueError):
                # Retrying an ambiguous send can duplicate a delivered message. Do not retry sends.
                self.status=f'Telegram {method} transport failure; delivery may be unknown'
                if method!='getUpdates':return None
                await asyncio.sleep(2**attempt)
        return None

    async def text(self,text,reply_markup=None):
        # Telegram's 4096 limit applies to UTF-16 code units. Keep conservative chunks.
        lines=text.splitlines(keepends=True)
        chunks=[]; current=''
        for line in lines:
            while len(line.encode('utf-16-le'))//2>3500:
                lineslice=line[:1500];line=line[1500:]
                if current:chunks.append(current);current=''
                chunks.append(lineslice)
            if len((current+line).encode('utf-16-le'))//2>3500:chunks.append(current);current=''
            current+=line
        if current:chunks.append(current)
        for i,chunk in enumerate(chunks):
            data={'chat_id':self.chat_id,'text':chunk}
            if reply_markup and i==len(chunks)-1:data['reply_markup']=json.dumps(reply_markup)
            if await self.request('sendMessage',data) is None:return False
        return True

    async def publish(self,report,force=False):
        if not self.token or self.scanner.settings.demo:return False
        async with self.send_lock:
            if not force and not self.eligible(report):return False
            if not force and self.last_sent==report['scan_id']:return False
            # Mark attempted to avoid duplicate reports after ambiguous transport errors.
            if not force:self.last_sent=report['scan_id']
            if not await self.text(summary(report),self.navigation()):return False
            if not force and (not report.get('send_zip',True) or not self.eligible(report)):return True
            payload=await asyncio.to_thread(report_bundle,report)
            if not force and not self.eligible(report):return False
            result=await self.request('sendDocument',{'chat_id':self.chat_id,'caption':'Scan: all-exchange spot/perpetual snapshots + 136 records per research-watchlist coin. ZIP includes HTML, CSV and JSON. Missing and stale data stay labelled.'},
                                      {'document':(f"atlas-136-{report['scan_id']}.zip",payload,'application/zip')})
            return result is not None

    async def poll(self):
        if not self.token:return
        me=await self.request('getMe')
        if not me:return
        hook=await self.request('getWebhookInfo')
        if hook and hook.get('url'):
            self.status='Existing Telegram webhook detected; polling disabled. Use a dedicated bot or remove its webhook.'
            return
        # Skip pre-start commands; never replay queued historical commands after a redeploy.
        pending=await self.request('getUpdates',{'offset':-1,'timeout':0})
        if pending:self.offset=max(u['update_id'] for u in pending)+1
        commands=[('controls','Coins, timer and pause/resume'),('technical','RSI MFI OBV EMA MACD'),('access','Provider connection health'),('metrics','Browse all 136 metric IDs'),('report','Download complete report'),('markets','Browse exchange instruments'),('website','Open dashboard'),('status','Scanner and settings status'),('backup','Back up settings'),('help','Commands and guidance')]
        await self.request('setMyCommands',{'commands':json.dumps([{'command':c,'description':d} for c,d in commands])})
        while True:
            updates=await self.request('getUpdates',{'offset':self.offset,'timeout':25,'allowed_updates':'["message","callback_query"]'})
            if updates is None:
                await asyncio.sleep(10)
                continue
            for update in updates:
                self.offset=update['update_id']+1
                if 'callback_query' in update:
                    await self.callback(update['callback_query'])
                    continue
                await self.handle_message(update.get('message',{}))

    def require_owner(self,sender):
        if not self.controller:raise ControlError('Controls are not configured.')
        if not self.controller.is_owner(sender):
            raise ControlError('Only the configured owner can change settings. In a group, set TELEGRAM_OWNER_ID once on Render; /whoami shows your user ID.')

    async def backup(self):
        await self.text('Save this settings backup. It contains no API keys. Restore by sending the complete command below:\n\n/restore '+self.controller.backup())

    async def prompt(self,action,sender):
        self.require_owner(sender)
        if action not in ('add','replace','interval'):raise ControlError('Unknown input.')
        revision=self.controller.state.revision
        question='Reply to this message with minutes (5–1440).' if action=='interval' else 'Reply to this message with coin symbols, e.g. SOL,DOGE. Maximum 20 research coins. /cancel cancels.'
        result=await self.request('sendMessage',{'chat_id':self.chat_id,'text':question,'reply_markup':json.dumps({'force_reply':True,'selective':False})})
        if result:self.pending_inputs[str(sender)]={'action':action,'revision':revision,'message_id':result['message_id'],'expires':time.monotonic()+300}

    async def handle_message(self,msg):
        if str(msg.get('chat',{}).get('id'))!=self.chat_id:return
        raw=msg.get('text','').strip();sender=msg.get('from',{}).get('id');report=self.scanner.latest
        if not raw:return
        try:
            if not raw.startswith('/'):
                pending=self.pending_inputs.get(str(sender))
                if not pending:return
                self.require_owner(sender)
                if time.monotonic()>pending['expires']:
                    self.pending_inputs.pop(str(sender),None)
                    raise ControlError('Input expired. Open Controls again.')
                if msg.get('reply_to_message',{}).get('message_id')!=pending['message_id']:return
                value=int(raw)*60 if pending['action']=='interval' else raw
                result=await self.controller.change(pending['action'],value,pending['revision'])
                self.pending_inputs.pop(str(sender),None)
                await self.text(result,self.navigation());return
            parts=raw.split();cmd=parts[0].split('@')[0].lower();argument=' '.join(parts[1:])
            coins=self.controller.state.coins if self.controller else self.scanner.settings.coins
            if cmd in ('/technical','/ta','/rsi','/mfi','/obv','/ema','/macd'):
                if not argument and cmd in ('/technical','/ta'):await self.text(*telegram_technical.choose(coins))
                else:
                    symbol=parts[1].upper() if len(parts)>1 else coins[0]
                    timeframe=parts[2].lower() if len(parts)>2 else '5m'
                    await self.text(*telegram_technical.page(report,self.scanner.settings,symbol,timeframe,None if cmd in ('/technical','/ta') else cmd[1:]))
                return
            if cmd=='/access':await self.text(*telegram_technical.access(report));return
            if cmd in ('/start','/help'):
                await self.text('NEW: /technical → choose coin → 5m / 1d\nShortcuts: /rsi SOL 5m · /mfi SOL 1d · /obv SOL 5m · /ema SOL 5m · /macd SOL 5m\n/access — connection health from the bot host')
                await self.text('ATLAS 136\n/controls — coins, timer, pause/resume, scan now\n/metrics — browse 136 IDs with buttons\n/report — latest digest + complete ZIP\n/markets /perps — exchange listings\n/coin SOL — research digest\n/metric 114 SOL — metric and website link\n/website — dashboard\n/status — loop and data status\n\nOwner shortcuts:\n/add SOL,DOGE · /remove DOGE\n/interval 10 (minutes) · /window 180 (days)\n/pause · /resume · /scan\n/scope all or watchlist · /zip on or off\n/backup · /restore <backup> · /cancel\n\nButtons remember settings. Scans run automatically while the service is running. All 136 IDs remain visible; missing data is labelled. No trade orders are placed.',self.navigation())
            elif cmd in ('/controls','/coins','/panel') and self.controller:
                await self.text(*controls.menu(self.controller,'coins' if cmd=='/coins' else 'home'))
            elif cmd=='/whoami':await self.text(f'Your Telegram user ID: {sender}\nConfigured chat: {self.chat_id}')
            elif cmd=='/status':
                text=f"Scanner: {'collecting' if self.scanner.lock.locked() else 'idle'}\nLast report: {report['generated_at'] if report else 'none'}\nTelegram: {self.status}\nLast error: {self.scanner.last_error or 'none'}"
                if self.controller:text+='\n\n'+controls.menu(self.controller)[0]
                await self.text(text,self.navigation())
            elif cmd=='/website':
                url=controls.dashboard_url(self.scanner.settings)
                await self.text('Open the dashboard below. Login: atlas; password: your DASHBOARD_TOKEN set on Render.' if url else 'Dashboard URL is not configured yet. Render supplies it automatically after deployment, or set PUBLIC_BASE_URL.',{'inline_keyboard':[[{'text':'Open dashboard','url':url}]]} if url else None)
            elif cmd=='/metrics':await self.text(*(controls.metric_list(report,argument.upper()) if argument else controls.metric_coins(coins)))
            elif cmd=='/metric':
                await self.text(*controls.metric_detail(report,self.scanner.settings,parts[2].upper() if len(parts)>2 else coins[0],int(parts[1])))
            elif cmd in ('/add','/remove','/interval','/window','/pause','/resume','/scan','/scope','/zip','/backup','/restore','/cancel'):
                self.require_owner(sender)
                if cmd=='/backup':await self.backup();return
                if cmd=='/cancel':self.pending_inputs.pop(str(sender),None);await self.text('Input cancelled.');return
                if cmd=='/scan':result=await self.controller.scan_now()
                else:
                    operation=cmd[1:];value=argument
                    if cmd in ('/pause','/resume'):operation='paused';value=cmd=='/pause'
                    elif cmd=='/interval':value=int(argument)*60
                    elif cmd=='/window':value=int(argument)
                    elif cmd=='/scope':
                        if argument not in ('all','watchlist'):raise ControlError('Use /scope all or /scope watchlist.')
                        operation='all_markets';value=argument=='all'
                    elif cmd=='/zip':
                        if argument not in ('on','off'):raise ControlError('Use /zip on or /zip off.')
                        operation='send_zip';value=argument=='on'
                    elif cmd=='/restore':value=self.controller.decode_backup(argument)
                    result=await self.controller.change(operation,value)
                await self.text(result,self.navigation())
            elif not report:await self.text('First scan is still collecting data. Try /status shortly.',self.navigation())
            elif cmd=='/report':await self.publish(report,force=True)
            elif cmd in ('/markets','/perps'):await self.text(*telegram_markets.page(report,'perpetual' if cmd=='/perps' else 'all'))
            elif cmd=='/coin':
                symbol=argument.upper() or coins[0]
                await self.text(summary(report,symbol) if any(c['symbol']==symbol for c in report['coins']) else 'No completed report for this coin yet.',self.navigation())
            else:await self.text('Unknown command. Use /help.')
        except ControlError as exc:await self.text(str(exc),self.navigation())
        except (ValueError,IndexError,StopIteration):await self.text('Invalid input. Use /help for examples; metric IDs are 1–136 and intervals are whole minutes.')

    async def callback(self,query):
        msg=query.get('message',{})
        if str(msg.get('chat',{}).get('id'))!=self.chat_id:
            await self.request('answerCallbackQuery',{'callback_query_id':query['id'],'text':'This chat is not authorized.'})
            return
        await self.request('answerCallbackQuery',{'callback_query_id':query['id']})
        report=self.scanner.latest
        try:
            data=query.get('data','')
            parts=data.split(':');sender=query.get('from',{}).get('id')
            if parts[0]=='ctl' and self.controller:
                action=parts[1];section='home';notice=''
                if action in ('home','coins','timer','coverage','research'):section=action
                else:
                    self.require_owner(sender)
                    if action=='input':await self.prompt(parts[2],sender);return
                    if action=='backup':await self.backup();return
                    if action=='scan':notice=await self.controller.scan_now()
                    elif action=='remove':notice=await self.controller.change('remove',parts[2],int(parts[3]));section='coins'
                    elif action in ('exchange','type'):notice=await self.controller.change(action,parts[2],int(parts[3]));section='coverage'
                    elif action=='set':
                        field=parts[2];value=int(parts[3])
                        if field in ('paused','send_zip','all_markets'):
                            if value not in (0,1):raise ControlError('Invalid switch.')
                            value=bool(value)
                        elif field not in ('interval','window'):raise ControlError('Unknown setting.')
                        notice=await self.controller.change(field,value,int(parts[4]))
                        section={'interval':'timer','window':'research','send_zip':'research','all_markets':'coverage'}.get(field,'home')
                    else:return
                text,markup=controls.menu(self.controller,section)
                if notice:text=notice+'\n\n'+text
            elif parts[0]=='tech':
                if parts[1]=='coins':text,markup=telegram_technical.choose(self.controller.state.coins if self.controller else self.scanner.settings.coins)
                else:text,markup=telegram_technical.page(report,self.scanner.settings,parts[1],parts[2],parts[3] if len(parts)>3 else None)
            elif data=='access':text,markup=telegram_technical.access(report)
            elif parts[0]=='research':
                if parts[1]=='coins':text,markup=controls.metric_coins(self.controller.state.coins if self.controller else self.scanner.settings.coins)
                elif parts[1]=='list':text,markup=controls.metric_list(report,parts[2],int(parts[3]))
                elif parts[1]=='metric':text,markup=controls.metric_detail(report,self.scanner.settings,parts[2],int(parts[3]))
                else:return
            elif not report:await self.text('Waiting for the first completed scan.',self.navigation());return
            elif data=='marketzip':
                await self.publish(report,force=True);return
            elif parts[0]=='markets' and len(parts)==3:
                text,markup=telegram_markets.page(report,parts[1],int(parts[2]))
            elif parts[0]=='instrument' and len(parts)==3:
                text,markup=telegram_markets.detail(report,parts[1],int(parts[2]))
            else:return
            if len(text.encode('utf-16-le'))//2>3500:await self.text(text,markup);return
            await self.request('editMessageText',{'chat_id':self.chat_id,'message_id':msg['message_id'],
                'text':text,'reply_markup':json.dumps(markup)})
        except ControlError as exc:await self.text(str(exc),self.navigation())
        except (ValueError,KeyError,IndexError):return

    async def close(self):await self.client.aclose()
