(()=>{
'use strict';

/*
 * TP27 Speed Test recovery + stable UI state.
 * server-speed-ui.js redraws server-card actions periodically. A previous
 * recovery version kept only the original button element, so the visible
 * button was replaced while the test was still running and looked dead.
 *
 * This version stores state per Agent and continuously re-applies that state
 * to the currently rendered button. The data plane is never restarted here.
 */

const PORT=52701;
const DURATION=5;
const PARALLEL=2;
const RESULTS_KEY='tp27_benchmark_results_v1';
const jobs=new Map(); // agent_id -> {phase,text,started,error}

function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
function strip(v){return String(v||'').split('/')[0].trim()}
function state(){try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}}

async function req(path,opt={}){
  const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  let data={};
  try{data=await r.json()}catch(_){data={detail:await r.text().catch(()=> '')}}
  if(!r.ok)throw new Error(data.detail||data.error||`HTTP ${r.status}`);
  return data;
}

async function waitCommand(id,timeout=100000){
  const end=Date.now()+timeout;
  while(Date.now()<end){
    const r=await req('/api/agents/commands/'+encodeURIComponent(id));
    if(r.state==='done'){
      if(!r.ok)throw new Error(r.error||'فرمان Agent ناموفق بود');
      return r.result||{};
    }
    if(r.state==='failed')throw new Error(r.error||'فرمان Agent ناموفق بود');
    await sleep(1000);
  }
  throw new Error('مهلت فرمان Agent تمام شد');
}

async function getAgent(agentId){
  const d=await req('/api/agents');
  return (d.agents||[]).find(a=>a.agent_id===agentId)||null;
}

async function waitFresh(agentId,lastSeen,timeout=80000){
  const end=Date.now()+timeout;
  while(Date.now()<end){
    const a=await getAgent(agentId);
    if(a&&a.online&&Number(a.last_seen||0)>Number(lastSeen||0))return a;
    await sleep(1500);
  }
  throw new Error('Agent بعد از ارتقا Heartbeat جدید ارسال نکرد');
}

function setJob(agentId,phase,text,error=''){
  jobs.set(agentId,{phase,text,error,started:jobs.get(agentId)?.started||Date.now()});
  syncUi();
}

function clearJob(agentId){
  jobs.delete(agentId);
  syncUi();
}

function syncUi(){
  const cards=[...document.querySelectorAll('#agentList .agent-card')];
  for(const card of cards){
    const aid=card.dataset&&card.dataset.agentId;
    if(!aid)continue;
    const job=jobs.get(aid);
    const btn=card.querySelector('.srv-server-test');
    const status=card.querySelector('.srv-speed-status');
    if(!btn)continue;

    if(job){
      btn.disabled=true;
      btn.classList.add('srv-speed-running');
      btn.textContent=job.text||'در حال تست...';
      if(status){
        status.textContent=job.error?`خطا: ${job.error}`:(job.phase==='upgrade'?'در حال آماده‌سازی Agent':job.phase==='benchmark'?'در حال سنجش داخل Tunnel':'در حال پردازش');
        status.title=job.error||'';
      }
      card.dataset.speedTestBusy='1';
    }else{
      if(card.dataset.speedTestBusy==='1'){
        btn.disabled=false;
        btn.classList.remove('srv-speed-running');
        btn.textContent='تست سرعت';
        delete card.dataset.speedTestBusy;
      }
    }
  }
}

async function upgradeAgent(agentId){
  const before=await getAgent(agentId);
  if(!before||!before.online)throw new Error('Agent سرور خارج آفلاین است');
  const seen=Number(before.last_seen||0);
  setJob(agentId,'upgrade','آماده‌سازی Agent...');
  const c=await req('/api/agents/'+encodeURIComponent(agentId)+'/commands',{
    method:'POST',body:JSON.stringify({action:'upgrade_agent',args:{}})
  });
  await waitCommand(c.command_id,100000);
  setJob(agentId,'upgrade','اتصال مجدد Agent...');
  await sleep(8000);
  await waitFresh(agentId,seen,80000);
}

async function benchmark(x){
  if(!x||!x.healthy)throw new Error('Tunnel سالم برای این سرور وجود ندارد');
  const target=strip(x.local_ip), source=strip(x.remote_ip);
  if(!target||!source)throw new Error('IP داخلی Tunnel کامل نیست');
  setJob(x.agent_id,'benchmark','در حال تست سرعت...');
  const c=await req('/api/agents/'+encodeURIComponent(x.agent_id)+'/commands',{
    method:'POST',body:JSON.stringify({action:'status',args:{
      benchmark_v1:true,id:x.id,target_ip:target,source_ip:source,
      port:PORT,duration:DURATION,parallel:PARALLEL
    }})
  });
  const r=await waitCommand(c.command_id,80000);
  if(!r.benchmark_v1)throw new Error('BENCHMARK_NOT_READY');

  let all={};
  try{all=JSON.parse(localStorage.getItem(RESULTS_KEY)||'{}')||{}}catch(_){all={}}
  all[x.id]={...r,saved_at:Date.now()};
  localStorage.setItem(RESULTS_KEY,JSON.stringify(all));
  window.dispatchEvent(new Event('tp27:benchmark-results-updated'));
  return r;
}

function bootstrapError(err){
  const s=String(err&&err.message||err||'');
  return /BENCHMARK_NOT_READY|benchmark|iperf3|apt|seteuid|setresuid|operation not permitted|method http has died|sub-process http|unsupported|پشتیبانی|نصب خودکار/i.test(s);
}

async function benchmarkAuto(x){
  try{
    return await benchmark(x);
  }catch(e){
    if(!bootstrapError(e))throw e;
    await upgradeAgent(x.agent_id);
    await sleep(1200);
    return benchmark(x);
  }
}

function tunnelsForAgent(agentId){
  return (state().exits||[]).filter(x=>x.agent_id===agentId&&x.healthy);
}

async function run(agentId){
  if(jobs.has(agentId))return;
  setJob(agentId,'start','شروع تست...');
  try{
    const list=tunnelsForAgent(agentId);
    if(!list.length)throw new Error('Tunnel سالم برای این سرور وجود ندارد');

    for(let i=0;i<list.length;i++){
      const x=list[i];
      setJob(agentId,'benchmark',list.length>1?`تست ${i+1}/${list.length}...`:'در حال تست سرعت...');
      await benchmarkAuto(x);
    }

    setJob(agentId,'done','تست کامل شد');
    setTimeout(()=>clearJob(agentId),1400);
  }catch(e){
    const msg=String(e&&e.message||e||'خطای ناشناخته');
    setJob(agentId,'error','تلاش مجدد',msg.slice(0,180));
    console.error('TP27 speed test failed',e);
    setTimeout(()=>clearJob(agentId),3500);
    alert('تست سرعت ناموفق بود: '+msg);
  }
}

// Capture-phase delegation intentionally owns direct server-card speed clicks.
// The old onclick is stopped so a test cannot run twice.
document.addEventListener('click',e=>{
  const btn=e.target&&e.target.closest&&e.target.closest('.srv-server-test');
  if(!btn)return;
  const card=btn.closest('.agent-card');
  const aid=card&&card.dataset&&card.dataset.agentId;
  if(!aid)return;
  e.preventDefault();
  e.stopImmediatePropagation();
  run(aid);
},true);

// server-speed-ui.js rebuilds button DOM every 1.5s. Keep current UI in sync
// with the real running job so the button never appears inert/disconnected.
setInterval(syncUi,250);
window.addEventListener('tp27:benchmark-results-updated',syncUi);
window.tp27SpeedTestAgent=run;

})();
