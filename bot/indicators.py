"""Book mathematics. Return models use closed, aligned candles; beta is daily.

House choices are recorded at the metric, never presented as vendor measurements.
"""
import warnings
import numpy as np
import pandas as pd
from scipy import stats, optimize
import statsmodels.api as sm
from statsmodels.tsa.stattools import acf, pacf, adfuller, kpss, coint, grangercausalitytests
from .models import Results, iso


def divide(a, b):
    if not np.isfinite(b) or abs(b) < 1e-15:
        raise ValueError('Zero/degenerate denominator')
    return float(a / b)


def beta(y, x, minimum=20):
    if len(x) < minimum:
        raise ValueError(f'Need at least {minimum} aligned conditional observations; have {len(x)}')
    return divide(np.cov(y, x, ddof=1)[0, 1], np.var(x, ddof=1))


def expected_shortfall(returns, q=.95):
    losses = np.sort(-np.asarray(returns))[::-1]
    mass = len(losses) * (1-q)
    count = int(np.floor(mass))
    return float((losses[:count].sum() + (mass-count)*(losses[count] if count < len(losses) else 0))/mass)


def hurst(y):
    sizes, ranges = [], []
    for size in (8, 16, 32, 64):
        values = []
        for start in range(0, len(y)-size+1, size):
            a = y[start:start+size]
            sd = np.std(a)
            if sd > 1e-15:
                z = np.cumsum(a-a.mean())
                values.append((z.max()-z.min())/sd)
        if len(values) >= 2 and np.mean(values) > 0:
            sizes.append(size)
            ranges.append(np.mean(values))
    if len(sizes) < 3:
        raise ValueError('Insufficient nonconstant blocks for rescaled-range fit')
    return float(np.polyfit(np.log(sizes), np.log(ranges), 1)[0])


def garch_forecast(returns):
    # Gaussian QMLE on percent returns for numerical stability. No invented fitted parameters.
    y = (returns-np.mean(returns))*100
    variance = np.var(y)
    if variance < 1e-12:
        raise ValueError('Constant returns cannot identify GARCH')

    def series(params):
        omega, a, b = params
        out = np.empty(len(y)+1)
        out[0] = variance
        for t in range(1, len(out)):
            out[t] = omega+a*y[t-1]**2+b*out[t-1]
        return out

    def loss(params):
        v = series(params)[:-1]
        return float(.5*np.sum(np.log(v)+y*y/v))

    fit = optimize.minimize(loss, [variance*.05, .08, .85], method='SLSQP',
                            bounds=[(1e-8, max(variance*10, 1)), (0, .999), (0, .999)],
                            constraints=[{'type':'ineq', 'fun':lambda p: .999-p[1]-p[2]}],
                            options={'maxiter':150, 'ftol':1e-8})
    if not fit.success or fit.x[1]+fit.x[2] >= 1:
        raise ValueError('GARCH fit did not converge to a stationary solution')
    return {'next_day_volatility':float(np.sqrt(series(fit.x)[-1])/100),
            'omega_percent_squared':float(fit.x[0]), 'alpha':float(fit.x[1]), 'beta':float(fit.x[2])}


