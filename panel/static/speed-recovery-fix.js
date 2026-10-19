(()=>{
'use strict';

/*
 * TP27 Speed Test recovery shim.
 *
 * Older server-speed-ui.js versions tried the benchmark first. On exits
 * without iperf3, the old sandboxed Agent could fail inside apt before the UI
 * decided that an Agent upgrade was required. This shim intercepts the per-
 * server speed button in capture phase, performs a managed Agent upgrade when
 * any bootstrap/privilege error is seen, waits for a fresh heartbeat, then
 * retries the benchmark. Data-plane services are never restarted here.
 */

const PORT=52701;
const DURATION=5;
const PARALLEL=2;
const RESULTS_KEY='tp27_benchmark_results_v1';
const busy=new Set();

function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
function strip(v){return String(v||'').split('/')[0].trim()}
function state(){try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}}
function agentRows(){try{return agents||[]}catch(_){return[]}}

async function req(path,opt={}){
  const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  let data={};
  try{data=await r.json()}catch(_){data={detail:await r.text().catch(()=> '')}}
  if(!r.ok)throw new Error(data.detail||data.error||`HTTP ${r.status}`);
  return data;
}

async function waitCommand(id,timeout=90000){
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

async function waitFresh(agentId,lastSeen,timeout=75000){
  const end=Date.now()+timeout;
  while(Date.now()<end){
    const a=await getAgent(agentId);
    if(a&&a.online&&Number(a.last_seen||0)>Number(lastSeen||0))return a;
    await sleep(1500);
  }
  throw new Error('Agent بعد از ارتقا Heartbeat جدید ارسال نکرد');
}

async function upgradeAgent(agentId){
  const before=await getAgent(agentId);
  if(!before||!before.online)throw new Error('Agent سرور خارج آفلاین است');
  const seen=Number(before.last_seen||0);
  const c=await req('/api/agents/'+encodeURIComponent(agentId)+'/commands',{
    method:'POST',body:JSON.stringify({action:'upgrade_agent',args:{}})
  });
  await waitCommand(c.command_id,90000);
  await sleep(8000);
  await waitFresh(agentId,seen,75000);
}

async function benchmark(x){
  if(!x||!x.healthy)throw new Error('Tunnel سالم برای این سرور وجود ندارد');
  const target=strip(x.local_ip), source=strip(x.remote_ip);
  if(!target||!source)throw new Error('IP داخلی Tunnel کامل نیست');
  const c=await req('/api/agents/'+encodeURIComponent(x.agent_id)+'/commands',{
    method:'POST',body:JSON.stringify({action:'status',args:{
      benchmark_v1:true,id:x.id,target_ip:target,source_ip:source,
      port:PORT,duration:DURATION,parallel:PARALLEL
    }})
  });
  const r=await waitCommand(c.command_id,75000);
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
  try{return await benchmark(x)}catch(e){
    if(!bootstrapError(e))throw e;
    await upgradeAgent(x.agent_id);
    await sleep(1500);
    return benchmark(x);
  }
}

function tunnelForAgent(agentId){
  return (state().exits||[]).filter(x=>x.agent_id===agentId&&x.healthy);
}

async function run(agentId,button){
  if(busy.has(agentId))return;
  busy.add(agentId);
  const old=button.textContent;
  button.disabled=true;
  button.textContent='آماده‌سازی و تست...';
  try{
    const list=tunnelForAgent(agentId);
    if(!list.length)throw new Error('Tunnel سالم برای این سرور وجود ندارد');
    for(const x of list)await benchmarkAuto(x);
    button.textContent='تست شد';
    setTimeout(()=>{button.textContent=old||'تست سرعت';button.disabled=false},1200);
  }catch(e){
    button.disabled=false;
    button.textContent=old||'تست سرعت';
    alert('تست سرعت ناموفق بود: '+(e.message||e));
  }finally{busy.delete(agentId)}
}

document.addEventListener('click',e=>{
  const btn=e.target&&e.target.closest&&e.target.closest('.srv-server-test');
  if(!btn)return;
  const card=btn.closest('.agent-card');
  const aid=card&&card.dataset&&card.dataset.agentId;
  if(!aid)return;
  e.preventDefault();
  e.stopImmediatePropagation();
  run(aid,btn);
},true);

})();
