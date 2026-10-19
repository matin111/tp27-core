(()=>{
'use strict';

const AUTO_RESULTS_KEY='tp27_benchmark_results_v1';
const AUTO_PORT=52701;
let fleetBootstrapBusy=false;

function el(id){return document.getElementById(id)}
function pause(ms){return new Promise(r=>setTimeout(r,ms))}

async function req(path,opt={}){
  const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  let data={};
  try{data=await r.json()}catch(_){data={detail:await r.text().catch(()=> '')}}
  if(!r.ok)throw new Error(data.detail||data.error||`HTTP ${r.status}`);
  return data;
}

async function waitCmd(id,timeoutMs=70000){
  const end=Date.now()+timeoutMs;
  while(Date.now()<end){
    const x=await req('/api/agents/commands/'+encodeURIComponent(id));
    if(x.state==='done'){
      if(!x.ok)throw new Error(x.error||'فرمان Agent ناموفق بود');
      return x.result||{};
    }
    if(x.state==='failed')throw new Error(x.error||'فرمان Agent ناموفق بود');
    await pause(1000);
  }
  throw new Error('مهلت فرمان Agent تمام شد');
}

async function waitAgentOnline(agentId,timeoutMs=70000){
  const end=Date.now()+timeoutMs;
  while(Date.now()<end){
    const data=await req('/api/agents');
    const a=(data.agents||[]).find(x=>x.agent_id===agentId);
    if(a&&a.online)return a;
    await pause(1500);
  }
  throw new Error('Agent بعد از ارتقا آنلاین نشد');
}

async function upgradeAgent(agentId){
  const created=await req('/api/agents/'+encodeURIComponent(agentId)+'/commands',{
    method:'POST',
    body:JSON.stringify({action:'upgrade_agent',args:{}}),
  });
  await waitCmd(created.command_id,90000);
  // upgrade_agent schedules its own restart a few seconds later.
  await pause(8000);
  return waitAgentOnline(agentId,70000);
}

function stripCidr(v){return String(v||'').split('/')[0].trim()}

async function benchmarkTunnel(x){
  const target=stripCidr(x.local_ip);
  const source=stripCidr(x.remote_ip);
  if(!target||!source)throw new Error('IP داخلی Tunnel کامل نیست');
  const created=await req('/api/agents/'+encodeURIComponent(x.agent_id)+'/commands',{
    method:'POST',
    body:JSON.stringify({
      action:'status',
      args:{benchmark_v1:true,id:x.id,target_ip:target,source_ip:source,port:AUTO_PORT,duration:3,parallel:1},
    }),
  });
  const r=await waitCmd(created.command_id,65000);
  if(!r.benchmark_v1)throw new Error('Benchmark addon روی Agent فعال نشد');
  let saved={};
  try{saved=JSON.parse(localStorage.getItem(AUTO_RESULTS_KEY)||'{}')||{}}catch(_){saved={}}
  saved[x.id]={...r,saved_at:Date.now()};
  localStorage.setItem(AUTO_RESULTS_KEY,JSON.stringify(saved));
  return r;
}

function setMsg(text,kind=''){
  const box=el('benchmarkAutoMsg');
  if(!box)return;
  box.textContent=text;
  box.dataset.kind=kind;
}

function summaryLine(r){
  const p=Number(r.ping_ms);
  const d=Number(r.download_mbps);
  const u=Number(r.upload_mbps);
  return `${Number.isFinite(p)?p.toFixed(1):'-'}ms · ↓${Number.isFinite(d)?d.toFixed(1):'-'} · ↑${Number.isFinite(u)?u.toFixed(1):'-'} Mbps`;
}

async function bootstrapAndTestFleet(){
  if(fleetBootstrapBusy)return;
  fleetBootstrapBusy=true;
  const btn=el('benchmarkAutoFleetBtn');
  if(btn){btn.disabled=true;btn.textContent='در حال آماده‌سازی...'}
  try{
    const [statusData,agentData]=await Promise.all([req('/api/status'),req('/api/agents')]);
    const exits=statusData.exits||[];
    const agents=agentData.agents||[];
    const agentIds=[...new Set(exits.map(x=>x.agent_id).filter(Boolean))];
    if(!exits.length)throw new Error('Tunnel ثبت‌شده‌ای وجود ندارد');
    if(!agentIds.length)throw new Error('Tunnelها Agent متصل ندارند');

    const offline=agentIds.filter(id=>!agents.find(a=>a.agent_id===id&&a.online));
    if(offline.length)throw new Error(`ابتدا Agentهای آفلاین را آنلاین کنید: ${offline.length} مورد`);

    let n=0;
    for(const aid of agentIds){
      n++;
      const a=agents.find(x=>x.agent_id===aid)||{};
      setMsg(`مرحله ۱/۲ · ارتقای خودکار Agent ${n}/${agentIds.length} · ${a.hostname||a.public_ip||aid}`);
      await upgradeAgent(aid);
    }

    // Let all heartbeats settle after the last scheduled restart.
    await pause(3000);
    const freshStatus=await req('/api/status');
    const freshAgents=await req('/api/agents');
    const onlineMap=new Map((freshAgents.agents||[]).map(a=>[a.agent_id,!!a.online]));
    const healthy=(freshStatus.exits||[]).filter(x=>x.healthy&&x.agent_id&&onlineMap.get(x.agent_id));
    if(!healthy.length)throw new Error('پس از ارتقا Tunnel سالمی برای Benchmark وجود ندارد');

    const ok=[];
    const failed=[];
    for(let i=0;i<healthy.length;i++){
      const x=healthy[i];
      setMsg(`مرحله ۲/۲ · تست واقعی ${i+1}/${healthy.length} · ${x.id} · Ping / Download / Upload`);
      try{
        const r=await benchmarkTunnel(x);
        ok.push({id:x.id,result:r});
      }catch(e){
        failed.push({id:x.id,error:e.message||String(e)});
      }
    }

    if(typeof refreshAll==='function'){
      try{await refreshAll()}catch(_){ }
    }
    window.dispatchEvent(new Event('tp27:benchmark-results-updated'));

    const detail=ok.map(x=>`${x.id}: ${summaryLine(x.result)}`).join(' | ');
    if(failed.length){
      setMsg(`آماده‌سازی تمام شد · موفق ${ok.length}/${healthy.length} · ناموفق ${failed.length}. ${detail}`,'warn');
    }else{
      setMsg(`همه ${ok.length} Tunnel سالم آماده و تست شدند. ${detail}`,'ok');
    }
  }catch(e){
    setMsg('خطا: '+(e.message||e),'bad');
  }finally{
    fleetBootstrapBusy=false;
    if(btn){btn.disabled=false;btn.textContent='آماده‌سازی خودکار + تست همه'}
  }
}

function inject(){
  if(el('benchmarkAutoFleetBtn'))return true;
  const actions=document.querySelector('.premium-actions');
  if(!actions)return false;
  const btn=document.createElement('button');
  btn.id='benchmarkAutoFleetBtn';
  btn.className='btn primary';
  btn.type='button';
  btn.textContent='آماده‌سازی خودکار + تست همه';
  btn.title='پنل Agent همه سرورهای خارج را خودش ارتقا می‌دهد و سپس Ping، Download و Upload همه Tunnelهای سالم را تست می‌کند.';
  btn.onclick=bootstrapAndTestFleet;
  actions.prepend(btn);

  const msg=document.createElement('div');
  msg.id='benchmarkAutoMsg';
  msg.style.cssText='font-size:10px;line-height:1.8;color:var(--p-muted);margin-top:7px;grid-column:1/-1';
  const tools=document.querySelector('#premiumCommand .premium-tools')||actions.parentElement;
  if(tools)tools.appendChild(msg);
  return true;
}

let tries=0;
const timer=setInterval(()=>{
  tries++;
  if(inject()||tries>60)clearInterval(timer);
},250);

window.tp27BootstrapBenchmarkFleet=bootstrapAndTestFleet;
})();
