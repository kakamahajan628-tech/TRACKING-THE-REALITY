"""Eight technical studies on closed, contiguous instrument candles; no trade score.

Seeds and history limits are explicit. These are local calculations, not values
fetched from TradingView. Volume indicators use venue-native base-asset volume.
"""
import time
from .tracking import TIMEFRAMES
import numpy as np
import pandas as pd
from .models import iso
from .providers import validated_candles, DataError

DEFINITIONS=[
    {'id':'rsi','name':'RSI (14)','unit':'0–100','minimum':15,
     'method':'Wilder smoothing: seed mean of first 14 close gains/losses; next average = (13 × previous + change) / 14. RSI = 100 − 100/(1 + avg_gain/avg_loss). Flat gains and losses = 50; no losses = 100.',
     'note':'70/30 are descriptive zones, not automatic reversal or trade signals.'},
    {'id':'mfi','name':'MFI (14)','unit':'0–100','minimum':15,
     'method':'Typical price = (high + low + close)/3. Flow = typical price × base volume. Sum positive/negative flow across 14 price changes; MFI = 100 × positive/(positive + negative). Equal typical prices contribute neither side.',
     'note':'80/20 are descriptive zones. No directional money flow makes MFI unavailable; it is not filled with a neutral score.'},
    {'id':'obv','name':'OBV / 20-bar change','unit':'base-asset volume','minimum':21,
     'method':'OBV starts at zero at the first retained candle. Add volume on a higher close, subtract on a lower close, unchanged on equal closes. Also report OBV[t] − OBV[t−20].',
     'note':'Absolute OBV depends on the retained history start. Compare the 20-bar change; this is not actual buyer/seller trade classification.'},
    {'id':'ema','name':'EMA (20 / 50)','unit':'USDT','minimum':51,
     'method':'Each EMA starts at its first N-close simple mean; then EMA = alpha × close + (1−alpha) × previous, alpha=2/(N+1). Cross checks the last two closed bars.',
     'note':'EMA alignment describes the observed trend. A crossover does not establish future performance.'},
    {'id':'macd','name':'MACD (12 / 26 / 9)','unit':'USDT','minimum':35,
     'method':'MACD = SMA-seeded EMA(12) − SMA-seeded EMA(26). Signal = SMA-seeded EMA(9) of available MACD values. Histogram = MACD − signal. Cross checks the last two closed bars.',
     'note':'Values depend on venue, candle timeframe, history length and initialization; exact TradingView parity is not claimed.'},
    {'id':'atr','name':'ATR (14)','unit':'USDT / percent','minimum':15,
     'method':'True range = max(high-low, abs(high-previous close), abs(low-previous close)); mean seed of 14 ranges, then Wilder smoothing alpha=1/14.',
     'note':'Volatility magnitude, not direction; percentage uses the last closed price.'},
    {'id':'bb','name':'Bollinger Bands (20 / 2)','unit':'USDT / ratio','minimum':20,
     'method':'20-close SMA plus/minus 2 population standard deviations; width=(upper-lower)/middle; percent B=(close-lower)/(upper-lower).',
     'note':'A band touch is not a reversal forecast. Zero width makes percent B unavailable.'},
    {'id':'adx','name':'ADX / +DI / -DI (14)','unit':'0–100','minimum':28,
     'method':'Directional movements use mutually exclusive positive high/low changes; Wilder-smoothed TR and DM over 14; DX=100*abs(+DI-minusDI)/(+DI+minusDI), then Wilder-14 mean seed.',
     'note':'ADX describes directional strength; +DI/-DI provide direction context, not a trade instruction.'},
]


def wilder(values,period=14):
    values=np.asarray(values,dtype=float);out=np.full(len(values),np.nan)
    if len(values)<period:return out
    out[period-1]=np.mean(values[:period])
    for i in range(period,len(values)):out[i]=(out[i-1]*(period-1)+values[i])/period
    return out


def ema(values,period):
    values=np.asarray(values,dtype=float);out=np.full(len(values),np.nan)
    if len(values)<period:return out
    out[period-1]=np.mean(values[:period]);alpha=2/(period+1)
    for i in range(period,len(values)):out[i]=alpha*values[i]+(1-alpha)*out[i-1]
    return out


