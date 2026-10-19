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
.tp27-refresh-shadow{position:absolute!important;z-index:20!important;pointer-events:none!important;margin-top:0!important;width:auto}
.tp27-refresh-live-hidden{visibility:hidden!important}
.tp27-premium .tunnel-card .exit-main{width:100%;min-width:0}
.tp27-premium .tunnel-card .badges{width:100%;display:flex!important;flex-wrap:wrap!important;gap:6px!important;align-items:center;justify-content:flex-start}
.tp27-premium .tunnel-card .badge{display:inline-flex!important;align-items:center;justify-content:space-between;gap:7px;min-width:128px;white-space:nowrap;direction:ltr;text-align:left}
.tp27-premium .tunnel-card .badge b{direction:rtl;text-align:right}
@media(max-width:760px){.tp27-premium .tunnel-card .badge{min-width:calc(50% - 4px);flex:1 1 calc(50% - 4px)}}
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

function freezeList(id){
  const live=document.getElementById(id);
  if(!live||!live.parentElement||!live.children.length)return null;
  const host=live.parentElement;
  const prevPosition=host.style.position;
  if(getComputedStyle(host).position==='static')host.style.position='relative';
  const shadow=live.cloneNode(true);
  stripIds(shadow);
  shadow.classList.add('tp27-refresh-shadow');
  shadow.style.top=live.offsetTop+'px';
  shadow.style.left=live.offsetLeft+'px';
  shadow.style.width=live.offsetWidth+'px';
  shadow.style.minHeight=live.offsetHeight+'px';
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

function uiReady(){
  const tunnelCards=[...document.querySelectorAll('#exitList .tunnel-card')];
  const agentCards=[...document.querySelectorAll('#agentList .agent-card')];
  const tunnelsReady=tunnelCards.every(card=>{
    const actions=card.querySelector('.tunnel-actions');
    return !!card.querySelector('.premium-card-meta') && !!actions && !!actions.querySelector('.bm-btn') && !!actions.querySelector('.premium-stability-btn');
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