def analyze_prices(symbol, histories, window=180, rf_annual=0, now=None, period=86400):
    import time
    now = time.time() if now is None else now
    asset = histories.get(symbol)
    if asset is None or len(asset) < 91:
        r = Results()
        for i in range(1, 81):
            r.missing(i, 'Need at least 91 valid closed UTC daily candles for baseline research')
        if asset is not None and len(asset)>=2:
            meta=dict(source=asset.attrs.get('source','unknown'),as_of=iso(int(asset.index[-1])+period),
                      sample=len(asset)-1,status='ok' if now-(int(asset.index[-1])+period)<=period+(7200 if period==86400 else 120) else 'stale')
            latest=float(asset.close.iloc[-1]/asset.close.iloc[-2]-1)
            r.put(34,latest,'fraction','Last closed daily bar return; limited history',**meta)
            r.put(35,np.log1p(latest),'fraction','Last closed daily log return; limited history',**meta)
            r.put(36,float(asset.close.iloc[-1]/asset.close.iloc[0]-1),'fraction','Cumulative return over available daily history',**meta)
        return r
    source = asset.attrs.get('source', 'unknown')
    cutoff = int(asset.index[-1])+period
    periods_per_year = 365*86400/period
    # Daily observations remain fresh until the next daily release plus a two-hour grace period.
    r = Results(source, iso(cutoff), min(len(asset)-1, window), now-cutoff > period+(7200 if period==86400 else 120))
    selected = asset.tail(window+1)
    y = selected.close.pct_change(fill_method=None).dropna().to_numpy()
    prices = selected.close.to_numpy()
    n = len(y)
    rf = (1+rf_annual)**(1/periods_per_year)-1
    sd = np.std(y, ddof=1)
    mean = np.mean(y)
    wealth = np.r_[1., np.cumprod(1+y)]
    dd = wealth/np.maximum.accumulate(wealth)-1
    values = {34:y[-1], 35:np.log1p(y[-1]), 36:wealth[-1]-1, 37:mean,
              38:np.median(y), 39:sd**2, 40:sd, 41:sd*np.sqrt(periods_per_year),
              44:np.sqrt(np.mean(np.minimum(y, 0)**2)), 45:np.mean(np.minimum(y, 0)**2),
              46:stats.skew(y, bias=True), 47:stats.kurtosis(y, fisher=True, bias=True),
              48:dd[-1], 49:abs(dd.min()), 60:np.quantile(-y, .95, method='linear'),
              61:stats.norm.ppf(.95)*sd-mean, 63:expected_shortfall(y), 66:np.sqrt(np.mean((dd*100)**2))}
    for i,v in values.items():
        unit = 'return²' if i in (39,45) else 'dimensionless' if i in (46,47) else 'percentage points' if i==66 else 'fraction'
        r.put(i, v, unit, 'Single-asset buy-and-hold daily price path; q=95% where applicable; not strategy performance.')
    trough = int(np.argmin(dd))
    peak = int(np.argmax(wealth[:trough+1]))
    recovered = np.where(wealth[trough+1:] >= wealth[peak])[0]
    if dd.min() == 0:
        r.missing(50, 'No drawdown in this sample; recovery time undefined')
    elif len(recovered):
        r.put(50, trough+1+int(recovered[0])-peak, 'days', 'Peak to full recovery of the sample maximum drawdown')
    else:
        r.missing(50, f'Maximum drawdown not recovered; {len(wealth)-1-peak} days since peak (right-censored)')
    v = np.mean(y[:30]**2)
    for ret in y[30:]:
        v = .94*v+.06*ret**2
    r.put(43, np.sqrt(v), 'daily volatility fraction', 'Next-day EWMA forecast; lambda=.94; seed mean squared first 30 returns')
    r.calc(51, lambda: divide(np.mean(y-rf), np.std(y-rf, ddof=1))*np.sqrt(periods_per_year), 'ratio', f'Risk-free annual assumption={rf_annual}; asset buy-and-hold')
    r.calc(52, lambda: divide(mean, np.sqrt(np.mean(np.minimum(y,0)**2)))*np.sqrt(periods_per_year), 'ratio', 'MAR=0; all observations in denominator')
    r.calc(53, lambda: divide(wealth[-1]**(periods_per_year/n)-1, abs(dd.min())), 'ratio', 'Geometric CAGR / maximum drawdown; same daily path')
    r.calc(67, lambda: divide(np.maximum(y,0).sum(), np.maximum(-y,0).sum()), 'ratio', 'Omega threshold=0')
    rng = np.random.default_rng(136)
    simulations = rng.normal(mean, sd, 10000)
    r.put(62, np.sort(-simulations)[int(np.ceil(.95*10000))-1], 'fraction',
          'MODEL ESTIMATE: one-day single-asset Gaussian simple-return simulation; seed=136; S=10000; nearest-rank q=.95', status='model')
    r.put(64, -.30, 'fraction', 'SCENARIO ONLY: fully invested unlevered spot, price falls 30%; not an observation or prediction', status='scenario')
    r.put(65, {'spot_down_10pct':-.10,'spot_down_30pct':-.30,'spot_down_50pct':-.50}, 'fraction',
          'SCENARIOS ONLY: unlevered spot full price revaluation; no probabilities', status='scenario')
    r.calc(68, lambda: np.corrcoef(y[1:],y[:-1])[0,1], 'correlation', 'Pearson lag=1 day')
    r.calc(69, lambda: acf(y, nlags=5, adjusted=False, fft=False).tolist(), 'lags 0–5', 'ACF biased denominator')
    r.calc(70, lambda: pacf(y, nlags=5, method='ywm').tolist(), 'lags 0–5', 'Yule-Walker MLE denominator')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            ad = adfuller(y, regression='c', autolag='AIC')
            kp = kpss(y, regression='c', nlags='auto')
            r.put(72, {'statistic':ad[0],'p_value':ad[1],'lags':ad[2],'observations':ad[3]}, 'test', 'Daily RETURNS; constant; AIC lag selection')
            r.put(73, {'statistic':kp[0],'p_value':kp[1],'lags':kp[2]}, 'test', 'Daily RETURNS; level stationarity; auto bandwidth. Table-bound p-values may be censored.')
            r.put(71, {'adf_rejects_unit_root':bool(ad[1]<.05),'kpss_rejects_stationarity':bool(kp[1]<.05),
                       'first_half_mean':float(np.mean(y[:n//2])), 'second_half_mean':float(np.mean(y[n//2:])),
                       'first_half_sd':float(np.std(y[:n//2],ddof=1)), 'second_half_sd':float(np.std(y[n//2:],ddof=1))},
                  'diagnostic', 'Daily RETURNS, not price levels; complementary tests do not prove stationarity')
        except (ValueError, OverflowError):
            for i in (71,72,73): r.missing(i, 'Stationarity tests undefined for this sample')
    r.calc(74, lambda: divide(y[-1]-np.mean(y[-61:-1]),np.std(y[-61:-1],ddof=1)), 'SD', 'Latest daily RETURN vs prior 60 returns; current observation excluded')
    r.calc(78, lambda: hurst(y), 'exponent', 'Classical R/S on daily returns; blocks 8,16,32,64; nonoverlapping; estimator sensitive to short samples')
    r.calc(79, lambda: garch_forecast(y), 'model parameters', 'Gaussian GARCH(1,1) QMLE; demeaned daily returns; converged stationary fit only', status='model')
    if np.std(y[:60],ddof=1)>1e-15:
        standardized=(y[60:]-y[:60].mean())/np.std(y[:60],ddof=1)
        upper=lower=0.
        for z in standardized:
            upper=max(0.,upper+z-.5)
            lower=min(0.,lower+z+.5)
        r.put(80, {'upper':upper,'lower':lower,'threshold':5,'crossed':upper>5 or lower< -5}, 'diagnostic',
              'House CUSUM: frozen first 60 daily returns; k=.5,h=5 illustrative and UNCALIBRATED, no automatic trade rule', status='model')
    if 'quote_volume' in selected and (selected.quote_volume.iloc[1:] > 0).all():
        r.put(87, np.mean(np.abs(y)/selected.quote_volume.iloc[1:].to_numpy()), '1/USD', 'Mean daily |return| / actual venue VWAP × base volume; single venue')
    btc = histories.get('BTC')
    eth = histories.get('ETH')
    if period!=86400:
        return r  # All beta/benchmark regressions are deliberately daily-only.
    if btc is None:
        return r
    # A venue switch never joins benchmark histories from two venues.
    if btc.attrs.get('source') != source:
        for i in list(range(1,34))+[54,55,56,57,58,59,75,76,77]:
            r.missing(i, 'Benchmark venue differs from asset; cross-venue daily regression withheld')
        return r
    frame = pd.concat({'asset':asset.close,'BTC':btc.close},axis=1).dropna()
    if frame.empty or int(frame.index[-1]) != int(asset.index[-1]):
        for i in list(range(1,34))+[54,55,56,57,58,59,75,76,77]:
            r.missing(i, 'Benchmark latest daily close is not aligned with asset; stale attribution withheld')
        return r
    returns = frame.pct_change(fill_method=None).dropna().tail(window)
    if len(returns)<90 or np.any(np.diff(returns.index.to_numpy()) != 86400):
        return r
    a, b = returns.asset.to_numpy(), returns.BTC.to_numpy()
    count=len(a)
    baseline = dict(source=source+'; benchmark BTC/'+asset.attrs.get('quote','USD'), as_of=iso(int(returns.index[-1])+86400), sample=count)
    try:
        raw=beta(a,b)
        fit=sm.OLS(a,sm.add_constant(b)).fit(cov_type='HAC',cov_kwds={'maxlags':5,'use_correction':True},use_t=False)
    except (ValueError, np.linalg.LinAlgError):
        return r
    corr=np.corrcoef(a,b)[0,1]
    for i,val,unit,note in [
        (1,raw,'beta','Declared market benchmark BTC; not total market'),(2,corr*np.std(a,ddof=1)/np.std(b,ddof=1),'beta','All identity inputs share one aligned window'),
        (3,raw,'beta','BTC benchmark'),(8,beta(a[-90:],b[-90:]),'beta','Rolling window 90 daily returns'),
        (14,.67*raw+.33,'beta','Blume-style .67 raw + .33 × 1'),(16,np.cov(a,b,ddof=1)[0,1],'return²','Sample covariance n−1'),
        (17,corr,'correlation','Pearson'),(18,stats.spearmanr(a,b).statistic,'correlation','Spearman average ranks'),
        (19,stats.kendalltau(a,b,variant='b').statistic,'correlation','Kendall tau-b'),(20,np.corrcoef(a[-90:],b[-90:])[0,1],'correlation','Rolling 90 days'),
        (21,np.corrcoef(a[1:],b[:-1])[0,1],'correlation','Corr(asset_t, BTC_t-1); one day lag'),
        (23,fit.params[0],'daily fraction','BTC OLS intercept'),(24,fit.rsquared,'fraction','In-sample R²'),(25,fit.rsquared_adj,'fraction','k=1'),
        (27,np.std(fit.resid,ddof=1)*np.sqrt(365),'annualized fraction','Sample SD residuals × sqrt(365)'),
        (28,fit.bse[1],'beta SE','HAC Bartlett kernel; maxlags=5; finite-sample correction'),
        (29,fit.tvalues[1],'z-like statistic','H0 beta=0; HAC asymptotic normal reference'),(30,fit.pvalues[1],'probability','Two-sided HAC normal p-value; no multiple-test correction'),
        (31,fit.conf_int(alpha=.05)[1].tolist(),'95% beta interval','HAC asymptotic normal; model uncertainty excluded'),
        (32,np.sqrt(np.mean(fit.resid**2)),'daily fraction','In-sample residual RMSE; not forward prediction accuracy'),
        (33,np.mean(np.abs(fit.resid)),'daily fraction','In-sample residual MAE'),
        (55,(np.mean(a-rf)-raw*np.mean(b-rf))*365,'annualized fraction',f'Jensen BTC benchmark; annual rf assumption={rf_annual}'),
        (57,np.std(a-b,ddof=1)*np.sqrt(365),'annualized fraction','BTC active-return tracking error')]:
        r.put(i,val,unit,note,**baseline)
    for i,mask in [(9,b>0),(10,b<0)]:
        r.calc(i,lambda m=mask:beta(a[m],b[m]),'beta','Conditional sample; minimum=20', **(baseline|{'sample':int(mask.sum())}))
    state=(btc.close-btc.close.rolling(60).mean()).shift(1).reindex(returns.index)
    for i,mask in [(11,state.to_numpy()>0),(12,state.to_numpy()<0)]:
        r.calc(i,lambda m=mask:beta(a[m],b[m]),'beta','Previous close vs previous SMA60; ties unclassified',**(baseline|{'sample':int(mask.sum())}))
    r.calc(13, lambda:beta(a[1:],b[:-1]),'beta','One-day lagged BTC returns',**baseline)
    r.calc(15, lambda:np.std([beta(a[t-60:t],b[t-60:t]) for t in range(60,count+1)],ddof=1),'beta SD','60-day rolling betas across stated daily sample',**baseline)
    prior=sm.OLS(a[:-1],sm.add_constant(b[:-1])).fit()
    r.put(26,a[-1]-prior.predict([1,b[-1]])[0],'daily fraction',f'OUT-OF-SAMPLE latest close; fitted through {iso(int(returns.index[-2])+86400)}',**baseline)
    r.calc(54,lambda:divide(np.mean(a-rf)*365,raw),'ratio','Arithmetic annual excess return / BTC beta',**baseline)
    r.calc(56,lambda:divide(np.mean(a-b),np.std(a-b,ddof=1))*np.sqrt(365),'ratio','BTC active-return information ratio',**baseline)
    for i,mask in [(58,b>0),(59,b<0)]:
        r.calc(i,lambda m=mask:100*divide(np.expm1(np.log1p(a[m]).mean()),np.expm1(np.log1p(b[m]).mean())),
               'percent','Conditional geometric capture',**(baseline|{'sample':int(mask.sum())}))
    if symbol!='BTC':
        logs=np.log(frame.tail(window+1))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                ct=coint(logs.asset,logs.BTC,trend='c',autolag='aic')
                r.put(75,{'statistic':ct[0],'p_value':ct[1]},'test','Engle-Granger on log prices; constant; AIC. I(1) assumptions require independent review.',**baseline)
                spread=sm.OLS(logs.asset,sm.add_constant(logs.BTC)).fit().resid.to_numpy()
                if np.std(spread)<1e-12:raise ValueError('Degenerate residual spread')
                phi=sm.OLS(spread[1:],sm.add_constant(spread[:-1])).fit().params[1]
                if ct[1]<.05 and 0<abs(phi)<1:
                    r.put(76,np.log(.5)/np.log(abs(phi)),'days',f'AR(1) log-price residual spread; phi={phi:.5f}; magnitude decay, not a guaranteed recovery',**baseline)
                else:r.missing(76,'Cointegration/stable decay not supported by selected model')
                gc=grangercausalitytests(np.column_stack([a,b]),[2],verbose=False)[2][0]['ssr_ftest']
                r.put(77,{'F':gc[0],'p_value':gc[1]},'test','BTC → asset; predeclared 2 lags; classical F may fail under heteroskedasticity; not economic causation',**baseline)
            except (ValueError,IndexError,np.linalg.LinAlgError):
                pass
    else:
        for i in (75,76,77):r.missing(i,'BTC against itself is a degenerate test')
    if eth is not None and eth.attrs.get('source')==source:
        f=pd.concat({'asset':asset.close,'BTC':btc.close,'ETH':eth.close},axis=1).dropna().pct_change(fill_method=None).dropna().tail(window)
        if len(f)>=90 and f.index[-1]==asset.index[-1] and np.all(np.diff(f.index)==86400):
            r.calc(4,lambda:beta(f.asset,f.ETH),'beta','ETH benchmark',**(baseline|{'sample':len(f)}))
            X=sm.add_constant(f[['BTC','ETH']])
            if np.linalg.matrix_rank(X)==3:
                model=sm.OLS(f.asset,X).fit()
                r.put(7,dict(model.params),'beta coefficients','Simultaneous BTC and ETH OLS; raw factors; monitor collinearity',**(baseline|{'sample':len(f)}))
                ra=sm.OLS(f.asset,sm.add_constant(f.ETH)).fit().resid
                rb=sm.OLS(f.BTC,sm.add_constant(f.ETH)).fit().resid
                if np.std(ra)>1e-12 and np.std(rb)>1e-12:
                    r.put(22,np.corrcoef(ra,rb)[0,1],'correlation','Asset/BTC correlation controlling ETH',**baseline)
    for i in (5,6):r.missing(i,'Requires a point-in-time documented total-market/sector benchmark; watchlist is not substituted')
    return r
