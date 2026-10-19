(()=>{
'use strict';

function revealLegacy(reason){
  const body=document.body;
  if(!body||body.classList.contains('tp27-premium'))return;
  body.style.setProperty('visibility','visible','important');
  body.style.setProperty('opacity','1','important');
  body.dataset.tp27BootRecovery=String(reason||'fallback');
}

window.addEventListener('error',()=>revealLegacy('script-error'));
window.addEventListener('unhandledrejection',()=>revealLegacy('promise-error'));
window.setTimeout(()=>revealLegacy('timeout'),1200);

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
