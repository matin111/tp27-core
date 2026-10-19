(()=>{
'use strict';
const THEME_KEY='tp27_ui_theme';
const BUILDER_KEY='tp27_builder_open';
const AUTOHEAL_KEY='tp27_auto_heal';
const EVENT_KEY='tp27_health_events_v1';
const NOTICE_KEY='tp27_notice_enabled';
let issuesOnly=false;
let searchQuery='';
let lastSnapshot={agents:{},exits:{}};
let failStreak=new Map();
let repairCooldown=new Map();
let repairing=new Set();
let rebuilding=new Set();

function p(id){return document.getElementById(id)}
function faTime(){try{return new Intl.DateTimeFormat('fa-IR',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())}catch(_){return new Date().toLocaleTimeString()}}
function faDateTime(ts=Date.now()){try{return new Intl.DateTimeFormat('fa-IR',{dateStyle:'short',timeStyle:'medium'}).format(new Date(ts))}catch(_){return new Date(ts).toLocaleString()}}
function safeStatus(){try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}}
function safeAgents(){try{return agents||[]}catch(_){return[]}}
function autoHealEnabled(){return localStorage.getItem(AUTOHEAL_KEY)==='1'}
function noticeEnabled(){return localStorage.getItem(NOTICE_KEY)==='1'}
function transportLabel(x){
  const t=String((x&&x.transport)||'').toLowerCase();
  const m=String((x&&x.mode)||'').toLowerCase();
  if(t==='gre'||m==='gre-native')return 'GRE Native · Kernel';
  if(t==='gre-fou'||m==='gre-fou')return 'GRE/UDP FOU · Kernel';
  if(t==='openvpn-tcp')return 'OpenVPN P2P/TCP';
  if(t==='gost-wss')return 'GOST/WSS';
  if(t==='framed-tls')return 'Framed TLS/TCP';
  if(m==='reverse-fast-pool')return 'Reverse Fast Pool';
  if(m==='fast-pool')return 'Fast Pool';
  return (x&&x.transport)||(x&&x.mode)||'Auto';
}

function injectOpsCss(){
  if(p('tp27OpsStyle'))return;
  const style=document.createElement('style');
  style.id='tp27OpsStyle';
  style.textContent=`
  .premium-actions{align-items:center}.ops-btn-on{border-color:#34d39966!important;background:#123329!important;color:#a7f3d0!important}.ops-btn-busy{opacity:.65;pointer-events:none}.ops-modal{width:min(1040px,96vw)}.ops-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;margin-top:14px}.ops-panel{border:1px solid var(--p-border);background:var(--p-surface-2);border-radius:16px;padding:14px}.ops-panel h4{margin:0 0 10px}.ops-event-list{display:flex;flex-direction:column;gap:8px;max-height:420px;overflow:auto}.ops-event{display:grid;grid-template-columns:10px 1fr auto;gap:9px;align-items:start;padding:10px 11px;border:1px solid var(--p-border);border-radius:12px;background:var(--p-surface)}.ops-event i{width:8px;height:8px;border-radius:50%;margin-top:5px;background:var(--p-good)}.ops-event.warn i{background:var(--p-warn)}.ops-event.bad i{background:var(--p-bad)}.ops-event b{display:block;font-size:12px}.ops-event small{display:block;color:var(--p-muted);margin-top:3px;line-height:1.7}.ops-event time{direction:ltr;color:var(--p-muted);font-size:10px;white-space:nowrap}.ops-empty{color:var(--p-muted);font-size:12px;padding:14px;border:1px dashed var(--p-border-strong);border-radius:12px;text-align:center}.transport-stack{display:flex;flex-direction:column;gap:8px}.transport-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center;padding:10px;border:1px solid var(--p-border);border-radius:12px;background:var(--p-surface)}.transport-row b{font-size:12px}.transport-row small{display:block;color:var(--p-muted);margin-top:3px}.transport-tag{font-size:10px;border:1px solid var(--p-border);padding:4px 7px;border-radius:999px;color:var(--p-muted)}.transport-tag.prod{color:#86efac;border-color:#34d39944;background:#34d3990d}.transport-tag.lab{color:#fcd34d;border-color:#fbbf2444;background:#fbbf240d}.ops-kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px}.ops-kpi>div{border:1px solid var(--p-border);border-radius:12px;padding:10px;background:var(--p-surface)}.ops-kpi span,.ops-kpi b{display:block}.ops-kpi span{font-size:10px;color:var(--p-muted)}.ops-kpi b{margin-top:5px;font-size:14px}.premium-rebuild-btn{border-color:#8b5cf655!important}.premium-autoheal-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--p-muted);margin-inline-end:6px}.ops-btn-on .premium-autoheal-dot{background:var(--p-good);box-shadow:0 0 0 5px #34d39914}.premium-alert-actions{display:flex;gap:7px;margin-top:8px;flex-wrap:wrap}.premium-alert-actions .btn{padding:5px 8px;font-size:10px}.agent-health-note{margin-top:6px;font-size:10px}.agent-health-note.bad{color:#fda4af}.agent-health-note.good{color:#86efac}@media(max-width:850px){.ops-grid{grid-template-columns:1fr}.ops-kpi{grid-template-columns:1fr}.ops-modal{width:96vw}}
  `;
  document.head.appendChild(style);
}

function readEvents(){
  try{const x=JSON.parse(localStorage.getItem(EVENT_KEY)||'[]');return Array.isArray(x)?x:[]}catch(_){return[]}
}
function saveEvents(rows){try{localStorage.setItem(EVENT_KEY,JSON.stringify(rows.slice(0,120)))}catch(_){}}
function notifyBrowser(title,body){
  if(!noticeEnabled()||typeof Notification==='undefined'||Notification.permission!=='granted')return;
  try{new Notification(title,{body,tag:'tp27-health'})}catch(_){ }
}
function addEvent(level,title,detail=''){
  const rows=readEvents();
  const rec={id:Date.now()+'-'+Math.random().toString(16).slice(2),ts:Date.now(),level,title,detail};
  rows.unshift(rec);saveEvents(rows);
  if(level==='bad'||level==='warn')notifyBrowser('TP27 · '+title,detail||title);
  renderHealthEvents();
}
function clearEvents(){saveEvents([]);renderHealthEvents()}

function applyTheme(theme){
  const value=theme==='light'?'light':'dark';
  document.documentElement.dataset.theme=value;
  localStorage.setItem(THEME_KEY,value);
  const btn=p('premiumThemeBtn');
  if(btn)btn.textContent=value==='light'?'حالت تیره':'حالت روشن';
}
function toggleTheme(){applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light')}

function setBuilderExpanded(open,scroll=false){
  const b=document.querySelector('.builder');
  if(!b)return;
  b.classList.toggle('builder-collapsed',!open);
  localStorage.setItem(BUILDER_KEY,open?'1':'0');
  const btn=p('builderCollapseBtn');
  if(btn)btn.textContent=open?'جمع کردن':'باز کردن سازنده';
  if(open&&scroll)b.scrollIntoView({behavior:'smooth',block:'start'});
}
function enhanceBuilder(){
  const b=document.querySelector('.builder');
  if(!b||b.dataset.premiumBuilder==='1')return;
  b.dataset.premiumBuilder='1';
  const title=b.querySelector(':scope > h3');
  if(!title)return;
  const head=document.createElement('div');
  head.className='section-title builder-head';
  const copy=document.createElement('div');
  copy.className='builder-head-copy';
  const sub=document.createElement('small');
  title.textContent='ساخت تونل جدید';
  sub.textContent='AUTO Smart Builder: GRE → GRE/UDP → OpenVPN/TCP → GOST/WSS → TLS/TCP با Health Gate.';
  b.insertBefore(head,title);
  copy.appendChild(title);
  copy.appendChild(sub);
  const btn=document.createElement('button');
  btn.id='builderCollapseBtn';
  btn.className='btn small builder-toggle';
  btn.type='button';
  btn.onclick=()=>setBuilderExpanded(b.classList.contains('builder-collapsed'),false);
  head.appendChild(copy);
  head.appendChild(btn);
  setBuilderExpanded(localStorage.getItem(BUILDER_KEY)==='1',false);
}

function sectionMeta(){
  const sections=[...document.querySelectorAll('.main > section.card')];
  const agentSection=sections.find(s=>s.querySelector('#agentList'));
  const tunnelSection=sections.find(s=>s.querySelector('#exitList'));
  if(agentSection&&!p('agentSectionMeta')){
    const title=agentSection.querySelector('.section-title');
    if(title){const span=document.createElement('span');span.id='agentSectionMeta';span.className='premium-section-meta';title.querySelector('h3')?.after(span)}
  }
  if(tunnelSection&&!p('tunnelSectionMeta')){
    const title=tunnelSection.querySelector('.section-title');
    if(title){const span=document.createElement('span');span.id='tunnelSectionMeta';span.className='premium-section-meta';title.querySelector('h3')?.after(span)}
  }
}

function ensureCommandCenter(){
  if(p('premiumCommand'))return;
  const stats=document.querySelector('.stats');
  if(!stats)return;
  const box=document.createElement('section');
  box.id='premiumCommand';
  box.className='card premium-command';
  box.innerHTML=`
    <div>
      <div class="premium-health">
        <div id="premiumHealthOrb" class="premium-health-orb"><span></span></div>
        <div class="premium-health-copy">
          <b id="premiumHealthTitle">در حال بررسی وضعیت...</b>
          <small id="premiumHealthSub">آخرین بروزرسانی: -</small>
          <div class="premium-health-bar"><i id="premiumHealthFill"></i></div>
        </div>
      </div>
      <div class="premium-kpi-row">
        <span class="premium-kpi">Agent آنلاین <b id="premiumAgentKpi">0/0</b></span>
        <span class="premium-kpi">Mapping <b id="premiumMappingKpi">0</b></span>
        <span class="premium-kpi">Auto Refresh <b>10s</b></span>
      </div>
      <div id="premiumAlert" class="premium-alert"></div>
    </div>
    <div class="premium-tools">
      <div class="premium-search-wrap"><input id="premiumSearch" class="premium-search" placeholder="جستجو نام، IP یا Tunnel..."></div>
      <div class="premium-actions">
        <button class="btn primary" type="button" id="premiumDoctorBtn">Doctor سراسری</button>
        <button class="btn" type="button" id="premiumHealthCenterBtn">مرکز سلامت</button>
        <button class="btn" type="button" id="premiumAutoHealBtn"><span class="premium-autoheal-dot"></span>خودترمیم</button>
        <button class="btn" type="button" id="premiumBuildBtn">+ ساخت تونل</button>
        <button class="btn" type="button" id="premiumIssuesBtn">فقط مشکلات</button>
        <button class="btn" type="button" id="premiumThemeBtn">حالت روشن</button>
        <button class="btn" type="button" id="premiumRefreshBtn">تازه‌سازی</button>
      </div>
    </div>`;
  stats.insertAdjacentElement('afterend',box);
  p('premiumDoctorBtn').onclick=()=>{if(typeof openFleetDoctor==='function')openFleetDoctor()};
  p('premiumHealthCenterBtn').onclick=openHealthCenter;
  p('premiumAutoHealBtn').onclick=toggleAutoHeal;
  p('premiumBuildBtn').onclick=()=>setBuilderExpanded(true,true);
  p('premiumIssuesBtn').onclick=()=>{issuesOnly=!issuesOnly;p('premiumIssuesBtn').classList.toggle('primary',issuesOnly);applyFilters()};
  p('premiumThemeBtn').onclick=toggleTheme;
  p('premiumRefreshBtn').onclick=()=>refreshAll();
  p('premiumSearch').addEventListener('input',e=>{searchQuery=String(e.target.value||'').trim().toLowerCase();applyFilters()});
  syncAutoHealButton();
}

function cardHasIssue(card){
  const text=card.textContent||'';
  if(card.querySelector('.dot.bad'))return true;
  return /OFFLINE|DEGRADED|بدون Port Mapping|آفلاین/.test(text);
}
function applyFilters(){
  const cards=[...document.querySelectorAll('#agentList .exit,#exitList .exit')];
  for(const card of cards){
    const text=(card.textContent||'').toLowerCase();
    const matches=!searchQuery||text.includes(searchQuery);
    const issueMatch=!issuesOnly||cardHasIssue(card);
    card.classList.toggle('premium-hidden',!(matches&&issueMatch));
  }
}

function syncAutoHealButton(){
  const btn=p('premiumAutoHealBtn');if(!btn)return;
  const on=autoHealEnabled();
  btn.classList.toggle('ops-btn-on',on);
  btn.title=on?'پس از ۳ بررسی ناموفق، Restart هماهنگ ایران/خارج اجرا می‌شود.':'خودترمیم خاموش است.';
}
function toggleAutoHeal(){
  const on=!autoHealEnabled();
  localStorage.setItem(AUTOHEAL_KEY,on?'1':'0');
  syncAutoHealButton();
  addEvent('good',on?'خودترمیم فعال شد':'خودترمیم غیرفعال شد',on?'پس از ۳ Health Check ناموفق، Repair هماهنگ اجرا می‌شود.':'هیچ Repair خودکاری اجرا نمی‌شود.');
}

async function enableNotifications(){
  if(typeof Notification==='undefined'){addEvent('warn','اعلان مرورگر پشتیبانی نمی‌شود','مرورگر فعلی Notification API ندارد.');return}
  try{
    const result=await Notification.requestPermission();
    const enabled=result==='granted';
    localStorage.setItem(NOTICE_KEY,enabled?'1':'0');
    addEvent(enabled?'good':'warn',enabled?'اعلان مرورگر فعال شد':'اجازه اعلان داده نشد',enabled?'قطعی Tunnel و Agent به‌صورت Notification هم اعلام می‌شود.':'برای اعلان سیستم، Permission مرورگر لازم است.');
    renderHealthCenterMeta();
  }catch(e){addEvent('warn','فعال‌سازی اعلان ناموفق بود',e.message||'خطا')}
}

function detectTransitions(){
  const a=safeAgents();
  const s=safeStatus();
  const nextAgents={};
  const nextExits={};
  for(const row of a){
    const id=row.agent_id||row.hostname||row.public_ip||'';
    nextAgents[id]=!!row.online;
    if(Object.prototype.hasOwnProperty.call(lastSnapshot.agents,id)&&lastSnapshot.agents[id]!==nextAgents[id]){
      addEvent(nextAgents[id]?'good':'bad',nextAgents[id]?'سرور خارج برگشت':'سرور خارج آفلاین شد',`${row.hostname||id} · ${row.public_ip||'-'}`);
    }
  }
  for(const x of (s.exits||[])){
    nextExits[x.id]={healthy:!!x.healthy,degraded:!!x.degraded,transport:transportLabel(x)};
    const old=lastSnapshot.exits[x.id];
    if(old&&old.healthy!==!!x.healthy){
      addEvent(x.healthy?'good':'bad',x.healthy?'تونل بازیابی شد':'تونل قطع شد',`${x.id} · ${x.endpoint||'-'} · ${transportLabel(x)}`);
    }else if(old&&old.degraded!==!!x.degraded&&x.degraded){
      addEvent('warn','تونل DEGRADED شد',`${x.id} · بخشی از کانال‌ها سالم نیستند.`);
    }
  }
  lastSnapshot={agents:nextAgents,exits:nextExits};
}

async function premiumAutoRepair(x){
  if(!x||!x.id||repairing.has(x.id))return;
  const now=Date.now();
  if((repairCooldown.get(x.id)||0)>now)return;
  repairing.add(x.id);
  repairCooldown.set(x.id,now+5*60*1000);
  addEvent('warn','خودترمیم شروع شد',`${x.id} · Restart هماهنگ ایران و خارج`);
  try{
    const d=await api('/api/exits/'+encodeURIComponent(x.id)+'/repair',{method:'POST',body:'{}'});
    if(d.healthy){
      failStreak.set(x.id,0);
      addEvent('good','خودترمیم موفق بود',`${x.id} دوباره ONLINE شد.`);
    }else{
      addEvent('bad','خودترمیم کافی نبود',`${x.id} بعد از Restart هنوز Health Check را پاس نکرد؛ «تعویض هوشمند» پیشنهاد می‌شود.`);
    }
  }catch(e){
    addEvent('bad','خودترمیم ناموفق بود',`${x.id} · ${e.message||'خطا'}`);
  }finally{
    repairing.delete(x.id);
    try{await refreshAll()}catch(_){ }
  }
}

function runAutoHeal(){
  const s=safeStatus();
  const ids=new Set((s.exits||[]).map(x=>x.id));
  for(const key of [...failStreak.keys()])if(!ids.has(key))failStreak.delete(key);
  for(const x of (s.exits||[])){
    if(x.healthy){failStreak.set(x.id,0);continue}
    const n=(failStreak.get(x.id)||0)+1;
    failStreak.set(x.id,n);
    if(autoHealEnabled()&&n>=3)premiumAutoRepair(x);
  }
}

async function premiumQuickRebuild(id){
  const x=(safeStatus().exits||[]).find(v=>v.id===id);
  if(!x||rebuilding.has(id))return;
  const agent=safeAgents().find(a=>a.agent_id===x.agent_id);
  if(!agent||!agent.online){addEvent('bad','تعویض هوشمند ممکن نیست',`${id} · Agent سرور خارج آفلاین است.`);openHealthCenter();return}
  const ok=confirm(`Tunnel «${id}» با AUTO Smart Builder بازسازی شود؟\n\nPort Mappingها حفظ می‌شوند اما اتصال‌های جاری هنگام بازسازی کوتاه قطع خواهند شد.\n\nترتیب انتخاب: GRE → GRE/UDP → OpenVPN/TCP → GOST/WSS → TLS/TCP`);
  if(!ok)return;
  rebuilding.add(id);
  addEvent('warn','تعویض هوشمند شروع شد',`${id} · هسته فعلی: ${transportLabel(x)}`);
  try{
    const mappings=(x.mappings||[]).map(m=>({service:m.service,proto:m.proto,listen_port:Number(m.listen_port),target_port:Number(m.target_port)}));
    const removed=await api('/api/exits/'+encodeURIComponent(id),{method:'DELETE'});
    const body={agent_id:x.agent_id,host:x.endpoint||agent.public_ip||'',port:22,username:'root',password:null,private_key:null,id:id,tunnel_port:null,mtu:Number(x.mtu||1280),mappings};
    const created=await api('/api/exits',{method:'POST',body:JSON.stringify(body)});
    const job=p('job');
    if(job)job.textContent=(removed.warning?removed.warning+' · ':'')+'تعویض هوشمند شروع شد؛ AUTO Smart Builder در حال انتخاب مسیر سالم است...';
    setBuilderExpanded(true,true);
    if(typeof pollJob==='function')pollJob(created.job_id,0);
    addEvent('good','Job تعویض هوشمند ثبت شد',`${id} · Job ${created.job_id}`);
  }catch(e){
    addEvent('bad','تعویض هوشمند ناموفق بود',`${id} · ${e.message||'خطا'}`);
  }finally{
    rebuilding.delete(id);
  }
}

function addTunnelQuickActions(){
  const s=safeStatus();
  const cards=[...document.querySelectorAll('#exitList .tunnel-card')];
  cards.forEach((card,i)=>{
    const x=s.exits?.[i];
    if(!x)return;
    card.dataset.exitId=x.id||'';
    const main=card.querySelector('.exit-main');
    if(main&&!main.querySelector('.premium-card-meta')){
      const meta=document.createElement('div');
      meta.className='premium-card-meta';
      meta.innerHTML=`<span class="premium-kpi">Transport <b>${String(transportLabel(x)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</b></span>`;
      main.appendChild(meta);
    }
    const actions=card.querySelector('.tunnel-actions');
    if(actions&&!actions.querySelector('.premium-stability-btn')){
      const b=document.createElement('button');
      b.className='btn small premium-stability-btn';
      b.type='button';b.textContent='پایداری';
      b.onclick=()=>{if(typeof diagnoseTunnel==='function')diagnoseTunnel(x.id);setTimeout(()=>{if(typeof startTunnelStability==='function')startTunnelStability(x.id)},450)};
      actions.appendChild(b);
    }
    if(actions&&!actions.querySelector('.premium-rebuild-btn')){
      const b=document.createElement('button');
      b.className='btn small premium-rebuild-btn';
      b.type='button';b.textContent='تعویض هوشمند';
      b.title='بازسازی سریع با AUTO Smart Builder و حفظ Port Mapping';
      b.onclick=()=>premiumQuickRebuild(x.id);
      actions.appendChild(b);
    }
  });
}

function enhanceAgentCards(){
  const a=safeAgents();
  const cards=[...document.querySelectorAll('#agentList .agent-card')];
  cards.forEach((card,i)=>{
    const x=a[i];if(!x)return;
    let note=card.querySelector('.agent-health-note');
    if(!note){note=document.createElement('div');note.className='agent-health-note';card.querySelector('.exit-main')?.appendChild(note)}
    note.className='agent-health-note '+(x.online?'good':'bad');
    note.textContent=x.online?`کنترل Agent سالم · ${x.tunnel_count||0} Tunnel`:'هشدار: Agent پاسخ نمی‌دهد؛ تعمیر Tunnel تا برگشت Agent محدود است.';
  });
}

function ensureHealthCenter(){
  if(p('premiumHealthModal'))return;
  const modal=document.createElement('div');
  modal.id='premiumHealthModal';modal.className='modal hidden';modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');
  modal.innerHTML=`<div class="modal-card ops-modal">
    <div class="section-title modal-head"><div><h3 style="margin:0">مرکز سلامت و Transport</h3><div class="muted tiny" style="margin-top:5px">تشخیص قطعی، خودترمیم کنترل‌شده و تعویض سریع مسیر</div></div><button class="btn small" type="button" id="opsCloseBtn">×</button></div>
    <div class="ops-kpi"><div><span>خودترمیم</span><b id="opsAutoHealState">-</b></div><div><span>اعلان مرورگر</span><b id="opsNoticeState">-</b></div><div><span>رخدادهای ثبت‌شده</span><b id="opsEventCount">0</b></div></div>
    <div class="actions"><button class="btn primary" id="opsToggleHealBtn" type="button">تغییر وضعیت خودترمیم</button><button class="btn" id="opsNoticeBtn" type="button">فعال‌سازی اعلان</button><button class="btn" id="opsDoctorBtn" type="button">Doctor سراسری</button><button class="btn danger" id="opsClearBtn" type="button">پاک‌کردن رخدادها</button></div>
    <div class="ops-grid"><section class="ops-panel"><h4>رخدادهای سلامت</h4><div id="opsEventList" class="ops-event-list"></div></section><section class="ops-panel"><h4>Transport Stack</h4><div class="transport-stack">
      <div class="transport-row"><div><b>AUTO Smart Builder</b><small>Health Gate و انتخاب اولین Data Plane سالم</small></div><span class="transport-tag prod">Production</span></div>
      <div class="transport-row"><div><b>GRE Native</b><small>Linux Kernel · کمترین سربار</small></div><span class="transport-tag prod">Kernel</span></div>
      <div class="transport-row"><div><b>GRE over UDP / FOU</b><small>Kernel FOU · fallback روی UDP</small></div><span class="transport-tag prod">Kernel</span></div>
      <div class="transport-row"><div><b>OpenVPN P2P/TCP</b><small>Fallback مستقل روی TCP</small></div><span class="transport-tag prod">Stable</span></div>
      <div class="transport-row"><div><b>GOST/WSS 3.3.0</b><small>WebSocket Secure transport</small></div><span class="transport-tag prod">Stable</span></div>
      <div class="transport-row"><div><b>Framed TLS/TCP</b><small>Fallback نهایی TP27</small></div><span class="transport-tag prod">Fallback</span></div>
      <div class="transport-row"><div><b>QUIC / HTTP3 / MASQUE</b><small>مسیر آزمایشگاهی؛ تا عبور از Integration/Soak Test وارد Auto نمی‌شود.</small></div><span class="transport-tag lab">Lab</span></div>
    </div></section></div>
  </div>`;
  document.body.appendChild(modal);
  p('opsCloseBtn').onclick=()=>modal.classList.add('hidden');
  p('opsToggleHealBtn').onclick=()=>{toggleAutoHeal();renderHealthCenterMeta()};
  p('opsNoticeBtn').onclick=enableNotifications;
  p('opsDoctorBtn').onclick=()=>{modal.classList.add('hidden');if(typeof openFleetDoctor==='function')openFleetDoctor()};
  p('opsClearBtn').onclick=clearEvents;
}
function openHealthCenter(){ensureHealthCenter();renderHealthEvents();renderHealthCenterMeta();p('premiumHealthModal').classList.remove('hidden')}
function renderHealthCenterMeta(){
  if(!p('opsAutoHealState'))return;
  p('opsAutoHealState').textContent=autoHealEnabled()?'فعال':'خاموش';
  p('opsNoticeState').textContent=(noticeEnabled()&&typeof Notification!=='undefined'&&Notification.permission==='granted')?'فعال':'خاموش';
  p('opsEventCount').textContent=String(readEvents().length);
}
function renderHealthEvents(){
  const list=p('opsEventList');if(!list)return;
  const rows=readEvents();renderHealthCenterMeta();
  if(!rows.length){list.innerHTML='<div class="ops-empty">هنوز رخداد سلامت ثبت نشده است.</div>';return}
  list.innerHTML=rows.slice(0,80).map(e=>`<div class="ops-event ${e.level==='bad'?'bad':e.level==='warn'?'warn':''}"><i></i><div><b>${String(e.title||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</b><small>${String(e.detail||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</small></div><time>${faDateTime(e.ts)}</time></div>`).join('');
}

function updateDashboardMeta(){
  const s=safeStatus();
  const list=s.exits||[];
  const a=safeAgents();
  const total=list.length;
  const online=list.filter(x=>x.healthy).length;
  const degraded=list.filter(x=>x.degraded).length;
  const offline=Math.max(0,total-online);
  const mappings=list.reduce((n,x)=>n+((x.mappings||[]).length),0);
  const missing=list.filter(x=>x.healthy&&!((x.mappings||[]).length)).length;
  const agentOnline=a.filter(x=>x.online).length;
  const agentOffline=Math.max(0,a.length-agentOnline);
  const pct=total?Math.round(online*100/total):0;
  const title=p('premiumHealthTitle');
  const sub=p('premiumHealthSub');
  const fill=p('premiumHealthFill');
  const orb=p('premiumHealthOrb');
  if(title)title.textContent=total?`${online} از ${total} تونل آنلاین`:'هنوز تونلی ساخته نشده';
  if(sub)sub.textContent=`آخرین بروزرسانی ${faTime()}${degraded?` · ${degraded} DEGRADED`:''}${agentOffline?` · ${agentOffline} Agent آفلاین`:''}`;
  if(fill)fill.style.width=pct+'%';
  if(orb){orb.classList.remove('warn','bad');if(offline||agentOffline)orb.classList.add('bad');else if(degraded||missing)orb.classList.add('warn')}
  if(p('premiumAgentKpi'))p('premiumAgentKpi').textContent=`${agentOnline}/${a.length}`;
  if(p('premiumMappingKpi'))p('premiumMappingKpi').textContent=String(mappings);
  if(p('agentSectionMeta'))p('agentSectionMeta').textContent=`${agentOnline}/${a.length} آنلاین`;
  if(p('tunnelSectionMeta'))p('tunnelSectionMeta').textContent=`${online}/${total} آنلاین`;
  const alert=p('premiumAlert');
  if(alert){
    alert.className='premium-alert';
    if(offline||agentOffline){
      alert.classList.add('show','bad');
      alert.innerHTML=`${agentOffline?agentOffline+' سرور خارج آفلاین است. ':''}${offline?offline+' Tunnel آفلاین است.':''}<div class="premium-alert-actions"><button class="btn" type="button" onclick="document.getElementById('premiumHealthCenterBtn').click()">مرکز سلامت</button><button class="btn" type="button" onclick="document.getElementById('premiumDoctorBtn').click()">Doctor</button></div>`;
    }else if(degraded||missing){
      alert.classList.add('show','warn');
      alert.textContent=`${degraded?degraded+' Tunnel در حالت DEGRADED. ':''}${missing?missing+' Tunnel سالم ولی بدون Port Mapping.':''}`;
    }
  }
  addTunnelQuickActions();enhanceAgentCards();applyFilters();detectTransitions();runAutoHeal();renderHealthCenterMeta();
}

function boot(){
  injectOpsCss();
  document.body.classList.add('tp27-premium');
  applyTheme(localStorage.getItem(THEME_KEY)||'dark');
  ensureCommandCenter();ensureHealthCenter();enhanceBuilder();sectionMeta();
  const oldDoctor=p('fleetDoctorBtn');if(oldDoctor)oldDoctor.classList.add('hidden');
  const baseRefresh=typeof refreshAll==='function'?refreshAll:null;
  if(baseRefresh&&!refreshAll.__premiumWrapped){
    const wrapped=async function(){
      const btn=p('premiumRefreshBtn');if(btn){btn.disabled=true;btn.textContent='در حال بروزرسانی...'}
      try{return await baseRefresh.apply(this,arguments)}finally{setTimeout(()=>{updateDashboardMeta();if(btn){btn.disabled=false;btn.textContent='تازه‌سازی'}},30)}
    };
    wrapped.__premiumWrapped=true;refreshAll=wrapped;
  }
  if(typeof useReadyAgent==='function'&&!useReadyAgent.__premiumWrapped){
    const baseUse=useReadyAgent;
    const wrappedUse=function(){setBuilderExpanded(true,false);return baseUse.apply(this,arguments)};
    wrappedUse.__premiumWrapped=true;useReadyAgent=wrappedUse;
  }
  setTimeout(()=>{updateDashboardMeta();if(typeof refreshAll==='function')refreshAll()},100);
}

window.premiumQuickRebuild=premiumQuickRebuild;
window.openHealthCenter=openHealthCenter;
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
