(()=>{
'use strict';

const SPEED_RESULTS_KEY='tp27_benchmark_results_v1';
const SPEED_PORT=52701;
const TEST_DURATION=5;
const TEST_PARALLEL=2;
const SAFE_DURATION=4;
const SAFE_PARALLEL=1;
const testingAgents=new Set();
let fleetBusy=false;

function $s(id){return document.getElementById(id)}
function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
function fmt(v,d=1){const n=Number(v);return Number.isFinite(n)?n.toFixed(d):'-'}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function stripCidr(v){return String(v||'').split('/')[0].trim()}
function statusSafe(){try{return currentStatus||{exits:[]}}catch(_){return{exits:[]}}}
function agentsSafe(){try{return agents||[]}catch(_){return[]}}
function readResults(){try{const x=JSON.parse(localStorage.getItem(SPEED_RESULTS_KEY)||'{}');return x&&typeof x==='object'?x:{}}catch(_){return{}}}
function saveResult(id,r){const all=readResults();all[id]={...r,saved_at:Date.now()};localStorage.setItem(SPEED_RESULTS_KEY,JSON.stringify(all));window.dispatchEvent(new Event('tp27:benchmark-results-updated'));decorateServerCards()}
function tunnelsForAgent(agentId){return (statusSafe().exits||[]).filter(x=>x.agent_id===agentId)}
function qualityLabel(v){return({excellent:'عالی',good:'خوب',fair:'متوسط',poor:'ضعیف'})[v]||'-'}
function qualityClass(v){return v==='excellent'||v==='good'?'srv-speed-good':v==='fair'?'srv-speed-warn':'srv-speed-bad'}

function errorText(e){return String(e&&e.message||e||'خطای نامشخص')}
function benchmarkNotReady(msg){return /BENCHMARK_NOT_READY|پشتیبانی|unsupported|benchmark/i.test(String(msg||''))}
function transientIperf(msg){
  return /iperf|bits_per_second|"sender"|socket|broken pipe|connection reset|connection refused|timed out|timeout|resource temporarily unavailable|unable to|network is unreachable/i.test(String(msg||''));
}
function friendlyBenchmarkError(e){
  let msg=errorText(e).replace(/\s+/g,' ').trim();
  if(/bits_per_second|"sender"|"socket"|"streams"|"intervals"/.test(msg)){
    return 'اندازه‌گیری iperf3 کامل نشد؛ تست پایدار هم نتیجه معتبر نداد.';
  }
  if(msg.length>240)msg=msg.slice(0,237)+'...';
  return msg||'تست سرعت ناموفق بود';
}

async function req(path,opt={}){
  const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  let data={};
  try{data=await r.json()}catch(_){data={detail:await r.text().catch(()=> '')}}
  if(!r.ok)throw new Error(data.detail||data.error||`HTTP ${r.status}`);
  return data;
}

async function waitCommand(id,timeoutMs=80000){
  const end=Date.now()+timeoutMs;
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

async function waitAgentOnline(agentId,timeoutMs=70000){
  const end=Date.now()+timeoutMs;
  while(Date.now()<end){
    const data=await req('/api/agents');
    const row=(data.agents||[]).find(x=>x.agent_id===agentId);
    if(row&&row.online)return row;
    await sleep(1500);
  }
  throw new Error('Agent بعد از ارتقا آنلاین نشد');
}

async function upgradeAgent(agentId){
  const created=await req('/api/agents/'+encodeURIComponent(agentId)+'/commands',{
    method:'POST',body:JSON.stringify({action:'upgrade_agent',args:{}})
  });
  await waitCommand(created.command_id,90000);
  await sleep(7000);
  await waitAgentOnline(agentId,70000);
}

async function benchmarkTunnel(x,profile={}){
  if(!x||!x.healthy)throw new Error('Tunnel سالم برای این سرور وجود ندارد');
  const target=stripCidr(x.local_ip);
  const source=stripCidr(x.remote_ip);
  if(!target||!source)throw new Error('IP داخلی Tunnel کامل نیست');
  const duration=Math.max(2,Math.min(Number(profile.duration||TEST_DURATION),10));
  const parallel=Math.max(1,Math.min(Number(profile.parallel||TEST_PARALLEL),4));
  const created=await req('/api/agents/'+encodeURIComponent(x.agent_id)+'/commands',{
    method:'POST',
    body:JSON.stringify({
      action:'status',
      args:{benchmark_v1:true,id:x.id,target_ip:target,source_ip:source,port:SPEED_PORT,duration,parallel}
    })
  });
  const r=await waitCommand(created.command_id,70000);
  if(!r.benchmark_v1)throw new Error('BENCHMARK_NOT_READY');
  saveResult(x.id,r);
  return r;
}

async function benchmarkTunnelAuto(x){
  let upgraded=false;
  try{
    return await benchmarkTunnel(x);
  }catch(e){
    const msg=errorText(e);
    if(benchmarkNotReady(msg)){
      await upgradeAgent(x.agent_id);
      upgraded=true;
      try{return await benchmarkTunnel(x)}catch(second){e=second}
    }
    const retryMsg=errorText(e);
    if(transientIperf(retryMsg)){
      await sleep(1200);
      try{
        return await benchmarkTunnel(x,{duration:SAFE_DURATION,parallel:SAFE_PARALLEL});
      }catch(last){
        const err=new Error(friendlyBenchmarkError(last));
        err.cause=last;
        throw err;
      }
    }
    if(!upgraded&&benchmarkNotReady(retryMsg)){
      await upgradeAgent(x.agent_id);
      return benchmarkTunnel(x,{duration:SAFE_DURATION,parallel:SAFE_PARALLEL});
    }
    throw new Error(friendlyBenchmarkError(e));
  }
}

function latestResultForAgent(agentId){
  const results=readResults();
  const rows=tunnelsForAgent(agentId).map(x=>({tunnel:x,result:results[x.id]})).filter(x=>x.result);
  rows.sort((a,b)=>Number(b.result.saved_at||0)-Number(a.result.saved_at||0));
  return rows;
}

function resultRow(tunnel,r){
  const ts=r&&r.saved_at?new Date(r.saved_at).toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'}):'-';
  return `<div class="srv-speed-row">
    <div class="srv-speed-title"><b>${esc(tunnel.id)}</b><small>${esc(ts)}</small></div>
    <div class="srv-speed-metric download"><span>دانلود</span><strong>↓ ${fmt(r.download_mbps)} <small>Mbps</small></strong></div>
    <div class="srv-speed-metric upload"><span>آپلود</span><strong>↑ ${fmt(r.upload_mbps)} <small>Mbps</small></strong></div>
    <div class="srv-speed-metric ping"><span>Ping</span><strong>${fmt(r.ping_ms)} <small>ms</small></strong></div>
    <div class="srv-speed-quality ${qualityClass(r.quality)}">${qualityLabel(r.quality)}</div>
  </div>`;
}

function emptyRow(tunnel){
  return `<div class="srv-speed-row srv-speed-empty">
    <div class="srv-speed-title"><b>${esc(tunnel.id)}</b><small>هنوز تست نشده</small></div>
    <div class="srv-speed-metric download"><span>دانلود</span><strong>-- <small>Mbps</small></strong></div>
    <div class="srv-speed-metric upload"><span>آپلود</span><strong>-- <small>Mbps</small></strong></div>
    <div class="srv-speed-metric ping"><span>Ping</span><strong>-- <small>ms</small></strong></div>
    <div class="srv-speed-quality">آماده تست</div>
  </div>`;
}

function injectCss(){
  if($s('tp27ServerSpeedStyle'))return;
  const st=document.createElement('style');
  st.id='tp27ServerSpeedStyle';
  st.textContent=`
    .srv-speed-wrap{margin-top:10px;border-top:1px solid var(--p-border);padding-top:10px;display:flex;flex-direction:column;gap:7px}.srv-speed-row{display:grid;grid-template-columns:minmax(90px,1.1fr) repeat(3,minmax(90px,1fr)) auto;gap:7px;align-items:center;padding:8px;border:1px solid var(--p-border);border-radius:12px;background:var(--p-surface-2)}.srv-speed-title b,.srv-speed-title small{display:block}.srv-speed-title b{font-size:11px}.srv-speed-title small{font-size:9px;color:var(--p-muted);margin-top:3px}.srv-speed-metric{padding:6px 8px;border-radius:9px;background:var(--p-surface);border:1px solid var(--p-border)}.srv-speed-metric span,.srv-speed-metric strong{display:block}.srv-speed-metric span{font-size:9px;color:var(--p-muted)}.srv-speed-metric strong{direction:ltr;text-align:left;font-size:13px;margin-top:3px}.srv-speed-metric strong small{font-size:8px;color:var(--p-muted)}.srv-speed-metric.download strong{color:#67e8f9}.srv-speed-metric.upload strong{color:#a7f3d0}.srv-speed-metric.ping strong{color:#c4b5fd}.srv-speed-quality{font-size:10px;border:1px solid var(--p-border);border-radius:999px;padding:5px 8px;white-space:nowrap;color:var(--p-muted)}.srv-speed-good{color:#86efac;border-color:#34d39955}.srv-speed-warn{color:#fde68a;border-color:#fbbf2455}.srv-speed-bad{color:#fda4af;border-color:#fb718555}.srv-speed-running{opacity:.65;pointer-events:none}.srv-fleet-msg{font-size:10px;color:var(--p-muted);line-height:1.7;margin-top:6px;grid-column:1/-1}.tp27-premium #agentList .agent-card .tunnel-actions{display:flex!important;grid-template-columns:none!important;justify-content:flex-end!important;align-items:center!important;gap:7px!important;padding:10px 12px 12px!important}.tp27-premium #agentList .agent-card .tunnel-actions .btn{width:auto!important;min-width:96px!important;flex:0 0 auto!important;padding:7px 12px!important}.tp27-premium #agentList .agent-card .srv-server-test{border-color:#22d3ee66!important;background:color-mix(in srgb,#22d3ee 8%,var(--p-surface-3))!important;color:#a5f3fc!important}.tp27-premium #agentList .agent-card .srv-server-test:hover{border-color:#22d3eeaa!important;background:color-mix(in srgb,#22d3ee 14%,var(--p-surface-3))!important}@media(max-width:900px){.srv-speed-row{grid-template-columns:1fr 1fr}.srv-speed-title,.srv-speed-quality{grid-column:1/-1}.srv-speed-metric strong{text-align:center}.srv-speed-metric span{text-align:center}.tp27-premium #agentList .agent-card .tunnel-actions{justify-content:stretch!important}.tp27-premium #agentList .agent-card .tunnel-actions .btn{flex:1 1 0!important;min-width:0!important}}
  `;
  document.head.appendChild(st);
}

async function testServer(agentId){
  if(testingAgents.has(agentId))return;
  const agent=agentsSafe().find(a=>a.agent_id===agentId);
  if(!agent||!agent.online)throw new Error('سرور خارج آفلاین است');
  const tunnels=tunnelsForAgent(agentId).filter(x=>x.healthy);
  if(!tunnels.length)throw new Error('Tunnel سالم برای این سرور وجود ندارد');
  testingAgents.add(agentId);decorateServerCards();
  try{
    for(const x of tunnels)await benchmarkTunnelAuto(x);
  }finally{
    testingAgents.delete(agentId);decorateServerCards();
  }
}

async function runServerTest(agentId,button){
  const old=button&&button.textContent;
  try{
    if(button){button.disabled=true;button.textContent='در حال تست...'}
    await testServer(agentId);
  }catch(e){
    alert('تست سرعت ناموفق بود: '+friendlyBenchmarkError(e));
  }finally{
    if(button){button.disabled=false;button.textContent=old||'تست سرعت'}
  }
}

function decorateServerCards(){
  injectCss();
  const list=agentsSafe();
  const cards=[...document.querySelectorAll('#agentList .agent-card')];
  const results=readResults();
  cards.forEach((card,i)=>{
    const a=list[i];if(!a)return;
    card.dataset.agentId=a.agent_id||'';
    const main=card.querySelector('.exit-main');if(!main)return;
    let wrap=main.querySelector('.srv-speed-wrap');
    if(!wrap){wrap=document.createElement('div');wrap.className='srv-speed-wrap';main.appendChild(wrap)}
    const tunnels=tunnelsForAgent(a.agent_id);
    wrap.innerHTML=tunnels.length?tunnels.map(x=>results[x.id]?resultRow(x,results[x.id]):emptyRow(x)).join(''):'<div class="muted tiny">برای این سرور Tunnel ثبت نشده است.</div>';

    const legacyActions=card.querySelector('.srv-speed-actions');
    if(legacyActions)legacyActions.remove();

    const actions=card.querySelector('.tunnel-actions');
    if(!actions)return;
    const busy=testingAgents.has(a.agent_id);
    let btn=actions.querySelector('.srv-server-test');
    if(!btn){
      btn=document.createElement('button');
      btn.type='button';
      btn.className='btn small srv-server-test';
      btn.title='تست Ping، دانلود و آپلود واقعی روی Tunnel این سرور';
      actions.appendChild(btn);
    }
    btn.classList.toggle('srv-speed-running',busy);
    btn.disabled=!a.online||!tunnels.some(x=>x.healthy)||busy;
    btn.textContent=busy?'در حال تست...':'تست سرعت';
    btn.onclick=()=>runServerTest(a.agent_id,btn);
  });
}

async function runFleetSpeeds(){
  if(fleetBusy)return;
  fleetBusy=true;
  const btn=$s('serverSpeedFleetBtn');
  const msg=$s('serverSpeedFleetMsg');
  if(btn){btn.disabled=true;btn.textContent='در حال تست همه...'}
  try{
    const list=agentsSafe().filter(a=>a.online&&tunnelsForAgent(a.agent_id).some(x=>x.healthy));
    if(!list.length)throw new Error('سرور آنلاین با Tunnel سالم پیدا نشد');
    let ok=0,fail=0,lastError='';
    for(let i=0;i<list.length;i++){
      const a=list[i];
      if(msg)msg.textContent=`تست سرور ${i+1}/${list.length} · ${a.hostname||a.public_ip||a.agent_id}`;
      try{await testServer(a.agent_id);ok++}catch(e){fail++;lastError=friendlyBenchmarkError(e)}
    }
    if(msg)msg.textContent=`تست تمام شد · موفق ${ok} سرور${fail?` · ناموفق ${fail}${lastError?' · '+lastError:''}`:''}`;
  }catch(e){if(msg)msg.textContent='خطا: '+friendlyBenchmarkError(e)}finally{
    fleetBusy=false;
    if(btn){btn.disabled=false;btn.textContent='تست سرعت همه سرورها'}
  }
}

function injectFleetButton(){
  if($s('serverSpeedFleetBtn'))return true;
  const actions=document.querySelector('.premium-actions');
  if(!actions)return false;
  const btn=document.createElement('button');
  btn.id='serverSpeedFleetBtn';btn.className='btn';btn.type='button';btn.textContent='تست سرعت همه سرورها';btn.onclick=runFleetSpeeds;
  actions.prepend(btn);
  const msg=document.createElement('div');msg.id='serverSpeedFleetMsg';msg.className='srv-fleet-msg';
  const tools=document.querySelector('#premiumCommand .premium-tools')||actions.parentElement;if(tools)tools.appendChild(msg);
  return true;
}

window.addEventListener('tp27:benchmark-results-updated',decorateServerCards);
window.addEventListener('storage',e=>{if(e.key===SPEED_RESULTS_KEY)decorateServerCards()});

let tries=0;
const boot=setInterval(()=>{
  tries++;
  injectCss();injectFleetButton();decorateServerCards();
  if(tries>120)clearInterval(boot);
},500);
setInterval(()=>{injectFleetButton();decorateServerCards()},1500);
})();