def rsi(values,period=14):
    changes=np.diff(np.asarray(values,dtype=float));out=np.full(len(values),np.nan)
    if len(changes)<period:return out
    up=np.maximum(changes,0);down=np.maximum(-changes,0)
    gain=float(np.mean(up[:period]));loss=float(np.mean(down[:period]))
    def score():return 50. if gain==loss==0 else 100.*gain/(gain+loss)
    out[period]=score()
    for i in range(period,len(changes)):
        gain=(gain*(period-1)+up[i])/period;loss=(loss*(period-1)+down[i])/period
        out[i+1]=score()
    return out


def position(a,b):
    tolerance=32*np.finfo(float).eps*max(abs(a),abs(b))
    return 0 if abs(a-b)<=tolerance else 1 if a>b else -1


def crossed(a,b):
    before,after=position(a[-2],b[-2]),position(a[-1],b[-1])
    if before<=0 and after>0:return 'crossed above on last closed bar'
    if before>=0 and after<0:return 'crossed below on last closed bar'
    return 'no new cross on last closed bar'


def zone(value,low,high):return 'upper zone' if value>=high else 'lower zone' if value<=low else 'middle zone'


def studies(frame,timeframe,now=None):
    now=time.time() if now is None else now;period=TIMEFRAMES[timeframe]
    pack={'timeframe':timeframe,'period_seconds':period,'source':None,'as_of':None,'history_start':None,
          'bars':0,'quote':'USDT','market':'unavailable','status':'unavailable','rows':[],'chart':[]}
    problem='Closed candles unavailable for this coin and timeframe.'
    if isinstance(frame,pd.DataFrame):
        try:
            # Revalidate so direct callers cannot accidentally include an open bar,
            # a gap, duplicated timestamps or corrupt volume in these calculations.
            if frame.attrs.get('period')!=period:raise DataError('Candle timeframe mismatch')
            source=frame.attrs.get('source')
            if not isinstance(source,str) or not source:raise DataError('Candle provenance missing')
            attributes=dict(frame.attrs)
            frame=validated_candles(frame.reset_index().to_dict('records'),source,period,now).tail(260)
            frame.attrs.update(attributes)
            pack.update(quote=attributes.get('quote','USD'),market=attributes.get('market','spot'),symbol=attributes.get('symbol'),volume_unit=attributes.get('volume_unit','base-asset volume'))
            close=frame.close.to_numpy(dtype=float);volume=frame.volume.to_numpy(dtype=float)
            stamp=int(frame.index[-1])+period
            pack.update(source=source,as_of=iso(stamp),history_start=iso(int(frame.index[0])),bars=len(frame),
                        status='ok' if now-stamp<=period+(7200 if period==86400 else 120) else 'stale')
            pack['chart']=[{'time':iso(int(t)+period),'close':float(c)} for t,c in zip(frame.index[-60:],close[-60:])]
        except (DataError,ValueError,KeyError,TypeError) as exc:
            frame=None;problem='Invalid candle input: '+str(exc)
    else:frame=None
    for definition in DEFINITIONS:
        row={**definition,'status':'unavailable','value':None,'reading':problem,'as_of':pack['as_of'],'source':pack['source']}
        row['frequency']=timeframe
        if frame is not None and frame.attrs.get('derivation'):row['note']+=' '+frame.attrs['derivation']
        if definition['id'] in ('ema','macd'):row['unit']=pack['quote']
        if definition['id']=='atr':row['unit']=pack['quote']+' / percent'
        if definition['id']=='bb':row['unit']=pack['quote']+' / ratio'
        if definition['id']=='obv':row['unit']=pack.get('volume_unit','base-asset volume')
        if frame is not None:
            row['reading']=f"Needs {definition['minimum']} consecutive closed bars; received {len(frame)}."
            if len(frame)>=definition['minimum']:
                key=definition['id'];value=None
                if key=='rsi':value=float(rsi(close)[-1]);reading=zone(value,30,70)
                elif key=='mfi':
                    typical=(frame.high.to_numpy()+frame.low.to_numpy()+close)/3
                    change=np.diff(typical)[-14:];flow=(typical*volume)[-14:]
                    positive=float(flow[change>0].sum());negative=float(flow[change<0].sum())
                    if positive+negative>0:value=100.*positive/(positive+negative);reading=zone(value,20,80)
                    else:row['reading']='No directional money flow in the last 14 bars; MFI is undefined.'
                elif key=='obv':
                    obv=np.r_[0.,np.cumsum(np.sign(np.diff(close))*volume[1:])]
                    delta=float(obv[-1]-obv[-21]);value={'level':float(obv[-1]),'change_20_bars':delta}
                    reading='positive 20-bar change' if delta>0 else 'negative 20-bar change' if delta<0 else 'flat 20-bar change'
                elif key=='ema':
                    fast,slow=ema(close,20),ema(close,50)
                    value={'ema20':float(fast[-1]),'ema50':float(slow[-1]),'close':float(close[-1])}
                    direction=position(fast[-1],slow[-1])
                    reading=('EMA20 above EMA50' if direction>0 else 'EMA20 below EMA50' if direction<0 else 'EMAs equal')+'; '+crossed(fast,slow)
                elif key=='macd':
                    line=(ema(close,12)-ema(close,26))[25:];signal=ema(line,9);hist=line-signal
                    value={'macd':float(line[-1]),'signal':float(signal[-1]),'histogram':float(hist[-1])}
                    direction=position(line[-1],signal[-1])
                    reading=('positive histogram' if direction>0 else 'negative histogram' if direction<0 else 'histogram near zero')+'; '+crossed(line,signal)
                elif key in ('atr','adx'):
                    high=frame.high.to_numpy(dtype=float);low=frame.low.to_numpy(dtype=float)
                    tr=np.maximum(high[1:]-low[1:],np.maximum(abs(high[1:]-close[:-1]),abs(low[1:]-close[:-1])))
                    atr=wilder(tr)
                    if key=='atr':
                        value={'atr':float(atr[-1]),'percent_of_close':float(atr[-1]/close[-1]*100)};reading='Closed-bar volatility magnitude'
                    else:
                        up=np.diff(high);down=-np.diff(low)
                        plus=wilder(np.where((up>down)&(up>0),up,0.));minus=wilder(np.where((down>up)&(down>0),down,0.))
                        if np.all(atr[13:]>0):
                            p=100*plus[13:]/atr[13:];m=100*minus[13:]/atr[13:];den=p+m
                            dx=np.divide(100*np.abs(p-m),den,out=np.zeros_like(den),where=den>0)
                            value={'adx':float(wilder(dx)[-1]),'plus_di':float(p[-1]),'minus_di':float(m[-1])};reading='Directional strength; compare +DI and -DI'
                        else:row['reading']='Zero true range; directional strength undefined.'
                elif key=='bb':
                    middle=float(np.mean(close[-20:]));sd=float(np.std(close[-20:],ddof=0))
                    value={'middle':middle,'upper':middle+2*sd,'lower':middle-2*sd,'width_fraction':4*sd/middle}
                    if sd>0:value['percent_b']=(float(close[-1])-value['lower'])/(4*sd)
                    reading='20-bar bands; zero-width percent B is omitted'
                if value is not None:
                    numbers=list(value.values()) if isinstance(value,dict) else [value]
                    if all(np.isfinite(x) for x in numbers):row.update(value=value,status=pack['status'],reading=reading)
                    else:row['reading']='Calculation overflow; value withheld.'
        pack['rows'].append(row)
    return pack


def technical_pack(daily,intraday,now=None,hourly=None,four_hour=None):
    return {tf:studies(frame,tf,now) for tf,frame in [('5m',intraday),('1h',hourly),('4h',four_hour),('1d',daily)]}


def compact(row):
    value=row['value']
    if value is None:return 'N/A'
    def number(v):return f'{v:.6g}'
    if row['id']=='obv':return 'Δ20 '+number(value['change_20_bars'])
    if row['id']=='ema':return number(value['ema20'])+' / '+number(value['ema50'])
    if row['id']=='macd':return 'hist '+number(value['histogram'])
    if row['id']=='atr':return number(value['atr'])+' ('+number(value['percent_of_close'])+'%)'
    if row['id']=='bb':return number(value['lower'])+' / '+number(value['middle'])+' / '+number(value['upper'])
    if row['id']=='adx':return number(value['adx'])+' | +DI '+number(value['plus_di'])+' / -DI '+number(value['minus_di'])
    if row['id']=='funding':return number(value*100)+'%'
    return number(value)
