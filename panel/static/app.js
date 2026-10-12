const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const ACTIVE_JOB_KEY='tp27_active_job';
let agents=[];
let currentStatus={exits:[]};
let wizardBaseline=new Set();
let wizardTimer=null;
let wizardReadyAgent=null;
let activeTunnelId=null;
let managedTunnelId=null;

async function api(url,opt={}){
  const method=String(opt.method||'GET').toUpperCase();
  const attempts=method==='GET'?4:1;
  let lastError=null;
  for(let attempt=1;attempt<=attempts;attempt++){
    try{
      const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
      const type=r.headers.get('content-type')||'';
      const body=type.includes('application/json')?await r.json():{detail:await r.text()};
      if(!r.ok){
        const err=new Error(body.detail||body.error||'خطا');
        err.status=r.status;
        err.http=true;
        throw err;
      }
      return body;
    }catch(e){
      lastError=e;
      if(e&&e.http)throw e;
      if(attempt>=attempts)break;
      await sleep(500*attempt);
    }
  }
  const err=new Error(lastError&&lastError.message&&lastError.message!=='Failed to fetch'?lastError.message:'ارتباط موقت با پنل برقرار نشد');
  err.network=true;
  throw err;
}

function mappingBadges(items=[]){
  if(!items.length)return '';
  return '<div class="badges">'+items.map(m=>`<span class="badge"><b>${esc(m.service)}</b> ${esc(String(m.proto).toUpperCase())} ${esc(m.listen_port)}→${esc(m.target_port)}</span>`).join('')+'</div>';
}

