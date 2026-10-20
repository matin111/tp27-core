(()=>{
'use strict';

const BOOT_MAX_MS=2400;
const REFRESH_MAX_MS=1900;
let refreshPromise=null;

const isV9=()=>!!(document.body&&document.body.hasAttribute('data-v9-view'));

const style=document.createElement('style');
style.id='tp27UiStabilityStyle';
style.textContent=`
html.tp27-ui-stabilizing body.tp27-premium .wrap{visibility:hidden!important}
html.tp27-ui-stabilizing body.tp27-premium:before{content:'TP27 Control';position:fixed;inset:0;display:grid;place-items:center;z-index:99999;color:var(--p-muted);font:700 14px Tahoma,Arial,sans-serif;letter-spacing:.3px;pointer-events:none}
.tp27-refresh-shadow{position:absolute!important;z-index:20!important;pointer-events:none!important;margin-top:0!important}
.tp27-refresh-live-hidden{visibility:hidden!important}

/* V9 owns the final card geometry. Legacy runtime tiles must never override it. */
body[data-v9-view] #agentList,
body[data-v9-view] #exitList{
  display:flex!important;
  flex-direction:column!important;
  grid-template-columns:none!important;
  gap:10px!important;
  align-items:stretch!important;
}
body[data-v9-view] #agentList>.agent-card,
body[data-v9-view] #exitList>.tunnel-card{
  width:100%!important;
  min-width:0!important;
  min-height:0!important;
  margin:0!important;
  padding:15px 16px!important;
  display:grid!important;
  grid-template-columns:minmax(0,1fr) auto!important;
  grid-template-rows:auto!important;
  gap:16px!important;
  align-items:center!important;
  overflow:visible!important;
  border-radius:16px!important;
  border:1px solid var(--v9-line)!important;
  background:var(--v9-panel-2)!important;
  box-shadow:none!important;
  transform:none!important;
}
body[data-v9-view] #agentList>.agent-card:before,
body[data-v9-view] #exitList>.tunnel-card:before{display:none!important}
body[data-v9-view] #agentList>.agent-card .exit-main,
body[data-v9-view] #exitList>.tunnel-card .exit-main{
  width:100%!important;
  min-width:0!important;
  padding:0!important;
  gap:8px!important;
}
body[data-v9-view] #agentList>.agent-card .badges,
body[data-v9-view] #exitList>.tunnel-card .badges{
  width:100%!important;
  margin-top:2px!important;
  padding-top:8px!important;
  display:flex!important;
  flex-wrap:wrap!important;
  gap:6px!important;
  border-top:1px dashed var(--v9-line)!important;
}
body[data-v9-view] #agentList>.agent-card .bm-strip,
body[data-v9-view] #exitList>.tunnel-card .bm-strip{
  width:100%!important;
  display:grid!important;
  grid-template-columns:repeat(4,minmax(0,1fr))!important;
  gap:6px!important;
  margin-top:4px!important;
  padding-top:9px!important;
  border-top:1px solid var(--v9-line)!important;
}
body[data-v9-view] #agentList>.agent-card .tunnel-actions,
body[data-v9-view] #exitList>.tunnel-card .tunnel-actions{
  width:auto!important;
  min-width:0!important;
  margin:0!important;
  padding:0!important;
  border:0!important;
  background:transparent!important;
  align-self:center!important;
  display:flex!important;
  flex-wrap:wrap!important;
  justify-content:flex-start!important;
  align-items:center!important;
  gap:6px!important;
}
body[data-v9-view] #agentList>.agent-card .tunnel-actions .btn,
body[data-v9-view] #exitList>.tunnel-card .tunnel-actions .btn{
  width:auto!important;
  min-width:78px!important;
  min-height:34px!important;
  flex:0 0 auto!important;
  padding:7px 10px!important;
  border-radius:9px!important;
  font-size:10px!important;
}
body[data-v9-view] #agentList>.agent-card:hover,
body[data-v9-view] #exitList>.tunnel-card:hover{
  transform:none!important;
  border-color:color-mix(in srgb,var(--v9-accent) 42%,var(--v9-line))!important;
  box-shadow:none!important;
}

@media(max-width:1180px){
  body[data-v9-view] #agentList>.agent-card,
  body[data-v9-view] #exitList>.tunnel-card{grid-template-columns:1fr!important}
  body[data-v9-view] #agentList>.agent-card .tunnel-actions,
  body[data-v9-view] #exitList>.tunnel-card .tunnel-actions{justify-content:flex-start!important}
}
@media(max-width:760px){
  body[data-v9-view] #agentList>.agent-card .bm-strip,
  body[data-v9-view] #exitList>.tunnel-card .bm-strip{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  body[data-v9-view] #agentList>.agent-card .tunnel-actions .btn,
  body[data-v9-view] #exitList>.tunnel-card .tunnel-actions .btn{flex:1 1 calc(50% - 6px)!important;min-width:0!important}
}
`;
document.head.appendChild(style);
document.documentElement.classList.add('tp27-ui-stabilizing');

const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const htmlEsc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function serviceRank(name){
  const key=String(name||'').trim().toLowerCase();
  const ranks={cisco:10,openvpn:20,wireguard:30,l2tp:40,v2ray:50,xray:50};
  return ranks[key]??90;
}
function protoRank(value){return String(value||'').toUpperCase()==='TCP'?0:1}
function l2tpPortRank(port){return ({500:0,4500:1,1701:2})[Number(port)]??10}

function badgeInfo(el){
  const service=(el.querySelector('b')?.textContent||'').trim().toLowerCase();
  const text=(el.textContent||'').replace(/\s+/g,' ').trim();
  const proto=(text.match(/\b(TCP|UDP)\b/i)||[])[1]||'';
  const nums=[...text.matchAll(/\b(\d{1,5})\b/g)].map(m=>Number(m[1]));
  const port=nums[0]||0;
  return {service,proto,port,text};
}

function normalizePortBadges(root=document){
  root.querySelectorAll('#exitList .badges').forEach(box=>{
    const badges=[...box.querySelectorAll(':scope > .badge')];
    if(!badges.length)return;
    badges.sort((a,b)=>{
      const A=badgeInfo(a),B=badgeInfo(b);
      const sr=serviceRank(A.service)-serviceRank(B.service);if(sr)return sr;
      if(A.service==='l2tp'&&B.service==='l2tp'){
        const lr=l2tpPortRank(A.port)-l2tpPortRank(B.port);if(lr)return lr;
      }
      const pr=protoRank(A.proto)-protoRank(B.proto);if(pr)return pr;
      if(A.port!==B.port)return A.port-B.port;
      return A.text.localeCompare(B.text,'en');
    });
    for(const badge of badges){
      const info=badgeInfo(badge);
      badge.dataset.tp27Service=info.service||'custom';
      box.appendChild(badge);
    }
  });
}

/*
 * V9 zero-shift refresh contract:
 * - heartbeat/state objects are always replaced with fresh API data;
 * - visible lists are rebuilt only when a field rendered on screen changed;
 * - volatile heartbeat fields such as last_seen never rebuild inventory cards;
 * - transient GET errors keep the last healthy DOM instead of replacing it.
 */
function tunnelVisualSignature(status){
  const rows=(status&&Array.isArray(status.exits)?status.exits:[]).map(x=>({
    id:String(x.id||''),
    healthy:!!x.healthy,
    degraded:!!x.degraded,
    endpoint:String(x.endpoint||''),
    port:Number(x.port||0),
    interface:String(x.interface||''),
    remote_ip:String(x.remote_ip||''),
    transport:String(x.transport||''),
    mode:String(x.mode||''),
    mappings:(x.mappings||[]).map(m=>({
      service:String(m.service||''),
      proto:String(m.proto||'').toLowerCase(),
      listen_port:Number(m.listen_port||0),
      target_port:Number(m.target_port||0)
    }))
  }));
  return JSON.stringify(rows);
}

function agentVisualSignature(list){
  return JSON.stringify((list||[]).map(a=>({
    agent_id:String(a.agent_id||''),
    hostname:String(a.hostname||''),
    public_ip:String(a.public_ip||''),
    online:!!a.online,
    version:String(a.version||''),
    tunnel_count:Number(a.tunnel_count||0)
  })));
}

function agentOptionsSignature(list){
  return JSON.stringify((list||[]).map(a=>[
    String(a.agent_id||''),
    String(a.hostname||''),
    String(a.public_ip||''),
    !!a.online
  ]));
}

function mappingsHtml(items=[]){
  if(!items.length)return '';
  return '<div class="badges">'+items.map(m=>`<span class="badge"><b>${htmlEsc(m.service)}</b> ${htmlEsc(String(m.proto).toUpperCase())} ${htmlEsc(m.listen_port)}→${htmlEsc(m.target_port)}</span>`).join('')+'</div>';
}

function seedVisualSignature(listId,signature,count,selector){
  const list=document.getElementById(listId);
  if(!list||list.dataset.tp27VisualSignature)return;
  if(list.querySelectorAll(selector).length===count)list.dataset.tp27VisualSignature=signature;
}

function updateAgentSelectSilently(list){
  const sel=document.getElementById('agentId');
  if(!sel)return;
  const signature=agentOptionsSignature(list);
  if(sel.dataset.tp27OptionsSignature===signature)return;
  const current=sel.value;
  sel.innerHTML='<option value="">انتخاب سرور</option>'+list.map(a=>`<option value="${htmlEsc(a.agent_id)}" ${a.online?'':'disabled'}>${htmlEsc(a.hostname||a.agent_id)} · ${htmlEsc(a.public_ip||'-')}${a.online?'':' · آفلاین'}</option>`).join('');
  if([...sel.options].some(o=>o.value===current&&!o.disabled))sel.value=current;
  sel.dataset.tp27OptionsSignature=signature;
}

function renderTunnelInventory(status,signature){
  const list=document.getElementById('exitList');
  if(!list)return;
  const rows=status.exits||[];
  list.innerHTML=rows.length?rows.map(x=>`
    <div class="exit tunnel-card" data-exit-id="${htmlEsc(x.id)}">
      <div class="exit-main">
        <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${htmlEsc(x.id)}</b></div>
        <div class="muted">${htmlEsc(x.endpoint)}:${htmlEsc(x.port)} · ${htmlEsc(x.interface)} · ${htmlEsc(x.remote_ip)}</div>
        ${mappingsHtml(x.mappings)}
      </div>
      <div class="actions tunnel-actions">
        <button class="btn small primary" type="button" onclick="openTunnelManage('${htmlEsc(x.id)}')">مدیریت</button>
        <button class="btn small" type="button" onclick="diagnoseTunnel('${htmlEsc(x.id)}')">بررسی</button>
        <button class="btn small" type="button" onclick="repairTunnel('${htmlEsc(x.id)}')">Restart</button>
      </div>
    </div>`).join(''):'<div class="muted">بدون تونل</div>';
  list.dataset.tp27VisualSignature=signature;
  list.dataset.tp27LastRender=String(Date.now());
}

function renderAgentInventory(listData,signature){
  const list=document.getElementById('agentList');
  if(!list)return;
  list.innerHTML=listData.length?listData.map(a=>`
    <div class="exit agent-card tunnel-card" data-agent-id="${htmlEsc(a.agent_id)}">
      <div class="exit-main">
        <div><span class="dot ${a.online?'good':'bad'}"></span><b>${htmlEsc(a.hostname||a.agent_id)}</b></div>
        <div class="muted">${htmlEsc(a.public_ip||'-')} · ${a.online?'آنلاین':'آفلاین'} · v${htmlEsc(a.version||'-')} · ${Number(a.tunnel_count||0)} تونل</div>
      </div>
      <div class="actions tunnel-actions">
        <button class="btn small primary" type="button" onclick="openAgentManage('${htmlEsc(a.agent_id)}')">مدیریت</button>
      </div>
    </div>`).join(''):'<div class="muted">بدون سرور خارج</div>';
  list.dataset.tp27VisualSignature=signature;
  list.dataset.tp27LastRender=String(Date.now());
}

async function silentV9LoadStatus(){
  const list=document.getElementById('exitList');
  const previous=(()=>{try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}})();
  if(list){
    const oldSignature=tunnelVisualSignature(previous);
    seedVisualSignature('exitList',oldSignature,(previous.exits||[]).length,'.tunnel-card');
  }
  try{
    const s=await api('/api/status');
    currentStatus=s;
    const publicIp=document.getElementById('publicIp');
    const exitCount=document.getElementById('exitCount');
    const onlineCount=document.getElementById('onlineCount');
    if(publicIp)publicIp.textContent=s.public_ip||'-';
    if(exitCount)exitCount.textContent=String((s.exits||[]).length);
    if(onlineCount)onlineCount.textContent=String((s.exits||[]).filter(x=>x.healthy).length);
    if(!list)return s;
    const signature=tunnelVisualSignature(s);
    const hasExpectedCards=list.querySelectorAll('.tunnel-card').length===(s.exits||[]).length;
    if(list.dataset.tp27VisualSignature===signature&&hasExpectedCards){
      list.dataset.tp27Refresh='silent';
      list.dataset.tp27LastSync=String(Date.now());
      return s;
    }
    renderTunnelInventory(s,signature);
    normalizePortBadges(list);
    return s;
  }catch(e){
    if(list){
      list.dataset.tp27RefreshError=String(e&&e.message||e||'refresh failed');
      if(list.querySelector('.tunnel-card'))return previous;
      list.innerHTML='<div class="muted">'+htmlEsc(e&&e.message||'خطا')+'</div>';
    }
    return previous;
  }
}

async function silentV9LoadAgents(){
  const list=document.getElementById('agentList');
  const previous=(()=>{try{return agents||[]}catch(_){return[]}})();
  if(list){
    const oldSignature=agentVisualSignature(previous);
    seedVisualSignature('agentList',oldSignature,previous.length,'.agent-card');
  }
  try{
    const data=await api('/api/agents');
    const fresh=data.agents||[];
    agents=fresh;
    const count=document.getElementById('agentCount');
    if(count)count.textContent=String(fresh.length);
    updateAgentSelectSilently(fresh);
    if(!list)return fresh;
    const signature=agentVisualSignature(fresh);
    const hasExpectedCards=list.querySelectorAll('.agent-card').length===fresh.length;
    if(list.dataset.tp27VisualSignature===signature&&hasExpectedCards){
      list.dataset.tp27Refresh='silent';
      list.dataset.tp27LastSync=String(Date.now());
      return fresh;
    }
    renderAgentInventory(fresh,signature);
    return fresh;
  }catch(e){
    if(list){
      list.dataset.tp27RefreshError=String(e&&e.message||e||'refresh failed');
      if(list.querySelector('.agent-card'))return previous;
      list.innerHTML='<div class="muted">'+htmlEsc(e&&e.message||'خطا')+'</div>';
    }
    return previous;
  }
}

function installSilentV9DataHooks(){
  if(!isV9())return;
  if(typeof window.loadStatus==='function'&&!window.loadStatus.__tp27ZeroShift){
    silentV9LoadStatus.__tp27ZeroShift=true;
    window.loadStatus=silentV9LoadStatus;
  }
  if(typeof window.loadAgents==='function'&&!window.loadAgents.__tp27ZeroShift){
    silentV9LoadAgents.__tp27ZeroShift=true;
    window.loadAgents=silentV9LoadAgents;
  }
}

function ensureV9SystemNav(){
  if(!isV9())return;
  const nav=document.querySelector('.v9-nav');
  if(!nav)return;

  if(!document.getElementById('v9PanelUpdateBtn')){
    const update=document.createElement('button');
    update.id='v9PanelUpdateBtn';
    update.className='v9-nav-item';
    update.type='button';
    update.innerHTML='<span class="v9-nav-icon">↥</span><span>بروزرسانی پنل</span>';
    update.onclick=()=>{
      if(typeof window.tp27OpenPanelUpdate==='function')window.tp27OpenPanelUpdate();
      else alert('ماژول بروزرسانی هنوز آماده نشده است؛ صفحه را یک‌بار تازه‌سازی کنید.');
    };
    nav.appendChild(update);
  }

  if(!document.getElementById('v9GitHubLink')){
    const github=document.createElement('a');
    github.id='v9GitHubLink';
    github.className='v9-nav-item';
    github.href='https://github.com/matin111/tp27-core';
    github.target='_blank';
    github.rel='noopener noreferrer';
    github.style.textDecoration='none';
    github.innerHTML='<span class="v9-nav-icon">⌘</span><span>GitHub</span>';
    nav.appendChild(github);
  }
}

function stripIds(node){
  if(node.nodeType!==1)return;
  node.removeAttribute('id');
  node.querySelectorAll('[id]').forEach(x=>x.removeAttribute('id'));
}

function copyLayoutSnapshot(live,shadow){
  const computed=getComputedStyle(live);
  const props=[
    'display','grid-template-columns','grid-template-rows','grid-auto-flow',
    'grid-auto-columns','grid-auto-rows','column-gap','row-gap',
    'align-items','justify-items','align-content','justify-content','direction'
  ];
  for(const prop of props){
    const value=computed.getPropertyValue(prop);
    if(value)shadow.style.setProperty(prop,value,'important');
  }
  const rect=live.getBoundingClientRect();
  const width=Math.max(0,rect.width);
  const height=Math.max(0,rect.height);
  shadow.style.setProperty('width',width+'px','important');
  shadow.style.setProperty('min-width',width+'px','important');
  shadow.style.setProperty('max-width',width+'px','important');
  shadow.style.setProperty('min-height',height+'px','important');
}

function freezeList(id){
  const live=document.getElementById(id);
  if(!live||!live.parentElement||!live.children.length)return null;
  const host=live.parentElement;
  const prevPosition=host.style.position;
  if(getComputedStyle(host).position==='static')host.style.position='relative';
  const shadow=live.cloneNode(true);
  copyLayoutSnapshot(live,shadow);
  shadow.dataset.tp27ShadowFor=id;
  stripIds(shadow);
  shadow.classList.add('tp27-refresh-shadow');
  shadow.style.setProperty('top',live.offsetTop+'px','important');
  shadow.style.setProperty('left',live.offsetLeft+'px','important');
  live.classList.add('tp27-refresh-live-hidden');
  host.appendChild(shadow);
  return ()=>{
    shadow.remove();
    live.classList.remove('tp27-refresh-live-hidden');
    host.style.position=prevPosition;
  };
}

function freezeDashboard(){
  /* V9 keeps the live list in place. A cloned shadow was the source of the
     visible second card model during refresh. Legacy UI can still use it. */
  if(isV9())return ()=>{};
  const cleanups=[freezeList('agentList'),freezeList('exitList')].filter(Boolean);
  return ()=>{for(const fn of cleanups.reverse())try{fn()}catch(_){} };
}

function expectedCounts(){
  let tunnelCount=0,agentCount=0;
  try{tunnelCount=(currentStatus.exits||[]).length}catch(_){ }
  try{agentCount=(agents||[]).length}catch(_){ }
  return {tunnelCount,agentCount};
}

function uiReady(){
  if(!document.body.classList.contains('tp27-premium'))return false;
  const exitList=document.getElementById('exitList');
  const agentList=document.getElementById('agentList');
  if(!exitList||!agentList)return false;
  if(/در حال دریافت/.test(exitList.textContent||'')||/در حال دریافت/.test(agentList.textContent||''))return false;

  const tunnelCards=[...exitList.querySelectorAll('.tunnel-card')];
  const agentCards=[...agentList.querySelectorAll('.agent-card')];
  const expected=expectedCounts();
  if(tunnelCards.length!==expected.tunnelCount||agentCards.length!==expected.agentCount)return false;

  const tunnelsReady=tunnelCards.every(card=>{
    const actions=card.querySelector('.tunnel-actions');
    const safe=actions&&actions.querySelector('.premium-rebuild-btn');
    return !!card.querySelector('.premium-card-meta') && !!actions && !!actions.querySelector('.bm-btn') && !!actions.querySelector('.premium-stability-btn') && !!safe && safe.classList.contains('tp27-safe-ready');
  });
  const agentsReady=agentCards.every(card=>!!card.querySelector('.srv-speed-wrap'));
  return tunnelsReady&&agentsReady;
}

async function waitForStableUi(maxMs){
  const end=Date.now()+maxMs;
  do{
    normalizePortBadges();
    if(uiReady())return true;
    await sleep(80);
  }while(Date.now()<end);
  normalizePortBadges();
  return false;
}

function installLoadStatusHook(){
  installSilentV9DataHooks();
  const base=window.loadStatus;
  if(typeof base!=='function'||base.__tp27PortStable)return;
  const wrapped=async function(){
    const result=await base.apply(this,arguments);
    normalizePortBadges();
    return result;
  };
  wrapped.__tp27PortStable=true;
  if(base.__tp27ZeroShift)wrapped.__tp27ZeroShift=true;
  window.loadStatus=wrapped;
}

function installRefreshHook(){
  installSilentV9DataHooks();
  const base=window.refreshAll;
  if(typeof base!=='function'||base.__tp27UiStable)return;
  const wrapped=async function(){
    if(refreshPromise)return refreshPromise;
    const thaw=freezeDashboard();
    refreshPromise=(async()=>{
      try{
        const result=await base.apply(this,arguments);
        await waitForStableUi(REFRESH_MAX_MS);
        return result;
      }finally{
        normalizePortBadges();
        thaw();
        refreshPromise=null;
      }
    })();
    return refreshPromise;
  };
  wrapped.__tp27UiStable=true;
  window.refreshAll=wrapped;
}

async function releaseBoot(){
  ensureV9SystemNav();
  installSilentV9DataHooks();
  installLoadStatusHook();
  installRefreshHook();
  await waitForStableUi(BOOT_MAX_MS);
  normalizePortBadges();
  ensureV9SystemNav();
  document.documentElement.classList.remove('tp27-ui-stabilizing');
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',()=>setTimeout(releaseBoot,0),{once:true});
}else{
  setTimeout(releaseBoot,0);
}

window.addEventListener('pageshow',()=>{
  ensureV9SystemNav();
  installSilentV9DataHooks();
  installLoadStatusHook();
  installRefreshHook();
  normalizePortBadges();
});
})();