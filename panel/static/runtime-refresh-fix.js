(()=>{
'use strict';

const FIRST_PAINT_MAX_MS=8000;
const root=document.documentElement;
root.classList.add('tp27-first-paint-lock');
root.classList.remove('tp27-first-paint-ready');

const firstPaintStyle=document.createElement('style');
firstPaintStyle.id='tp27FirstPaintLockStyle';
firstPaintStyle.textContent=`
html.tp27-first-paint-lock body{visibility:hidden!important;opacity:0!important}
html.tp27-first-paint-lock:before{content:'TP27 Control';position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;background:#0a0810;color:#a9a0b8;font:700 14px Tahoma,Arial,sans-serif;letter-spacing:.25px}
`;
document.head.appendChild(firstPaintStyle);

/* Light mode must never inherit dark fixed button backgrounds from feature CSS. */
const lightModeStyle=document.createElement('style');
lightModeStyle.id='tp27LightButtonFixStyle';
lightModeStyle.textContent=`
html[data-theme="light"] body.tp27-premium .btn:not(.primary):not(.danger){
  background:#f8f5fd!important;
  color:#34283f!important;
  border-color:#d8cee7!important;
  box-shadow:none!important;
}
html[data-theme="light"] body.tp27-premium .btn:not(.primary):not(.danger):hover{
  background:#f1ebf9!important;
  color:#241b2e!important;
  border-color:#bfaed7!important;
}
html[data-theme="light"] body.tp27-premium .premium-stability-btn{
  background:#f3eef9!important;
  color:#4b3a60!important;
  border-color:#c9b9df!important;
}
html[data-theme="light"] body.tp27-premium .ops-btn-on{
  background:#ecfdf5!important;
  color:#047857!important;
  border-color:#86efac!important;
}
html[data-theme="light"] body.tp27-premium .srv-server-test{
  background:#ecfeff!important;
  color:#0e7490!important;
  border-color:#67e8f9!important;
}
html[data-theme="light"] body.tp27-premium .danger{
  background:#fff1f2!important;
  color:#be123c!important;
  border-color:#fecdd3!important;
}
html[data-theme="light"] body.tp27-premium .btn:disabled{
  opacity:.52!important;
}
`;
document.head.appendChild(lightModeStyle);

function firstPaintReady(){
  const body=document.body;
  if(!body||!body.classList.contains('tp27-premium'))return false;
  if(!document.getElementById('premiumCommand'))return false;
  const tunnelCards=[...document.querySelectorAll('#exitList .tunnel-card')];
  const agentCards=[...document.querySelectorAll('#agentList .agent-card')];
  const tunnelsReady=tunnelCards.every(card=>{
    const actions=card.querySelector('.tunnel-actions');
    return !!card.querySelector('.premium-card-meta') && !!actions && !!actions.querySelector('.bm-btn') && !!actions.querySelector('.premium-stability-btn');
  });
  const agentsReady=agentCards.every(card=>!!card.querySelector('.srv-speed-wrap'));
  return tunnelsReady&&agentsReady;
}

function releaseFirstPaint(reason){
  root.classList.add('tp27-first-paint-ready');
  root.classList.remove('tp27-first-paint-lock');
  document.body?.setAttribute('data-tp27-first-paint',String(reason||'ready'));
}

async function waitForFirstPaint(){
  const end=Date.now()+FIRST_PAINT_MAX_MS;
  while(Date.now()<end){
    if(firstPaintReady()){
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      releaseFirstPaint('ready');
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,60));
  }
  releaseFirstPaint('failsafe');
}

function revealLegacy(reason){
  const body=document.body;
  if(!body)return;
  if(body.classList.contains('tp27-premium')){
    if(String(reason)==='timeout')releaseFirstPaint('premium-timeout');
    return;
  }
  body.style.setProperty('visibility','visible','important');
  body.style.setProperty('opacity','1','important');
  body.dataset.tp27BootRecovery=String(reason||'fallback');
  releaseFirstPaint(reason||'legacy-fallback');
}

window.addEventListener('error',()=>{
  window.setTimeout(()=>{
    if(!document.body?.classList.contains('tp27-premium'))revealLegacy('script-error');
  },250);
});
window.addEventListener('unhandledrejection',()=>{
  window.setTimeout(()=>{
    if(!document.body?.classList.contains('tp27-premium'))revealLegacy('promise-error');
  },250);
});
window.setTimeout(()=>revealLegacy('timeout'),FIRST_PAINT_MAX_MS+250);
waitForFirstPaint();

function sanitizeBenchmarkModal(){
  const msg=document.getElementById('bmResultMsg');
  const btn=document.getElementById('bmSuggestSwitch');
  if(btn){
    if(btn.textContent!=='تعویض امن')btn.textContent='تعویض امن';
    btn.title='مسیر فعلی تا سالم شدن مسیر جایگزین حذف نمی‌شود.';
  }
  if(!msg)return;
  const text=String(msg.textContent||'');
  const rawIperf=/control socket has closed unexpectedly|bits_per_second|retransmits|num_streams|sock_bufsize|\"streams\"|\"socket\"/i.test(text);
  if(rawIperf||text.length>500){
    msg.textContent='تست کامل نشد: ارتباط iperf داخل Tunnel هنگام تست قطع شد. نتیجه قبلی حفظ شده است؛ این معمولاً نشانه ناپایداری یا افت شدید همان مسیر است.';
  }
}

window.setInterval(sanitizeBenchmarkModal,500);

/*
 * ui-stability keeps the previous list as a visible shadow while refreshAll
 * rebuilds the hidden live list. Visibility:hidden still participates in
 * layout, so the hidden list used to shrink/grow the section for a moment.
 * Lock the live list geometry for the whole shadow interval; only data changes
 * underneath it, and the page geometry is released after the final UI is ready.
 */
const refreshGeometryState=new WeakMap();

function lockRefreshGeometry(el){
  if(!el||refreshGeometryState.has(el))return;
  const rect=el.getBoundingClientRect();
  refreshGeometryState.set(el,{
    height:el.style.getPropertyValue('height'),
    heightPriority:el.style.getPropertyPriority('height'),
    minHeight:el.style.getPropertyValue('min-height'),
    minHeightPriority:el.style.getPropertyPriority('min-height'),
    maxHeight:el.style.getPropertyValue('max-height'),
    maxHeightPriority:el.style.getPropertyPriority('max-height'),
    overflow:el.style.getPropertyValue('overflow'),
    overflowPriority:el.style.getPropertyPriority('overflow')
  });
  const px=Math.max(0,Math.ceil(rect.height))+'px';
  el.style.setProperty('height',px,'important');
  el.style.setProperty('min-height',px,'important');
  el.style.setProperty('max-height',px,'important');
  el.style.setProperty('overflow','hidden','important');
}

function restoreStyleProperty(el,name,value,priority){
  if(value)el.style.setProperty(name,value,priority||'');
  else el.style.removeProperty(name);
}

function unlockRefreshGeometry(el){
  const prev=refreshGeometryState.get(el);
  if(!prev)return;
  refreshGeometryState.delete(el);
  restoreStyleProperty(el,'height',prev.height,prev.heightPriority);
  restoreStyleProperty(el,'min-height',prev.minHeight,prev.minHeightPriority);
  restoreStyleProperty(el,'max-height',prev.maxHeight,prev.maxHeightPriority);
  restoreStyleProperty(el,'overflow',prev.overflow,prev.overflowPriority);
}

function watchRefreshGeometry(id){
  const el=document.getElementById(id);
  if(!el||el.dataset.tp27GeometryWatch==='1')return;
  el.dataset.tp27GeometryWatch='1';
  const sync=()=>{
    if(el.classList.contains('tp27-refresh-live-hidden'))lockRefreshGeometry(el);
    else unlockRefreshGeometry(el);
  };
  new MutationObserver(sync).observe(el,{attributes:true,attributeFilter:['class']});
  sync();
}

function armRefreshGeometry(){
  watchRefreshGeometry('agentList');
  watchRefreshGeometry('exitList');
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',armRefreshGeometry,{once:true});
else armRefreshGeometry();
window.addEventListener('pageshow',armRefreshGeometry);

const nativeSetInterval=window.setInterval.bind(window);
window.setInterval=function(fn,delay,...args){
  if(Number(delay)===10000&&typeof fn==='function'&&fn.name==='refreshAll'){
    return nativeSetInterval(()=>{
      const current=window.refreshAll;
      if(typeof current==='function')return current();
      return fn();
    },delay,...args);
  }
  return nativeSetInterval(fn,delay,...args);
};
})();