async function loadStatus(){
  try{
    const s=await api('/api/status');
    currentStatus=s;
    $('publicIp').textContent=s.public_ip||'-';
    $('exitCount').textContent=s.exits.length;
    $('onlineCount').textContent=s.exits.filter(x=>x.healthy).length;
    $('exitList').innerHTML=s.exits.length?s.exits.map(x=>`
      <div class="exit tunnel-card">
        <div class="exit-main">
          <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${esc(x.id)}</b></div>
          <div class="muted">${esc(x.endpoint)}:${esc(x.port)} · ${esc(x.interface)} · ${esc(x.remote_ip)}</div>
          ${mappingBadges(x.mappings)}
        </div>
        <div class="actions tunnel-actions">
          <button class="btn small primary" type="button" onclick="openTunnelManage('${esc(x.id)}')">مدیریت</button>
          <button class="btn small" type="button" onclick="diagnoseTunnel('${esc(x.id)}')">بررسی</button>
          <button class="btn small" type="button" onclick="repairTunnel('${esc(x.id)}')">Restart</button>
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
  return{agent_id:a?a.agent_id:null,host:a?(a.public_ip||''):'',port:22,username:'root',password:null,private_key:null};
}

function collectRows(selector){
  return [...document.querySelectorAll(selector)].map((row,i)=>{
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

function collectMappings(){return collectRows('#portRows .port-row')}
function collectManageMappings(){return collectRows('#managePortRows .port-row')}

function payload(){
  const p=connectionPayload();
  if(!p.agent_id)throw new Error('سرور خارج را انتخاب کنید');
  p.id=$('exitId').value.trim()||null;
  p.tunnel_port=Number($('tunnelPort').value)||null;
  p.mtu=1280;
  p.mappings=collectMappings();
  return p;
}

function createPortRow(target,data={}){
  const row=document.createElement('div');
  row.className='port-row';
  const service=esc(data.service||'custom');
  const listen=data.listen_port??'';
  const targetPort=data.target_port??listen;
  const proto=(data.proto||'tcp').toLowerCase();
  row.innerHTML=`
    <input class="p-service" value="${service}" placeholder="service">
    <select class="p-proto"><option value="tcp" ${proto==='tcp'?'selected':''}>TCP</option><option value="udp" ${proto==='udp'?'selected':''}>UDP</option></select>
    <input class="p-iran" type="number" min="1" max="65535" value="${listen}" placeholder="ایران">
    <input class="p-exit" type="number" min="1" max="65535" value="${targetPort}" placeholder="خارج">
    <button class="btn danger small" type="button" title="حذف">×</button>`;
  row.querySelector('.danger').onclick=()=>row.remove();
  $(target).appendChild(row);
}

function standardList(){return[
  {service:'cisco',proto:'tcp',listen_port:8897,target_port:8897},
  {service:'cisco',proto:'udp',listen_port:8897,target_port:8897},
  {service:'openvpn',proto:'tcp',listen_port:9988,target_port:9988},
  {service:'wireguard',proto:'udp',listen_port:55821,target_port:55821},
  {service:'l2tp',proto:'udp',listen_port:500,target_port:500},
  {service:'l2tp',proto:'udp',listen_port:4500,target_port:4500},
  {service:'l2tp',proto:'udp',listen_port:1701,target_port:1701}
]}

function addPortRow(data={}){createPortRow('portRows',data)}
function addManagePortRow(data={}){createPortRow('managePortRows',data)}
function standardPorts(){$('portRows').innerHTML='';standardList().forEach(addPortRow)}
function manageStandardPorts(){$('managePortRows').innerHTML='';standardList().forEach(addManagePortRow)}

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
    localStorage.setItem(ACTIVE_JOB_KEY,r.job_id);
    pollJob(r.job_id,0);
  }catch(e){j.textContent='خطا: '+e.message}
}

async function pollJob(id,failures=0){
  try{
    const r=await api('/api/jobs/'+encodeURIComponent(id));
    $('job').innerHTML='<b>'+esc(r.state)+'</b><br>'+r.messages.map(x=>esc(x.text)).join('<br>');
    if(r.state==='done'||r.state==='failed'){
      localStorage.removeItem(ACTIVE_JOB_KEY);
      refreshAll();
      return;
    }
    setTimeout(()=>pollJob(id,0),1500);
  }catch(e){
    if(e&&e.status===404){
      localStorage.removeItem(ACTIVE_JOB_KEY);
      $('job').textContent='Job قبلی دیگر روی سرور وجود ندارد؛ وضعیت تونل را بررسی کنید.';
      refreshAll();
      return;
    }
    if(e&&e.status===401){
      localStorage.removeItem(ACTIVE_JOB_KEY);
      $('job').textContent='نشست پنل منقضی شده است؛ دوباره وارد شوید.';
      return;
    }
    const next=failures+1;
    if(next>=20){
      $('job').textContent='ارتباط با پنل ناپایدار است؛ وضعیت Job محفوظ ماند. صفحه را Refresh کنید تا پیگیری ادامه پیدا کند.';
      return;
    }
    $('job').textContent=`ارتباط موقت با پنل قطع شد؛ تلاش مجدد ${next}/20 ...`;
    setTimeout(()=>pollJob(id,next),Math.min(5000,1000+next*300));
  }
}

function tunnelById(id){return (currentStatus.exits||[]).find(x=>x.id===id)||null}

function openTunnelManage(id){
  const x=tunnelById(id);
  if(!x)return;
  managedTunnelId=id;
  $('manageTitle').textContent='مدیریت تونل '+id;
  $('manageHealth').textContent=x.healthy?'آنلاین':'آفلاین';
  $('manageHealth').className=x.healthy?'status-good':'status-bad';
  $('manageEndpoint').textContent=x.endpoint||'-';
  $('manageTunnelPort').textContent=x.port||'-';
  $('manageInterface').textContent=x.interface||'-';
  $('managePortRows').innerHTML='';
  (x.mappings||[]).forEach(addManagePortRow);
  $('manageMessage').textContent='آماده';
  $('tunnelManage').classList.remove('hidden');
}

function closeTunnelManage(){
  $('tunnelManage').classList.add('hidden');
  managedTunnelId=null;
}

async function saveManagedPorts(){
  if(!managedTunnelId)return;
  const box=$('manageMessage');
  try{
    const mappings=collectManageMappings();
    box.textContent='در حال ذخیره Port Mapping...';
    const r=await api('/api/exits/'+encodeURIComponent(managedTunnelId)+'/mappings',{method:'PUT',body:JSON.stringify({mappings})});
    box.textContent=`ذخیره شد · ${r.mappings.length} پورت فعال`;
    await loadStatus();
  }catch(e){box.textContent='خطا: '+e.message}
}

function diagnoseManagedTunnel(){
  if(!managedTunnelId)return;
  const id=managedTunnelId;
  closeTunnelManage();
  diagnoseTunnel(id);
}

async function repairManagedTunnel(){
  if(!managedTunnelId)return;
  const box=$('manageMessage');
  box.textContent='در حال Restart هماهنگ ایران و خارج...';
  try{
    const d=await api('/api/exits/'+encodeURIComponent(managedTunnelId)+'/repair',{method:'POST',body:'{}'});
    box.textContent=d.healthy?'تونل آنلاین شد':'Restart انجام شد؛ Tunnel هنوز Health Check را پاس نکرده است';
    await loadStatus();
    const x=tunnelById(managedTunnelId);
    if(x){$('manageHealth').textContent=x.healthy?'آنلاین':'آفلاین';$('manageHealth').className=x.healthy?'status-good':'status-bad'}
  }catch(e){box.textContent='خطا: '+e.message}
}

function openDeleteTunnel(){
  if(!managedTunnelId)return;
  $('deleteTunnelText').textContent=`تونل «${managedTunnelId}» و Port Mappingهای آن حذف شوند؟ این عملیات سرویس‌های VPN اصلی سرور خارج را حذف نمی‌کند.`;
  $('deleteTunnelBtn').disabled=false;
  $('deleteTunnelModal').classList.remove('hidden');
}

function closeDeleteTunnel(){$('deleteTunnelModal').classList.add('hidden')}

async function confirmDeleteTunnel(){
  if(!managedTunnelId)return;
  const id=managedTunnelId;
  const btn=$('deleteTunnelBtn');
  btn.disabled=true;
  btn.textContent='در حال حذف...';
  try{
    const r=await api('/api/exits/'+encodeURIComponent(id),{method:'DELETE'});
    closeDeleteTunnel();
    closeTunnelManage();
    $('job').textContent=r.warning?('تونل حذف شد. '+r.warning):'تونل با موفقیت حذف شد';
    await refreshAll();
  }catch(e){
    btn.disabled=false;
    btn.textContent='حذف کامل';
    $('deleteTunnelText').textContent='خطا: '+e.message;
  }
}

function diagFlag(label,ok){return `<div class="diag-row"><span class="dot ${ok?'good':'bad'}"></span><span>${esc(label)}</span><b>${ok?'سالم':'خطا'}</b></div>`}

function renderDiagnosis(d){
  $('diagTitle').textContent='بررسی تونل '+(d.id||'');
  let html='';
  html+=diagFlag('Agent سرور خارج',!!d.agent_online);
  html+=diagFlag('سرویس Link ایران',!!d.local_service);
  html+=diagFlag('Interface ایران',!!d.interface_up);
  html+=diagFlag('Ping تونل',!!d.peer_ping);
  html+=`<div class="diag-meta">${esc(d.endpoint||'-')}:${esc(d.port||'-')} · ${esc(d.interface||'-')} · ${esc(d.remote_ip||'-')}</div>`;
  if(d.local_log)html+=`<h4>لاگ ایران</h4><pre>${esc(d.local_log)}</pre>`;
  if(d.remote_status)html+=`<h4>وضعیت سرور خارج</h4><pre>${esc(d.remote_status)}</pre>`;
  $('diagBody').innerHTML=html;
  $('repairBtn').textContent=d.healthy?'Restart Link':'تعمیر خودکار';
  $('repairBtn').disabled=false;
}

async function diagnoseTunnel(id){
  activeTunnelId=id;
  $('tunnelDiag').classList.remove('hidden');
  $('diagTitle').textContent='در حال بررسی...';
  $('diagBody').innerHTML='<div class="muted">در حال دریافت وضعیت ایران و سرور خارج...</div>';
  $('repairBtn').disabled=true;
  try{renderDiagnosis(await api('/api/exits/'+encodeURIComponent(id)+'/diagnose'))}
  catch(e){$('diagBody').innerHTML='<div class="err">'+esc(e.message)+'</div>'}
}

async function repairTunnel(id){
  activeTunnelId=id;
  $('tunnelDiag').classList.remove('hidden');
  $('diagTitle').textContent='تعمیر تونل '+id;
  $('diagBody').innerHTML='<div class="muted">در حال Restart هماهنگ Link خارج و ایران...</div>';
  $('repairBtn').disabled=true;
  try{const d=await api('/api/exits/'+encodeURIComponent(id)+'/repair',{method:'POST',body:'{}'});renderDiagnosis(d);await loadStatus()}
  catch(e){$('diagBody').innerHTML='<div class="err">'+esc(e.message)+'</div>';$('repairBtn').disabled=false}
}

function repairActiveTunnel(){if(activeTunnelId)repairTunnel(activeTunnelId)}
function closeTunnelDiag(){$('tunnelDiag').classList.add('hidden');activeTunnelId=null}

function setWizardState(text,state='wait'){
  $('wizardStatus').textContent=text;
  $('wizardDot').className='dot '+(state==='ok'?'good':state==='bad'?'bad':'wait');
}

async function openServerWizard(){
  wizardReadyAgent=null;
  $('serverWizard').classList.remove('hidden');
  $('wizardWaiting').classList.remove('hidden');
  $('wizardReady').classList.add('hidden');
  $('installCommand').textContent='';
  setWizardState('در حال آماده‌سازی...');
  try{
    const before=await loadAgents();
    wizardBaseline=new Set(before.map(x=>x.agent_id));
    const r=await api('/api/agents/enrollment/rotate',{method:'POST',body:'{}'});
    const cmd=`curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install-exit-agent.sh | bash -s -- '${location.origin}' '${r.token}'`;
    $('installCommand').textContent=cmd;
    setWizardState('در انتظار اتصال سرور خارج');
    clearInterval(wizardTimer);
    wizardTimer=setInterval(checkEnrollmentNow,2000);
  }catch(e){setWizardState(e.message,'bad')}
}

function closeServerWizard(){$('serverWizard').classList.add('hidden');clearInterval(wizardTimer);wizardTimer=null}

async function copyInstallCommand(){
  const text=$('installCommand').textContent.trim();if(!text)return;
  try{await navigator.clipboard.writeText(text);setWizardState('دستور کپی شد؛ روی سرور خارج اجرا کنید')}
  catch(_){const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();setWizardState('دستور کپی شد؛ روی سرور خارج اجرا کنید')}
}

async function checkEnrollmentNow(){
  const list=await loadAgents();
  const found=list.find(a=>a.online&&!wizardBaseline.has(a.agent_id));
  if(!found)return;
  wizardReadyAgent=found;clearInterval(wizardTimer);wizardTimer=null;
  $('wizardWaiting').classList.add('hidden');$('wizardReady').classList.remove('hidden');
  $('readyAgentName').textContent=found.hostname||found.agent_id;
  $('readyAgentMeta').textContent=(found.public_ip||'-')+' · آنلاین';
}

function useReadyAgent(){
  if(!wizardReadyAgent)return;
  const sel=$('agentId');
  if([...sel.options].some(o=>o.value===wizardReadyAgent.agent_id))sel.value=wizardReadyAgent.agent_id;
  closeServerWizard();document.querySelector('.builder').scrollIntoView({behavior:'smooth',block:'start'});$('job').textContent='سرور انتخاب شد';
}

async function refreshAll(){await Promise.all([loadStatus(),loadAgents()])}

standardPorts();
refreshAll();
const savedJobId=localStorage.getItem(ACTIVE_JOB_KEY);
if(savedJobId)pollJob(savedJobId,0);
setInterval(refreshAll,10000);
