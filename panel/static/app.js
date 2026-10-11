const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));
let agents=[];
let wizardBaseline=new Set();
let wizardTimer=null;
let wizardReadyAgent=null;

async function api(url,opt={}){
  const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  const type=r.headers.get('content-type')||'';
  const body=type.includes('application/json')?await r.json():{detail:await r.text()};
  if(!r.ok)throw new Error(body.detail||body.error||'خطا');
  return body;
}

function mappingBadges(items=[]){
  if(!items.length)return '';
  return '<div class="badges">'+items.map(m=>`<span class="badge"><b>${esc(m.service)}</b> ${esc(String(m.proto).toUpperCase())} ${esc(m.listen_port)}→${esc(m.target_port)}</span>`).join('')+'</div>';
}

async function loadStatus(){
  try{
    const s=await api('/api/status');
    $('publicIp').textContent=s.public_ip||'-';
    $('exitCount').textContent=s.exits.length;
    $('onlineCount').textContent=s.exits.filter(x=>x.healthy).length;
    $('exitList').innerHTML=s.exits.length?s.exits.map(x=>`
      <div class="exit">
        <div class="exit-main">
          <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${esc(x.id)}</b></div>
          <div class="muted">${esc(x.endpoint)}:${esc(x.port)} · ${esc(x.interface)} · ${esc(x.remote_ip)}</div>
          ${mappingBadges(x.mappings)}
        </div>
      </div>`).join(''):'<div class="muted">بدون تونل</div>';
  }catch(e){$('exitList').innerHTML='<div class="muted">'+esc(e.message)+'</div>'}
}

async function loadAgents(){
  try{
    const data=await api('/api/agents');
    agents=data.agents||[];
    $('agentCount').textContent=agents.length;
    $('agentList').innerHTML=agents.length?agents.map(a=>`
      <div class="exit agent-card">
        <div class="exit-main">
          <div><span class="dot ${a.online?'good':'bad'}"></span><b>${esc(a.hostname||a.agent_id)}</b></div>
          <div class="muted">${esc(a.public_ip||'-')} · ${a.online?'آنلاین':'آفلاین'} · v${esc(a.version||'-')}</div>
        </div>
      </div>`).join(''):'<div class="muted">بدون سرور خارج</div>';

    const sel=$('agentId');
    const current=sel.value;
    sel.innerHTML='<option value="">انتخاب سرور</option>'+agents.map(a=>`<option value="${esc(a.agent_id)}" ${a.online?'':'disabled'}>${esc(a.hostname||a.agent_id)} · ${esc(a.public_ip||'-')}${a.online?'':' · آفلاین'}</option>`).join('');
    if([...sel.options].some(o=>o.value===current&&!o.disabled))sel.value=current;
    return agents;
  }catch(e){
    $('agentList').innerHTML='<div class="muted">'+esc(e.message)+'</div>';
    return [];
  }
}

function selectedAgent(){
  const aid=$('agentId').value;
  return agents.find(x=>x.agent_id===aid)||null;
}

function connectionPayload(){
  const a=selectedAgent();
  return{
    agent_id:a?a.agent_id:null,
    host:a?(a.public_ip||''):'',
    port:22,
    username:'root',
    password:null,
    private_key:null
  };
}

function collectMappings(){
  return [...document.querySelectorAll('.port-row')].map((row,i)=>{
    const service=row.querySelector('.p-service').value.trim();
    const proto=row.querySelector('.p-proto').value;
    const listen_port=Number(row.querySelector('.p-iran').value);
    const target_port=Number(row.querySelector('.p-exit').value);
    if(!service)throw new Error(`نام سرویس ردیف ${i+1} خالی است`);
    if(!listen_port||listen_port<1||listen_port>65535)throw new Error(`پورت ایران ردیف ${i+1} نامعتبر است`);
    if(!target_port||target_port<1||target_port>65535)throw new Error(`پورت خارج ردیف ${i+1} نامعتبر است`);
    return{service,proto,listen_port,target_port};
  });
}

function payload(){
  const p=connectionPayload();
  if(!p.agent_id)throw new Error('سرور خارج را انتخاب کنید');
  p.id=$('exitId').value.trim()||null;
  p.tunnel_port=Number($('tunnelPort').value)||null;
  p.mtu=1280;
  p.mappings=collectMappings();
  return p;
}

function addPortRow(data={}){
  const row=document.createElement('div');
  row.className='port-row';
  const service=esc(data.service||'custom');
  const listen=data.listen_port??'';
  const target=data.target_port??listen;
  const proto=(data.proto||'tcp').toLowerCase();
  row.innerHTML=`
    <input class="p-service" value="${service}" placeholder="service">
    <select class="p-proto"><option value="tcp" ${proto==='tcp'?'selected':''}>TCP</option><option value="udp" ${proto==='udp'?'selected':''}>UDP</option></select>
    <input class="p-iran" type="number" min="1" max="65535" value="${listen}" placeholder="ایران">
    <input class="p-exit" type="number" min="1" max="65535" value="${target}" placeholder="خارج">
    <button class="btn danger small" type="button" title="حذف">×</button>`;
  row.querySelector('.danger').onclick=()=>row.remove();
  $('portRows').appendChild(row);
}

