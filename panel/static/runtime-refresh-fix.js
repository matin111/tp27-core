(()=>{
'use strict';

const FIRST_PAINT_MAX_MS=8000;
const root=document.documentElement;
root.classList.add('tp27-first-paint-lock');

const firstPaintStyle=document.createElement('style');
firstPaintStyle.id='tp27FirstPaintLockStyle';
firstPaintStyle.textContent=`
html.tp27-first-paint-lock body{visibility:hidden!important;opacity:0!important}
html.tp27-first-paint-lock:before{content:'TP27 Control';position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;background:#0a0810;color:#a9a0b8;font:700 14px Tahoma,Arial,sans-serif;letter-spacing:.25px}
`;
document.head.appendChild(firstPaintStyle);

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
  if(!root.classList.contains('tp27-first-paint-lock'))return;
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
