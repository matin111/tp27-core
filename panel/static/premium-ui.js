(()=>{
'use strict';
const THEME_KEY='tp27_ui_theme';
const BUILDER_KEY='tp27_builder_open';
let issuesOnly=false;
let searchQuery='';

function p(id){return document.getElementById(id)}
function faTime(){try{return new Intl.DateTimeFormat('fa-IR',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())}catch(_){return new Date().toLocaleTimeString()}}
function safeStatus(){try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}}
function safeAgents(){try{return agents||[]}catch(_){return[]}}

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
  sub.textContent='سرور خارج را انتخاب کنید، Port Mapping را مشخص کنید و Health Gate را اجرا کنید.';
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
        <button class="btn" type="button" id="premiumBuildBtn">+ ساخت تونل</button>
        <button class="btn" type="button" id="premiumIssuesBtn">فقط مشکلات</button>
        <button class="btn" type="button" id="premiumThemeBtn">حالت روشن</button>
        <button class="btn" type="button" id="premiumRefreshBtn">تازه‌سازی</button>
      </div>
    </div>`;
  stats.insertAdjacentElement('afterend',box);
  p('premiumDoctorBtn').onclick=()=>{if(typeof openFleetDoctor==='function')openFleetDoctor()};
  p('premiumBuildBtn').onclick=()=>setBuilderExpanded(true,true);
  p('premiumIssuesBtn').onclick=()=>{issuesOnly=!issuesOnly;p('premiumIssuesBtn').classList.toggle('primary',issuesOnly);applyFilters()};
  p('premiumThemeBtn').onclick=toggleTheme;
  p('premiumRefreshBtn').onclick=()=>refreshAll();
  p('premiumSearch').addEventListener('input',e=>{searchQuery=String(e.target.value||'').trim().toLowerCase();applyFilters()});
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

function addTunnelQuickActions(){
  const s=safeStatus();
  const cards=[...document.querySelectorAll('#exitList .tunnel-card')];
  cards.forEach((card,i)=>{
    const x=s.exits?.[i];
    if(!x)return;
    card.dataset.exitId=x.id||'';
    const actions=card.querySelector('.tunnel-actions');
    if(actions&&!actions.querySelector('.premium-stability-btn')){
      const b=document.createElement('button');
      b.className='btn small premium-stability-btn';
      b.type='button';
      b.textContent='پایداری';
      b.onclick=()=>{
        if(typeof diagnoseTunnel==='function')diagnoseTunnel(x.id);
        setTimeout(()=>{if(typeof startTunnelStability==='function')startTunnelStability(x.id)},450);
      };
      actions.appendChild(b);
    }
  });
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
  const pct=total?Math.round(online*100/total):0;
  const title=p('premiumHealthTitle');
  const sub=p('premiumHealthSub');
  const fill=p('premiumHealthFill');
  const orb=p('premiumHealthOrb');
  if(title)title.textContent=total?`${online} از ${total} تونل آنلاین`:'هنوز تونلی ساخته نشده';
  if(sub)sub.textContent=`آخرین بروزرسانی ${faTime()}${degraded?` · ${degraded} DEGRADED`:''}`;
  if(fill)fill.style.width=pct+'%';
  if(orb){orb.classList.remove('warn','bad');if(offline)orb.classList.add('bad');else if(degraded||missing)orb.classList.add('warn')}
  if(p('premiumAgentKpi'))p('premiumAgentKpi').textContent=`${agentOnline}/${a.length}`;
  if(p('premiumMappingKpi'))p('premiumMappingKpi').textContent=String(mappings);
  if(p('agentSectionMeta'))p('agentSectionMeta').textContent=`${agentOnline}/${a.length} آنلاین`;
  if(p('tunnelSectionMeta'))p('tunnelSectionMeta').textContent=`${online}/${total} آنلاین`;
  const alert=p('premiumAlert');
  if(alert){
    alert.className='premium-alert';
    if(offline){alert.classList.add('show','bad');alert.textContent=`${offline} تونل آفلاین است؛ Doctor را اجرا کنید.`}
    else if(degraded||missing){alert.classList.add('show','warn');alert.textContent=`${degraded?degraded+' Tunnel در حالت DEGRADED. ':''}${missing?missing+' Tunnel سالم ولی بدون Port Mapping.':''}`}
  }
  addTunnelQuickActions();
  applyFilters();
}

function boot(){
  document.body.classList.add('tp27-premium');
  applyTheme(localStorage.getItem(THEME_KEY)||'dark');
  ensureCommandCenter();
  enhanceBuilder();
  sectionMeta();
  const oldDoctor=p('fleetDoctorBtn');if(oldDoctor)oldDoctor.classList.add('hidden');
  const baseRefresh=typeof refreshAll==='function'?refreshAll:null;
  if(baseRefresh&&!refreshAll.__premiumWrapped){
    const wrapped=async function(){
      const btn=p('premiumRefreshBtn');if(btn){btn.disabled=true;btn.textContent='در حال بروزرسانی...'}
      try{return await baseRefresh.apply(this,arguments)}finally{setTimeout(()=>{updateDashboardMeta();if(btn){btn.disabled=false;btn.textContent='تازه‌سازی'}},20)}
    };
    wrapped.__premiumWrapped=true;
    refreshAll=wrapped;
  }
  if(typeof useReadyAgent==='function'&&!useReadyAgent.__premiumWrapped){
    const baseUse=useReadyAgent;
    const wrappedUse=function(){setBuilderExpanded(true,false);return baseUse.apply(this,arguments)};
    wrappedUse.__premiumWrapped=true;
    useReadyAgent=wrappedUse;
  }
  setTimeout(()=>{updateDashboardMeta();if(typeof refreshAll==='function')refreshAll()},80);
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
