(()=>{
'use strict';

const BM_AUTO_KEY='tp27_benchmark_auto_v1';
const BM_PROFILE_KEY='tp27_benchmark_profile_v1';
const BM_RESULTS_KEY='tp27_benchmark_results_v1';
const BM_PORT=52701;
const profiles={
  quick:{label:'سریع',duration:3,parallel:1,note:'کم‌فشار · مناسب تست خودکار بعد از ساخت/تغییر'},
  standard:{label:'استاندارد',duration:5,parallel:2,note:'دقیق‌تر · مصرف پهنای‌باند متوسط'},
  deep:{label:'عمیق',duration:8,parallel:4,note:'حداکثر فشار کوتاه · فقط تست دستی'},
};
const running=new Set();
const completedJobs=new Set();
let fleetRunning=false;

function q(id){return document.getElementById(id)}
function escb(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function sleepb(ms){return new Promise(r=>setTimeout(r,ms))}
function currentProfile(){const k=localStorage.getItem(BM_PROFILE_KEY)||'quick';return profiles[k]?k:'quick'}
function autoEnabled(){return localStorage.getItem(BM_AUTO_KEY)!=='0'}
function stripCidr(v){return String(v||'').split('/')[0].trim()}
function tunnel(id){try{return (currentStatus.exits||[]).find(x=>x.id===id)||null}catch(_){return null}}
function agentFor(x){try{return (agents||[]).find(a=>a.agent_id===x.agent_id)||null}catch(_){return null}}
function readResults(){try{const v=JSON.parse(localStorage.getItem(BM_RESULTS_KEY)||'{}');return v&&typeof v==='object'?v:{}}catch(_){return{}}}
function saveResult(id,result){const all=readResults();all[id]={...result,saved_at:Date.now()};localStorage.setItem(BM_RESULTS_KEY,JSON.stringify(all));decorateCards();renderCenter()}
function resultFor(id){return readResults()[id]||null}

function qualityLabel(qv){return ({excellent:'عالی',good:'خوب',fair:'متوسط',poor:'ضعیف'})[qv]||'-'}
function qualityClass(qv){return qv==='excellent'||qv==='good'?'bm-good':qv==='fair'?'bm-warn':'bm-bad'}
function fmt(v,d=1){const n=Number(v);return Number.isFinite(n)?n.toFixed(d):'-'}

function injectCss(){
  if(q('tp27BenchmarkStyle'))return;
  const s=document.createElement('style');
  s.id='tp27BenchmarkStyle';
  s.textContent=`
  .bm-btn{border-color:#22d3ee44!important}.bm-btn.bm-running{opacity:.65;pointer-events:none}.bm-strip{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.bm-chip{display:inline-flex;align-items:center;gap:4px;padding:4px 7px;border:1px solid var(--p-border);border-radius:999px;font-size:10px;background:var(--p-surface-2);direction:ltr}.bm-chip b{font-size:10px}.bm-chip.bm-good{border-color:#34d39944;color:#86efac}.bm-chip.bm-warn{border-color:#fbbf2444;color:#fcd34d}.bm-chip.bm-bad{border-color:#fb718544;color:#fda4af}.bm-modal{width:min(980px,96vw)}.bm-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.bm-panel{border:1px solid var(--p-border);border-radius:16px;padding:14px;background:var(--p-surface-2)}.bm-panel h4{margin:0 0 10px}.bm-settings{display:grid;grid-template-columns:1fr 1fr;gap:10px}.bm-settings label{display:flex;flex-direction:column;gap:6px;color:var(--p-muted);font-size:11px}.bm-settings select{min-height:38px;border:1px solid var(--p-border);border-radius:10px;background:var(--p-surface);color:inherit;padding:0 10px}.bm-row{display:grid;grid-template-columns:1fr repeat(4,auto);gap:9px;align-items:center;padding:10px 0;border-bottom:1px solid var(--p-border)}.bm-row:last-child{border-bottom:0}.bm-row strong{font-size:12px}.bm-row span{font-size:11px;direction:ltr}.bm-main-result{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.bm-main-result>div{border:1px solid var(--p-border);border-radius:12px;padding:12px;text-align:center}.bm-main-result small,.bm-main-result b{display:block}.bm-main-result small{color:var(--p-muted);font-size:10px}.bm-main-result b{font-size:17px;margin-top:5px;direction:ltr}.bm-message{min-height:24px;margin-top:9px;color:var(--p-muted);font-size:11px;line-height:1.8}.bm-note{font-size:10px;color:var(--p-muted);line-height:1.8}.bm-switch{display:flex!important;flex-direction:row!important;align-items:center;gap:8px!important}.bm-switch input{width:18px;height:18px}.bm-fleet-list{max-height:360px;overflow:auto}@media(max-width:850px){.bm-grid,.bm-settings{grid-template-columns:1fr}.bm-main-result{grid-template-columns:1fr 1fr}.bm-row{grid-template-columns:1fr 1fr}.bm-modal{width:96vw}}
  `;
  document.head.appendChild(s);
}

function ensureCenter(){
  if(q('tp27BenchmarkModal'))return;
  const m=document.createElement('div');
  m.id='tp27BenchmarkModal';m.className='modal hidden';m.setAttribute('role','dialog');m.setAttribute('aria-modal','true');
  m.innerHTML=`<div class="modal-card bm-modal">
    <div class="section-title modal-head"><div><h3 style="margin:0">تست کیفیت Tunnel</h3><div class="muted tiny" style="margin-top:5px">Ping + Download + Upload روی خود Data Plane داخلی TP27</div></div><button class="btn small" id="bmClose" type="button">×</button></div>
    <div class="bm-grid">
      <section class="bm-panel"><h4>تنظیمات تست</h4><div class="bm-settings">
        <label>پروفایل<select id="bmProfile"><option value="quick">سریع · 3s / 1 stream</option><option value="standard">استاندارد · 5s / 2 stream</option><option value="deep">عمیق · 8s / 4 stream</option></select></label>
        <label class="bm-switch"><input id="bmAuto" type="checkbox"><span>بعد از ساخت یا تعویض موفق، تست سریع خودکار اجرا شود</span></label>
      </div><div id="bmProfileNote" class="bm-note"></div><div class="actions" style="margin-top:12px"><button class="btn primary" id="bmFleet" type="button">تست همه Tunnelهای سالم</button></div><div id="bmFleetMsg" class="bm-message"></div></section>
      <section class="bm-panel"><h4>آخرین نتایج</h4><div id="bmFleetList" class="bm-fleet-list"></div></section>
    </div>
  </div>`;
  document.body.appendChild(m);
  q('bmClose').onclick=()=>m.classList.add('hidden');
  q('bmProfile').onchange=e=>{localStorage.setItem(BM_PROFILE_KEY,e.target.value);syncCenter()};
  q('bmAuto').onchange=e=>localStorage.setItem(BM_AUTO_KEY,e.target.checked?'1':'0');
  q('bmFleet').onclick=runFleet;
}

function ensureResultModal(){
  if(q('tp27BenchmarkResult'))return;
  const m=document.createElement('div');
  m.id='tp27BenchmarkResult';m.className='modal hidden';m.setAttribute('role','dialog');m.setAttribute('aria-modal','true');
  m.innerHTML=`<div class="modal-card manage-card"><div class="section-title modal-head"><h3 id="bmResultTitle">تست Tunnel</h3><button class="btn small" id="bmResultClose" type="button">×</button></div><div id="bmResultBody"></div><div id="bmResultMsg" class="bm-message"></div><div class="actions"><button id="bmRunAgain" class="btn primary" type="button">تست مجدد</button><button id="bmSuggestSwitch" class="btn" type="button">تعویض هوشمند</button></div></div>`;
  document.body.appendChild(m);
  q('bmResultClose').onclick=()=>m.classList.add('hidden');
}

function syncCenter(){
  ensureCenter();
  const k=currentProfile();
  q('bmProfile').value=k;
  q('bmAuto').checked=autoEnabled();
  q('bmProfileNote').textContent=profiles[k].note;
  renderCenter();
}

function renderCenter(){
  const box=q('bmFleetList');if(!box)return;
  let list=[];try{list=currentStatus.exits||[]}catch(_){list=[]}
  const results=readResults();
  if(!list.length){box.innerHTML='<div class="muted">Tunnel وجود ندارد.</div>';return}
  box.innerHTML=list.map(x=>{const r=results[x.id];return `<div class="bm-row"><strong>${escb(x.id)}</strong><span>${r?fmt(r.ping_ms)+' ms':'-'}</span><span>↓ ${r?fmt(r.download_mbps):'-'} Mbps</span><span>↑ ${r?fmt(r.upload_mbps):'-'} Mbps</span><span class="${r?qualityClass(r.quality):''}">${r?qualityLabel(r.quality):(x.healthy?'آماده':'آفلاین')}</span></div>`}).join('');
}

function openCenter(){ensureCenter();syncCenter();q('tp27BenchmarkModal').classList.remove('hidden')}

async function waitCommand(id,timeoutMs=50000){
  const end=Date.now()+timeoutMs;
  while(Date.now()<end){
    const r=await api('/api/agents/commands/'+encodeURIComponent(id));
    if(r.state==='done'){
      if(!r.ok)throw new Error(r.error||'Benchmark روی Agent ناموفق بود');
      return r.result||{};
    }
    if(r.state==='failed')throw new Error(r.error||'Benchmark روی Agent ناموفق بود');
    await sleepb(1000);
  }
  throw new Error('Benchmark timeout');
}

async function runBenchmark(id,opt={}){
  const x=tunnel(id);
  if(!x)throw new Error('Tunnel پیدا نشد');
  if(!x.healthy)throw new Error('فقط Tunnel سالم Benchmark می‌شود');
  if(running.has(id))throw new Error('Benchmark این Tunnel در حال اجراست');
  const a=agentFor(x);
  if(!a||!a.online)throw new Error('Agent سرور خارج آفلاین است');
  const target=stripCidr(x.local_ip);
  const source=stripCidr(x.remote_ip);
  if(!target||!source)throw new Error('IP داخلی Tunnel برای Benchmark کامل نیست');
  const profileKey=opt.profile||currentProfile();
  const profile=profiles[profileKey]||profiles.quick;
  running.add(id);decorateCards();
  try{
    const created=await api('/api/agents/'+encodeURIComponent(x.agent_id)+'/commands',{method:'POST',body:JSON.stringify({action:'status',args:{benchmark_v1:true,id:id,target_ip:target,source_ip:source,port:BM_PORT,duration:profile.duration,parallel:profile.parallel}})});
    const r=await waitCommand(created.command_id,Math.max(45000,(profile.duration*2+25)*1000));
    if(!r.benchmark_v1){throw new Error('Agent فعلی Benchmark را پشتیبانی نمی‌کند؛ Agent این سرور را Upgrade کنید')}
    saveResult(id,r);
    return r;
  }finally{running.delete(id);decorateCards()}
}

function resultHtml(id,r){
  if(!r)return '<div class="muted">هنوز تستی ثبت نشده است.</div>';
  return `<div class="bm-main-result"><div><small>Ping</small><b>${fmt(r.ping_ms)} ms</b></div><div><small>Packet Loss</small><b>${fmt(r.loss_pct)}%</b></div><div><small>Download</small><b>${fmt(r.download_mbps)} Mbps</b></div><div><small>Upload</small><b>${fmt(r.upload_mbps)} Mbps</b></div></div><div class="bm-chip ${qualityClass(r.quality)}">کیفیت <b>${qualityLabel(r.quality)}</b></div><div class="bm-note" style="margin-top:10px">این تست بین IPهای داخلی Tunnel اجرا می‌شود و اینترنت عمومی را Benchmark نمی‌کند؛ بنابراین کیفیت واقعی Data Plane ایران ↔ خارج را نشان می‌دهد.</div>`;
}

async function openAndRun(id){
  ensureResultModal();
  q('bmResultTitle').textContent='تست کیفیت · '+id;
  q('bmResultBody').innerHTML=resultHtml(id,resultFor(id));
  q('bmResultMsg').textContent='';
  q('tp27BenchmarkResult').classList.remove('hidden');
  q('bmRunAgain').onclick=()=>openAndRun(id);
  q('bmSuggestSwitch').onclick=()=>{
    const card=document.querySelector(`#exitList .tunnel-card[data-exit-id="${CSS.escape(id)}"]`);
    const btn=card&&card.querySelector('.premium-rebuild-btn');
    if(btn){q('tp27BenchmarkResult').classList.add('hidden');btn.click()}else{q('bmResultMsg').textContent='برای تعویض هوشمند از دکمه همان Tunnel استفاده کنید.'}
  };
  q('bmRunAgain').disabled=true;
  q('bmResultMsg').textContent='در حال تست Ping، Download و Upload...';
  try{
    const r=await runBenchmark(id);
    q('bmResultBody').innerHTML=resultHtml(id,r);
    q('bmResultMsg').textContent=r.quality==='poor'?'کیفیت ضعیف است؛ قبل از تعویض Transport یک تست مجدد بگیرید.':'Benchmark با موفقیت کامل شد.';
  }catch(e){q('bmResultMsg').textContent='خطا: '+(e.message||e)}finally{q('bmRunAgain').disabled=false}
}

function decorateCards(){
  let list=[];try{list=currentStatus.exits||[]}catch(_){return}
  const cards=[...document.querySelectorAll('#exitList .tunnel-card')];
  const results=readResults();
  cards.forEach((card,i)=>{
    const x=list[i];if(!x)return;
    card.dataset.exitId=x.id||'';
    const actions=card.querySelector('.tunnel-actions');
    if(actions&&!actions.querySelector('.bm-btn')){
      const b=document.createElement('button');b.className='btn small bm-btn';b.type='button';b.textContent='سرعت';b.onclick=()=>openAndRun(x.id);actions.appendChild(b);
    }
    const b=actions&&actions.querySelector('.bm-btn');if(b){b.classList.toggle('bm-running',running.has(x.id));b.textContent=running.has(x.id)?'در حال تست...':'سرعت'}
    const main=card.querySelector('.exit-main');if(!main)return;
    let strip=main.querySelector('.bm-strip');if(!strip){strip=document.createElement('div');strip.className='bm-strip';main.appendChild(strip)}
    const r=results[x.id];
    strip.innerHTML=r?`<span class="bm-chip ${qualityClass(r.quality)}"><b>${qualityLabel(r.quality)}</b></span><span class="bm-chip">Ping <b>${fmt(r.ping_ms)} ms</b></span><span class="bm-chip">↓ <b>${fmt(r.download_mbps)} Mbps</b></span><span class="bm-chip">↑ <b>${fmt(r.upload_mbps)} Mbps</b></span>`:`<span class="bm-chip">Benchmark <b>${x.healthy?'آماده':'آفلاین'}</b></span>`;
  });
}

async function runFleet(){
  if(fleetRunning)return;
  fleetRunning=true;q('bmFleet').disabled=true;
  try{
    let list=[];try{list=(currentStatus.exits||[]).filter(x=>x.healthy)}catch(_){list=[]}
    const key=currentProfile()==='deep'?'standard':currentProfile();
    for(let i=0;i<list.length;i++){
      q('bmFleetMsg').textContent=`در حال تست ${i+1} از ${list.length}: ${list[i].id}`;
      try{await runBenchmark(list[i].id,{profile:key})}catch(e){q('bmFleetMsg').textContent=`${list[i].id}: ${e.message||e}`}
      await sleepb(700);
    }
    q('bmFleetMsg').textContent='تست Tunnelهای سالم تمام شد.';
  }finally{fleetRunning=false;q('bmFleet').disabled=false;renderCenter()}
}

async function autoAfterJob(jobResult,jobUrl){
  if(!autoEnabled()||!jobResult||jobResult.state!=='done')return;
  const jid=String(jobUrl||'');if(completedJobs.has(jid))return;completedJobs.add(jid);
  const id=jobResult.result&&jobResult.result.id;
  if(!id)return;
  await sleepb(1400);
  try{await refreshAll();await sleepb(500);const x=tunnel(id);if(x&&x.healthy)await runBenchmark(id,{profile:'quick'})}catch(e){console.warn('TP27 auto benchmark:',e)}
}

function wrapApiForJobs(){
  if(typeof api!=='function'||api.__benchmarkWrapped)return;
  const base=api;
  const wrapped=async function(url,opt={}){const r=await base.apply(this,arguments);if(String(url).startsWith('/api/jobs/'))autoAfterJob(r,url);return r};
  wrapped.__benchmarkWrapped=true;
  api=wrapped;
}

function enhanceCommandCenter(){
  const actions=document.querySelector('#premiumCommand .premium-actions');
  if(actions&&!q('premiumBenchmarkBtn')){const b=document.createElement('button');b.id='premiumBenchmarkBtn';b.className='btn';b.type='button';b.textContent='تست سرعت';b.onclick=openCenter;actions.insertBefore(b,q('premiumIssuesBtn')||null)}
}

function wrapRefresh(){
  if(typeof refreshAll!=='function'||refreshAll.__benchmarkWrapped)return;
  const base=refreshAll;
  const wrapped=async function(){try{return await base.apply(this,arguments)}finally{setTimeout(()=>{enhanceCommandCenter();decorateCards();renderCenter()},40)}};
  wrapped.__benchmarkWrapped=true;
  refreshAll=wrapped;
}

function boot(){
  injectCss();ensureCenter();ensureResultModal();wrapApiForJobs();wrapRefresh();
  setTimeout(()=>{enhanceCommandCenter();decorateCards();syncCenter()},150);
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
