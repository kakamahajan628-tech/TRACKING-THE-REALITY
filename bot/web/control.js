let loopState=null;
const controlTime=value=>value.slice(0,19).replace('T',' ')+' UTC';
function renderControl(){
 if(!LIVE)return;
 const box=$('controlStatus');box.hidden=false;
 if(!loopState){box.textContent='Connecting to Telegram controls…';return;}
 const s=loopState,pending=report?.control_revision!==s.revision;
 box.innerHTML=`<div><span class="eyebrow">TELEGRAM CONTROLLED</span><strong>${s.ready?(s.paused?'Loop paused':s.in_progress?'Scanning now':'Loop active'):'Settings storage unavailable'}</strong><span>${s.interval/60} min timer · ${s.coins.length}/20 research coins · Auto ZIP ${s.send_zip?'on':'off'}</span></div><div><b>${s.coins.map(escape).join(' · ')}</b><span>${s.next_scan_at?'Next scan: '+escape(controlTime(s.next_scan_at)):s.in_progress?'Current scan is finishing':s.paused?'Use Scan now for a single report':'Waiting for scheduler'}</span><span>${pending?'Settings changed. Displayed data is the last completed snapshot; a matching scan is pending.':'Settings match this report.'}</span><small>Change coins, timer and coverage in Telegram → Controls. Save: ${escape(s.storage)}${s.storage_error?' · '+escape(s.storage_error):''}</small></div>`;
}
async function refreshControl(){
 try{const response=await fetch('/api/control',{cache:'no-store'});if(!response.ok)throw new Error('status');loopState=await response.json();renderControl();}
 catch(error){$('controlStatus').hidden=false;$('controlStatus').textContent='Control status unavailable. Displayed report retains its original timestamps.';}
}
// Links from Telegram select the exact symbol and ID. An absent symbol must never
// silently display another coin's metric while its first scan is pending.
function readMetricLink(){
 const p=new URLSearchParams(location.hash.slice(1)),coin=p.get('coin'),metric=Number(p.get('metric'));
 if(p.get('view')!=='metrics'||!coin||!/^[A-Z0-9]{2,12}$/.test(coin)||!Number.isInteger(metric)||metric<1||metric>136)return;
 selectedCoin=coin;selectedMetric=metric;$('search').value='';$('statusSelect').value='all';showView('metrics');
}
const renderBeforeControls=render;
render=function(){renderBeforeControls();renderControl();};
window.addEventListener('hashchange',readMetricLink);
readMetricLink();renderControl();
if(LIVE){refreshControl();setInterval(refreshControl,10000);}
