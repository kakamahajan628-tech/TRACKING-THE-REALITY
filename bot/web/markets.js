let marketPage=0,selectedMarket=null;
const oldShowView=showView,oldRender=render;
const marketNumber=n=>typeof n==='number'?new Intl.NumberFormat('en-US',{maximumSignificantDigits:8}).format(n):String(n??'—');
showView=function(name){
 if(name!=='markets'){oldShowView(name);oldRender();if(name==='overview')$('viewSubtitle').textContent='All-exchange screening plus 136-metric research for your selected watchlist.';return;}
 view=name;document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!==name);
 document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.view===name));
 $('sectionName').textContent='All exchanges';$('viewTitle').innerHTML='Every listed market.<br><span>Spot and perpetuals.</span>';
 $('viewSubtitle').textContent='Browse the full discovered market list. Each venue, contract and source time stays visible.';renderMarkets();
};
render=function(){oldRender();if(view==='markets')renderMarkets();};
function marketState(m){return m.status==='ok'&&(Date.now()-new Date(m.as_of).getTime())/1000>600?'stale':m.status;}
function pct(v){return v==null?'—':(v>=0?'+':'')+marketNumber(v*100)+'%';}
function fieldState(value,stamp,status){return value==null?'Unavailable':status!=='ok'||(Date.now()-new Date(stamp).getTime())/1000>600?'Stale':'Current';}
function renderMarkets(){
 if(!report)return;const u=report.market_universe,rows=u?.markets||[];
 const current=rows.filter(r=>marketState(r)==='ok').length;
 const stats=[['DISCOVERED INSTRUMENTS',rows.length.toLocaleString(),'Every active listing returned by configured catalogs'],['SPOT / PERPETUAL',`${u?.counts?.spot||0} / ${u?.counts?.perpetual||0}`,'Separate products and settlement currencies'],['FRESH PRICE SNAPSHOTS',current.toLocaleString(),'Snapshot time within 10 minutes of this browser clock'],['ALTCOIN TICKER LABELS',u?.counts?.alt_base_symbols||0,'Not a verified unique-token count']];
 $('stats').innerHTML=stats.map(s=>`<div class="stat"><div class="stat-label">${escape(s[0])}</div><strong>${escape(s[1])}</strong><small>${escape(s[2])}</small></div>`).join('');
 $('marketCoverage').textContent=u?.enabled?`${u.counts.instruments.toLocaleString()} instruments · ${u.counts.spot} spot · ${u.counts.perpetual} perpetual · ${u.counts.alt_base_symbols} altcoin ticker labels · ${u.complete?'Coverage complete for configured catalogs':'PARTIAL COVERAGE — inspect connections below'}`:'All-market scan is disabled or unavailable in this snapshot. Demo mode makes no provider requests.';
 let query=$('marketSearch').value.trim().toLowerCase(),venue=$('marketExchange').value,kind=$('marketKind').value;
 let filtered=rows.filter(r=>(venue==='all'||r.exchange===venue)&&(kind==='all'||r.kind===kind)&&(!query||`${r.symbol} ${r.base} ${r.quote}`.toLowerCase().includes(query)));
 if($('marketSort').value==='move')filtered.sort((a,b)=>(marketState(b)==='ok'&&b.change_24h!=null?Math.abs(b.change_24h):-1)-(marketState(a)==='ok'&&a.change_24h!=null?Math.abs(a.change_24h):-1));
 const pages=Math.max(1,Math.ceil(filtered.length/50));marketPage=Math.min(marketPage,pages-1);
 $('marketCount').textContent=`${filtered.length.toLocaleString()} matching instruments`;
 $('marketPage').textContent=`${marketPage+1} / ${pages}`;$('marketPrev').disabled=marketPage===0;$('marketNext').disabled=marketPage>=pages-1;
 $('marketRows').innerHTML=filtered.slice(marketPage*50,marketPage*50+50).map(r=>`<tr><td><button data-market="${escape(r.id)}">${escape(r.symbol)}</button><div class="muted">${escape(r.exchange)} · ${escape(r.category)}</div></td><td>${marketNumber(r.last)} ${escape(r.price_unit)}<div class="muted">${pct(r.change_24h)} · 24h</div></td><td>${badge(marketState(r))}</td></tr>`).join('')||'<tr><td colspan="3">No matching instruments. Inspect the provider coverage below.</td></tr>';
 $('marketRows').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selectedMarket=b.dataset.market;renderMarketDetail();}));
 let lanes=(u?.lanes||[]).map(l=>`<div class="provider"><div>${escape(l.exchange)} / ${escape(l.category)}<div class="muted">${l.current}/${l.discovered} current at collection · catalog ${escape(l.catalog_as_of||'unavailable')}</div>${(l.issues||[]).map(x=>`<div class="issue">${escape(x)}</div>`).join('')}</div>${badge(l.status)}</div>`).join('');
 lanes+=Object.entries(report.aggregators||{}).map(([name,v])=>`<div class="provider"><div>${escape(name)}<div class="muted">${v.records?.length||0} context records · ${escape(v.fetched_at||'No retrieval')}<br>${escape(v.error||v.note||'')}</div></div>${badge(v.status)}</div>`).join('');
 $('marketProviders').innerHTML=lanes||'<p class="muted">No all-market connections in this snapshot.</p>';
 renderMarketDetail();
}
function renderMarketDetail(){
 const r=report?.market_universe?.markets?.find(x=>x.id===selectedMarket);if(!r){$('marketInspector').innerHTML='<h2>Select a market</h2><p>Every contract retains its own funding period, native units and timestamps. Click any instrument in the list.</p>';return;}
 const fields=[['Product',r.kind],['Price',`${marketNumber(r.last)} ${r.price_unit}`],['24h change',pct(r.change_24h)],['24h volume',`${marketNumber(r.volume_24h)} ${r.volume_unit||''}`],['Spread',`${marketNumber(r.spread_bps)} bps`],['Mark / index',`${marketNumber(r.mark_price)} / ${marketNumber(r.index_price)} ${r.quote}`],['Funding',`${pct(r.funding_rate)} · ${fieldState(r.funding_rate,r.funding_as_of,r.funding_status)}`],['Funding period',r.funding_interval_hours?`${r.funding_interval_hours} hours`:'Not supplied; no 8h assumption'],['Funding meaning',r.funding_kind||'Unavailable'],['Funding observed',r.funding_as_of||'Unavailable'],['Next funding',r.next_funding_at||'Unavailable'],['Open interest',`${marketNumber(r.open_interest)} ${r.open_interest_unit||''}`],['OI value',`${marketNumber(r.open_interest_value)} ${r.oi_value_unit||''}`],['OI observed',`${r.oi_as_of||'Unavailable'} · ${fieldState(r.open_interest,r.oi_as_of,r.oi_status)}`],['Snapshot time',r.as_of||'Unavailable'],['Timestamp meaning',r.timestamp_kind],['Since previous snapshot',`${pct(r.change_since_previous_snapshot)}${r.comparison_seconds?' over '+r.comparison_seconds+' seconds':''}`]];
 let html=`<div class="eyebrow">${escape(r.exchange)} / ${escape(r.category)}</div><h2>${escape(r.symbol)}</h2>${badge(marketState(r))}<dl>${fields.map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('')}</dl>`;
 if(r.tradingview_url?.startsWith('https://www.tradingview.com/symbols/'))html+=`<p><a class="button" href="${escape(r.tradingview_url)}" target="_blank" rel="noopener noreferrer">Find chart on TradingView ↗</a></p><p>Search link only; no TradingView indicator feed.</p>`;
 const cmc=report.aggregators?.coinmarketcap,cg=report.aggregators?.coinglass;
 const refs=(cmc?.records||[]).filter(x=>x.symbol===r.base).slice(0,5);
 if(refs.length){html+='<h3>CoinMarketCap symbol matches</h3><p>Identity not verified against this venue. Check the CMC ID and project name before comparing.</p>';for(const x of refs)html+=`<p>${escape(x.name)} · CMC ID ${escape(x.id)}<br>Market cap: $${marketNumber(x.market_cap_usd)}<br>As of: ${escape(x.as_of||'Not supplied')}<br>Feed: ${escape(cmc.status)}</p>`;}
 const ref=(cg?.records||[]).find(x=>x.symbol===r.base);
 if(ref){html+=`<h3>CoinGlass symbol context</h3><p>Aggregate across the configured provider exchanges, not this contract. Identity requires review. Retrieved ${escape(cg.fetched_at)} · ${escape(cg.status)}. Observation timestamp not supplied.</p><pre>${escape(JSON.stringify(ref.values,null,2))}</pre>`;}
 $('marketInspector').innerHTML=html;
}
['marketSearch','marketExchange','marketKind','marketSort'].forEach(id=>$(id).addEventListener(id==='marketSearch'?'input':'change',()=>{marketPage=0;renderMarkets();}));
$('marketPrev').addEventListener('click',()=>{marketPage=Math.max(0,marketPage-1);renderMarkets();});
$('marketNext').addEventListener('click',()=>{marketPage++;renderMarkets();});
$('exportMarkets').addEventListener('click',()=>{
 if(LIVE){window.location.href='/reports/markets.csv';return;}
 const rows=report?.market_universe?.markets||[],fields=['id','kind','last','price_unit','change_24h','status','as_of','funding_rate','funding_as_of','open_interest','open_interest_unit','oi_as_of'];
 const cell=v=>'"'+String(typeof v==='string'&&/^[=+@\-\t\r]/.test(v)?"'"+v:v??'').replace(/"/g,'""')+'"';
 const data='\ufeff'+[fields,...rows.map(r=>fields.map(k=>r[k]))].map(row=>row.map(cell).join(',')).join('\r\n');
 const url=URL.createObjectURL(new Blob([data],{type:'text/csv'})),a=document.createElement('a');a.href=url;a.download='markets.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('viewSubtitle').textContent='All-exchange screening plus 136-metric research for your selected watchlist.';