function standardPorts(){
  $('portRows').innerHTML='';
  [
    {service:'cisco',proto:'tcp',listen_port:8897,target_port:8897},
    {service:'cisco',proto:'udp',listen_port:8897,target_port:8897},
    {service:'openvpn',proto:'tcp',listen_port:9988,target_port:9988},
    {service:'wireguard',proto:'udp',listen_port:55821,target_port:55821},
    {service:'l2tp',proto:'udp',listen_port:500,target_port:500},
    {service:'l2tp',proto:'udp',listen_port:4500,target_port:4500},
    {service:'l2tp',proto:'udp',listen_port:1701,target_port:1701}
  ].forEach(addPortRow);
}

async function runPreflight(show=true){
  if(!selectedAgent())throw new Error('سرور خارج را انتخاب کنید');
  const r=await api('/api/preflight',{method:'POST',body:JSON.stringify(connectionPayload())});
  if(!r.ok)throw new Error(r.error||'سرور در دسترس نیست');
  if(show)$('job').textContent='سرور آماده است';
  return r;
}

async function preflight(){
  const j=$('job');j.textContent='در حال بررسی...';
  try{await runPreflight(true)}catch(e){j.textContent='خطا: '+e.message}
}

async function addExit(){
  const j=$('job');
  j.textContent='در حال بررسی...';
  try{
    await runPreflight(false);
    j.textContent='در حال ساخت تونل...';
    const r=await api('/api/exits',{method:'POST',body:JSON.stringify(payload())});
    pollJob(r.job_id);
  }catch(e){j.textContent='خطا: '+e.message}
}

async function pollJob(id){
  try{
    const r=await api('/api/jobs/'+id);
    $('job').innerHTML='<b>'+esc(r.state)+'</b><br>'+r.messages.map(x=>esc(x.text)).join('<br>');
    if(r.state==='done'){
      refreshAll();return;
    }
    if(r.state==='failed')return;
    setTimeout(()=>pollJob(id),1500);
  }catch(e){$('job').textContent=e.message}
}

function setWizardState(text,state='wait'){
  $('wizardStatus').textContent=text;
  $('wizardDot').className='dot '+(state==='ok'?'good':state==='bad'?'bad':'wait');
}

async function openServerWizard(){
  wizardReadyAgent=null;
  wizardBaseline=new Set(agents.map(x=>x.agent_id));
  $('serverWizard').classList.remove('hidden');
  $('wizardWaiting').classList.remove('hidden');
  $('wizardReady').classList.add('hidden');
  $('installCommand').textContent='';
  setWizardState('در حال آماده‌سازی...');

  try{
    const r=await api('/api/agents/enrollment/rotate',{method:'POST',body:'{}'});
    const cmd=`curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install-exit-agent.sh | bash -s -- '${location.origin}' '${r.token}'`;
    $('installCommand').textContent=cmd;
    setWizardState('در انتظار اتصال سرور خارج');
    clearInterval(wizardTimer);
    wizardTimer=setInterval(checkEnrollmentNow,2000);
  }catch(e){
    setWizardState(e.message,'bad');
  }
}

function closeServerWizard(){
  $('serverWizard').classList.add('hidden');
  clearInterval(wizardTimer);
  wizardTimer=null;
}

async function copyInstallCommand(){
  const text=$('installCommand').textContent.trim();
  if(!text)return;
  try{
    await navigator.clipboard.writeText(text);
    setWizardState('دستور کپی شد؛ روی سرور خارج اجرا کنید');
  }catch(_){
    const ta=document.createElement('textarea');
    ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();
    setWizardState('دستور کپی شد؛ روی سرور خارج اجرا کنید');
  }
}

async function checkEnrollmentNow(){
  const list=await loadAgents();
  const found=list.find(a=>a.online&&!wizardBaseline.has(a.agent_id));
  if(!found)return;
  wizardReadyAgent=found;
  clearInterval(wizardTimer);wizardTimer=null;
  $('wizardWaiting').classList.add('hidden');
  $('wizardReady').classList.remove('hidden');
  $('readyAgentName').textContent=found.hostname||found.agent_id;
  $('readyAgentMeta').textContent=(found.public_ip||'-')+' · آنلاین';
}

function useReadyAgent(){
  if(!wizardReadyAgent)return;
  const sel=$('agentId');
  if([...sel.options].some(o=>o.value===wizardReadyAgent.agent_id))sel.value=wizardReadyAgent.agent_id;
  closeServerWizard();
  document.querySelector('.builder').scrollIntoView({behavior:'smooth',block:'start'});
  $('job').textContent='سرور انتخاب شد';
}

async function refreshAll(){await Promise.all([loadStatus(),loadAgents()])}

standardPorts();
refreshAll();
setInterval(refreshAll,10000);
