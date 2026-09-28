let technicalCoin=report?.coins?.[0]?.symbol||'BTC',technicalTF='5m',technicalFocus='rsi';
const technicalOpenMethods=new Set();
const showBeforeTechnical=showView,renderBeforeTechnical=render;
const technicalNumber=n=>typeof n==='number'?new Intl.NumberFormat('en-US',{maximumSignificantDigits:6}).format(n):'—';
function technicalStatus(row,pack){return row.status==='ok'&&(Date.now()-new Date(pack.as_of).getTime())/1000>pack.period_seconds+(pack.timeframe==='1d'?7200:120)?'stale':row.status;}
function technicalValue(row){
 const v=row.value;if(v==null)return '—';
 if(row.id==='obv')return technicalNumber(v.change_20_bars);
 if(row.id==='ema')return technicalNumber(v.ema20)+' / '+technicalNumber(v.ema50);
 if(row.id==='macd')return technicalNumber(v.histogram);
 return technicalNumber(v);
}
showView=function(name){
 document.body.classList.toggle('technical-view',name==='technical');
 if(name!=='technical'){showBeforeTechnical(name);return;}
 view=name;document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!==name);
 document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.view===name));
 $('sectionName').textContent='Technical dashboard';$('viewTitle').innerHTML='Momentum. Flow. Trend.<br><span>Five studies, one view.</span>';
 $('viewSubtitle').textContent='RSI, MFI, OBV, EMA and MACD on closed candles, with separate open-interest context.';renderTechnical();
};
render=function(){renderBeforeTechnical();if(view==='technical')renderTechnical();};
function technicalChart(points){
 if(!points?.length)return '<p class="muted">No closed-candle price path available.</p>';
 const prices=points.map(p=>p.close),low=Math.min(...prices),high=Math.max(...prices),span=high-low||1;
 const coords=points.map((p,i)=>`${(10+i*620/Math.max(points.length-1,1)).toFixed(2)},${(125-(p.close-low)/span*110).toFixed(2)}`).join(' ');
 return `<div class="technical-chart-label"><span>$${technicalNumber(high)}</span><strong>$${technicalNumber(prices.at(-1))}</strong></div><svg viewBox="0 0 640 140" role="img" aria-label="Last ${points.length} closed candle prices"><path d="M10 125 H630 M10 70 H630 M10 15 H630" stroke="#233042" fill="none"/><polyline points="${coords}" fill="none" stroke="#8ae7cf" stroke-width="2"/></svg><div class="technical-chart-label"><span>${escape(points[0].time.slice(0,16).replace('T',' '))}</span><span>${escape(points.at(-1).time.slice(0,16).replace('T',' '))} UTC</span></div><small class="muted">Chart range: $${technicalNumber(low)} – $${technicalNumber(high)}</small>`;
}
function renderTechnical(){
 const coins=report?.coins||[],coin=coins.find(c=>c.symbol===technicalCoin),pack=coin?.technical?.[technicalTF];
 $('technicalCoin').innerHTML=coins.map(c=>`<option value="${escape(c.symbol)}">${escape(c.symbol)}</option>`).join('');
 if(!coin)$('technicalCoin').insertAdjacentHTML('beforeend',`<option value="${escape(technicalCoin)}">${escape(technicalCoin)} (pending)</option>`);
 $('technicalCoin').value=technicalCoin;$('technicalTimeframe').value=technicalTF;
 $('technicalChartTitle').textContent=`${technicalCoin} / USD · ${technicalTF}`;
 $('technicalMeta').className='notice';
 $('technicalMeta').textContent=pack?`${pack.source||'Source unavailable'} · Candle end: ${pack.as_of||'unavailable'} · ${pack.bars} bars · History start: ${pack.history_start||'unavailable'}`:`${technicalCoin}: no technical data in this completed report. Add the coin in Telegram Controls and wait for the next scan.`;
 const stale=pack&&pack.rows.some(r=>technicalStatus(r,pack)==='stale');
 if(stale){$('technicalMeta').className='notice error';$('technicalMeta').textContent='STALE CANDLES · '+$('technicalMeta').textContent;}
 $('technicalChart').innerHTML=technicalChart(pack?.chart);
 $('technicalCards').innerHTML=(pack?.rows||[]).map(r=>{const v=r.value,components=v&&typeof v==='object'?Object.entries(v).map(([k,val])=>`<div><span>${escape(k.replaceAll('_',' '))}</span><b>${technicalNumber(val)}</b></div>`).join(''):'';
 return `<article class="panel technical-card ${r.id===technicalFocus?'focused':''}" id="tech-${r.id}"><div class="eyebrow">${escape(r.id)} / ${escape(technicalTF)}</div><div class="technical-heading"><h2>${escape(r.name)}</h2>${badge(technicalStatus(r,pack))}</div><div class="technical-value">${technicalValue(r)}</div><div class="muted">${r.id==='obv'?'20-bar change · ':r.id==='macd'?'Histogram · ':''}${escape(r.unit)}</div>${typeof v==='number'?`<div class="technical-meter"><i style="width:${Math.max(0,Math.min(100,v))}%"></i></div>`:''}<p>${escape(r.reading)}</p><div class="technical-components">${components}</div><details data-study="${r.id}" ${technicalOpenMethods.has(r.id)?'open':''}><summary>Method & limitations</summary><p>${escape(r.method)}</p><p>${escape(r.note)}</p></details></article>`;}).join('');
 $('technicalCards').querySelectorAll('details').forEach(d=>d.addEventListener('toggle',()=>{if(d.open)technicalOpenMethods.add(d.dataset.study);else technicalOpenMethods.delete(d.dataset.study);}));
 const oi=(report?.market_universe?.markets||[]).filter(r=>r.base===technicalCoin&&r.kind==='perpetual'&&r.open_interest!=null).slice(0,4);
 $('technicalOI').innerHTML=oi.map(r=>`<div class="technical-oi-row"><b>${escape(r.exchange)} · ${escape(r.symbol)}</b><span>${technicalNumber(r.open_interest)} ${escape(r.open_interest_unit||'provider units')}</span><small>${escape(r.oi_as_of||'No observation time')} · ${escape(fieldState(r.open_interest,r.oi_as_of,r.oi_status))}</small></div>`).join('');
 if(!oi.length){const m=coin?.metrics?.find(m=>m.id===94),state=m?.status==='ok'&&(Date.now()-new Date(m.as_of).getTime())/1000>=180?'stale':m?.status;$('technicalOI').innerHTML=m?.value!=null?`<p>${technicalNumber(m.value)} ${escape(m.unit)} · ${badge(state)}<br>${escape(m.source)}<br>${escape(m.as_of)}</p>`:'<p class="muted">No eligible OI observation for this symbol. Spot assets have no open interest.</p>';}
 $('technicalWatchlist').innerHTML=coins.map(c=>{const p=c.technical?.[technicalTF];return `<tr><td><button class="button" data-tech-coin="${escape(c.symbol)}">${escape(c.symbol)}</button></td>${['rsi','mfi','obv','ema','macd'].map(id=>{const r=p?.rows.find(x=>x.id===id);return `<td>${r?escape(technicalValue(r)):'—'}${r&&technicalStatus(r,p)!=='ok'?'<small class="technical-status">'+escape(technicalStatus(r,p))+'</small>':''}</td>`;}).join('')}</tr>`;}).join('');
 $('technicalWatchlist').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{technicalCoin=b.dataset.techCoin;renderTechnical();}));
}
$('technicalCoin').addEventListener('change',()=>{technicalCoin=$('technicalCoin').value;renderTechnical();});
$('technicalTimeframe').addEventListener('change',()=>{technicalTF=$('technicalTimeframe').value;renderTechnical();});
$('technicalPerps').addEventListener('click',()=>{$('marketSearch').value=technicalCoin;$('marketKind').value='perpetual';marketPage=0;showView('markets');});
function readTechnicalLink(){const p=new URLSearchParams(location.hash.slice(1)),c=p.get('coin'),tf=p.get('tf'),id=p.get('indicator')||'rsi';if(p.get('view')!=='technical'||!c||!/^[A-Z0-9]{2,12}$/.test(c)||!['5m','1d'].includes(tf)||!['rsi','mfi','obv','ema','macd'].includes(id))return;technicalCoin=c;technicalTF=tf;technicalFocus=id;technicalOpenMethods.add(id);showView('technical');}
window.addEventListener('hashchange',readTechnicalLink);readTechnicalLink();
