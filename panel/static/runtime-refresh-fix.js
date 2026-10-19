(()=>{
'use strict';
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
