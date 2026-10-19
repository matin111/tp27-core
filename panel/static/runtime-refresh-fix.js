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
