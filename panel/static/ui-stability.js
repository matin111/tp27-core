(()=>{
'use strict';

const BOOT_MAX_MS=2400;
const REFRESH_MAX_MS=1900;
let refreshPromise=null;

const style=document.createElement('style');
style.id='tp27UiStabilityStyle';
style.textContent=`
html.tp27-ui-stabilizing body.tp27-premium .wrap{visibility:hidden!important}
html.tp27-ui-stabilizing body.tp27-premium:before{content:'TP27 Control';position:fixed;inset:0;display:grid;place-items:center;z-index:99999;color:var(--p-muted);font:700 14px Tahoma,Arial,sans-serif;letter-spacing:.3px;pointer-events:none}
.tp27-refresh-shadow{position:absolute!important;z-index:20!important;pointer-events:none!important;margin-top:0!important}
.tp27-refresh-live-hidden{visibility:hidden!important}

/* ============================================================
   TP27 TUNNEL CARD 2027
   Compact control-tile layout: identity -> mappings -> metrics -> actions.
   ============================================================ */
.tp27-premium #exitList{align-items:stretch}
.tp27-premium .tunnel-card{
  display:grid!important;
  grid-template-columns:minmax(0,1fr)!important;
  grid-template-rows:auto auto!important;
  gap:0!important;
  min-height:0!important;
  padding:0!important;
  overflow:hidden!important;
  position:relative!important;
  border-radius:18px!important;
  background:linear-gradient(180deg,color-mix(in srgb,var(--p-surface-2) 98%,transparent),color-mix(in srgb,var(--p-surface) 96%,transparent))!important;
}
.tp27-premium .tunnel-card:before{
  content:'';
  position:absolute;
  inset:0 0 auto 0;
  height:2px;
  background:linear-gradient(90deg,transparent,var(--p-accent),#22d3ee,transparent);
  opacity:.55;
  pointer-events:none;
}
.tp27-premium .tunnel-card .exit-main{
  width:100%;
  min-width:0;
  padding:15px 16px 12px!important;
  gap:8px!important;
}
.tp27-premium .tunnel-card .exit-main>div:first-child{
  display:flex;
  align-items:center;
  gap:7px;
  min-height:24px;
}
.tp27-premium .tunnel-card .exit-main>div:first-child b{
  font-size:15px;
  letter-spacing:-.15px;
}
.tp27-premium .tunnel-card .exit-main>.muted{
  font-size:11px!important;
  line-height:1.6;
  opacity:.9;
}
.tp27-premium .tunnel-card .badges{
  width:100%;
  display:flex!important;
  flex-wrap:wrap!important;
  gap:5px!important;
  align-items:center;
  justify-content:flex-start;
  margin-top:2px!important;
  padding-top:9px!important;
  border-top:1px dashed color-mix(in srgb,var(--p-border) 78%,transparent);
}
.tp27-premium .tunnel-card .badge{
  display:inline-flex!important;
  align-items:center;
  justify-content:flex-start;
  gap:6px;
  min-width:0!important;
  width:auto!important;
  flex:0 0 auto!important;
  white-space:nowrap;
  direction:ltr;
  text-align:left;
  padding:5px 8px!important;
  border-radius:9px!important;
  font-size:10px!important;
  background:color-mix(in srgb,var(--p-surface-3) 88%,transparent)!important;
}
.tp27-premium .tunnel-card .badge b{direction:rtl;text-align:right;font-size:10px}
.tp27-premium .tunnel-card .premium-card-meta{
  margin-top:1px!important;
  gap:5px!important;
}
.tp27-premium .tunnel-card .premium-card-meta .premium-kpi{
  padding:4px 7px!important;
  background:var(--p-accent-soft)!important;
  border-color:color-mix(in srgb,var(--p-accent) 26%,var(--p-border))!important;
  font-size:10px!important;
}
.tp27-premium .tunnel-card .bm-strip{
  display:grid!important;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:6px!important;
  width:100%;
  margin-top:2px!important;
  padding-top:9px!important;
  border-top:1px solid color-mix(in srgb,var(--p-border) 72%,transparent);
}
.tp27-premium .tunnel-card .bm-strip .bm-chip{
  min-width:0;
  min-height:31px;
  display:flex!important;
  align-items:center;
  justify-content:center;
  border-radius:10px!important;
  padding:5px 6px!important;
  background:color-mix(in srgb,var(--p-bg) 38%,var(--p-surface-2))!important;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.tp27-premium .tunnel-card .bm-strip .bm-chip:only-child{grid-column:1/-1;justify-content:flex-start}
.tp27-premium .tunnel-card .tunnel-actions{
  width:100%;
  align-self:auto!important;
  display:grid!important;
  grid-template-columns:repeat(6,minmax(0,1fr));
  gap:6px!important;
  padding:10px 12px 12px!important;
  margin:0!important;
  border-top:1px solid var(--p-border);
  background:color-mix(in srgb,var(--p-bg) 30%,var(--p-surface-2));
}
.tp27-premium .tunnel-card .tunnel-actions .btn{
  min-width:0!important;
  width:100%!important;
  padding:7px 5px!important;
  border-radius:9px!important;
  font-size:10px!important;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.tp27-premium .tunnel-card .tunnel-actions .primary{box-shadow:none!important}
.tp27-premium .tunnel-card .tunnel-actions .bm-btn{border-color:#22d3ee55!important}
.tp27-premium .tunnel-card .tunnel-actions .premium-rebuild-btn{border-color:#8b5cf666!important}
.tp27-premium .tunnel-card:hover{transform:translateY(-2px)!important;box-shadow:0 16px 36px #00000026!important}
@media(max-width:1250px){
  .tp27-premium .tunnel-card .tunnel-actions{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media(max-width:760px){
  .tp27-premium .tunnel-card .bm-strip{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tp27-premium .tunnel-card .tunnel-actions{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tp27-premium .tunnel-card .badge{max-width:100%}
}
`;
document.head.appendChild(style);
document.documentElement.classList.add('tp27-ui-stabilizing');

const sleep=ms=>new Promise(r=>setTimeout(r,ms));

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
  const cleanups=[freezeList('agentList'),freezeList('exitList')].filter(Boolean);
  return ()=>{for(const fn of cleanups.reverse())try{fn()}catch(_){}};
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
  const base=window.loadStatus;
  if(typeof base!=='function'||base.__tp27PortStable)return;
  const wrapped=async function(){
    const result=await base.apply(this,arguments);
    normalizePortBadges();
    return result;
  };
  wrapped.__tp27PortStable=true;
  window.loadStatus=wrapped;
}

function installRefreshHook(){
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
  installLoadStatusHook();
  installRefreshHook();
  await waitForStableUi(BOOT_MAX_MS);
  normalizePortBadges();
  document.documentElement.classList.remove('tp27-ui-stabilizing');
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',()=>setTimeout(releaseBoot,0),{once:true});
}else{
  setTimeout(releaseBoot,0);
}

window.addEventListener('pageshow',()=>{
  installLoadStatusHook();
  installRefreshHook();
  normalizePortBadges();
});
})();