let managedAgentId=null;
let managedAgentData=null;

function agentById(id){
  return (agents||[]).find(x=>x.agent_id===id)||null;
}

function formatAgentTime(ts){
  if(!ts)return '-';
  try{return new Date(Number(ts)*1000).toLocaleString('fa-IR')}
  catch(_){return String(ts)}
}

async function loadAgents(){
  try{
    const data=await api('/api/agents');
    agents=data.agents||[];
    $('agentCount').textContent=agents.length;
    $('agentList').innerHTML=agents.length?agents.map(a=>`
      <div class="exit agent-card tunnel-card">
        <div class="exit-main">
          <div><span class="dot ${a.online?'good':'bad'}"></span><b>${esc(a.hostname||a.agent_id)}</b></div>
          <div class="muted">${esc(a.public_ip||'-')} · ${a.online?'آنلاین':'آفلاین'} · v${esc(a.version||'-')} · ${Number(a.tunnel_count||0)} تونل</div>
        </div>
        <div class="actions tunnel-actions">
          <button class="btn small primary" type="button" onclick="openAgentManage('${a.agent_id}')">مدیریت</button>
        </div>
      </div>`).join(''):'<div class="muted">بدون سرور خارج</div>';

    const sel=$('agentId');
    const current=sel.value;
    sel.innerHTML='<option value="">انتخاب سرور</option>'+agents.map(a=>`<option value="${a.agent_id}" ${a.online?'':'disabled'}>${esc(a.hostname||a.agent_id)} · ${esc(a.public_ip||'-')}${a.online?'':' · آفلاین'}</option>`).join('');
    if([...sel.options].some(o=>o.value===current&&!o.disabled))sel.value=current;
    return agents;
  }catch(e){
    $('agentList').innerHTML='<div class="muted">'+esc(e.message)+'</div>';
    return [];
  }
}

async function openAgentManage(id){
  managedAgentId=id;
  managedAgentData=null;
  $('agentManage').classList.remove('hidden');
  $('agentManageTitle').textContent='مدیریت سرور خارج';
  $('agentManageBody').innerHTML='<div class="muted">در حال دریافت وضعیت...</div>';
  $('agentManageMessage').textContent='';
  $('agentDeleteBtn').disabled=true;
  try{
    const data=await api('/api/agents/'+encodeURIComponent(id)+'/manage');
    managedAgentData=data;
    renderAgentManage(data);
  }catch(e){
    $('agentManageBody').innerHTML='<div class="err">'+esc(e.message)+'</div>';
  }
}

function renderAgentManage(data){
  const a=data.agent||{};
  const tunnels=data.tunnels||[];
  $('agentManageTitle').textContent='مدیریت سرور '+(a.hostname||a.agent_id||'خارج');

  let tunnelHtml=tunnels.length?tunnels.map(t=>`
    <div class="exit tunnel-card">
      <div class="exit-main">
        <b>${esc(t.id||'-')}</b>
        <div class="muted">${esc(t.endpoint||'-')}:${esc(t.port||'-')} · ${esc(t.interface||'-')}</div>
      </div>
      <div class="actions tunnel-actions">
        <button class="btn small primary" type="button" onclick="openTunnelFromAgent('${esc(t.id||'')}')">مدیریت تونل</button>
      </div>
    </div>`).join(''):'<div class="muted">این سرور Tunnel وابسته ندارد.</div>';

  const statusText=a.online?'آنلاین':'آفلاین';
  $('agentManageBody').innerHTML=`
    <div class="manage-summary">
      <div><span class="muted">وضعیت</span><strong class="${a.online?'status-good':'status-bad'}">${statusText}</strong></div>
      <div><span class="muted">IP</span><strong>${esc(a.public_ip||'-')}</strong></div>
      <div><span class="muted">نسخه Agent</span><strong>v${esc(a.version||'-')}</strong></div>
      <div><span class="muted">Tunnelها</span><strong>${tunnels.length}</strong></div>
    </div>
    <div class="manage-summary agent-extra-summary">
      <div><span class="muted">Hostname</span><strong>${esc(a.hostname||'-')}</strong></div>
      <div><span class="muted">Agent ID</span><strong>${esc(a.agent_id||'-')}</strong></div>
      <div><span class="muted">آخرین ارتباط</span><strong>${esc(formatAgentTime(a.last_seen))}</strong></div>
      <div><span class="muted">ثبت اولیه</span><strong>${esc(formatAgentTime(a.created_at))}</strong></div>
    </div>
    <div class="ports-title"><h4>Tunnelهای این سرور</h4></div>
    <div>${tunnelHtml}</div>
    <div id="agentStatusOutput" class="job muted">آماده</div>`;

  $('agentDeleteBtn').disabled=tunnels.length>0;
  $('agentDeleteBtn').title=tunnels.length?'ابتدا Tunnelهای وابسته را حذف کنید':'';
  if(tunnels.length){
    $('agentManageMessage').textContent='برای حذف سرور، ابتدا Tunnelهای وابسته را حذف کنید.';
  }else{
    $('agentManageMessage').textContent='';
  }
}

function closeAgentManage(){
  $('agentManage').classList.add('hidden');
  managedAgentId=null;
  managedAgentData=null;
}

function openTunnelFromAgent(id){
  closeAgentManage();
  openTunnelManage(id);
}

function useManagedAgentForTunnel(){
  if(!managedAgentId)return;
  const sel=$('agentId');
  if([...sel.options].some(o=>o.value===managedAgentId&&!o.disabled)){
    sel.value=managedAgentId;
    closeAgentManage();
    document.querySelector('.builder').scrollIntoView({behavior:'smooth',block:'start'});
    $('job').textContent='سرور خارج انتخاب شد';
  }
}

async function checkManagedAgent(){
  if(!managedAgentId)return;
  const out=$('agentStatusOutput');
  if(!out)return;
  out.textContent='در حال بررسی Agent و TP27 Core...';
  try{
    const queued=await api('/api/agents/'+encodeURIComponent(managedAgentId)+'/commands',{
      method:'POST',body:JSON.stringify({action:'status',args:{}})
    });
    await pollManagedAgentCommand(queued.command_id);
  }catch(e){out.textContent='خطا: '+e.message}
}

async function pollManagedAgentCommand(commandId){
  const out=$('agentStatusOutput');
  try{
    const r=await api('/api/agents/commands/'+encodeURIComponent(commandId));
    if(r.state==='queued'||r.state==='delivered'){
      out.textContent='در حال دریافت وضعیت از سرور خارج...';
      setTimeout(()=>pollManagedAgentCommand(commandId),1200);
      return;
    }
    if(r.state==='failed'||!r.ok){
      out.textContent='خطا: '+(r.error||'بررسی Agent ناموفق بود');
      return;
    }
    const result=r.result||{};
    const text=[result.tp27||'',result.addresses||''].filter(Boolean).join('\n\n');
    out.innerHTML='<b>وضعیت دریافت شد</b><pre>'+esc(text||'Agent پاسخ داد')+'</pre>';
  }catch(e){out.textContent='خطا: '+e.message}
}

function openDeleteAgent(){
  if(!managedAgentId||!managedAgentData)return;
  const tunnels=managedAgentData.tunnels||[];
  if(tunnels.length){
    $('agentManageMessage').textContent='ابتدا Tunnelهای وابسته را حذف کنید: '+tunnels.map(x=>x.id).join(', ');
    return;
  }
  const a=managedAgentData.agent||{};
  $('deleteAgentText').textContent=`سرور «${a.hostname||a.public_ip||managedAgentId}» از پنل حذف شود؟ سرویس‌های VPN اصلی سرور خارج حذف نمی‌شوند.`;
  $('deleteAgentConfirmBtn').disabled=false;
  $('deleteAgentConfirmBtn').textContent='حذف سرور';
  $('deleteAgentModal').classList.remove('hidden');
}

function closeDeleteAgent(){$('deleteAgentModal').classList.add('hidden')}

async function confirmDeleteAgent(){
  if(!managedAgentId)return;
  const btn=$('deleteAgentConfirmBtn');
  btn.disabled=true;
  btn.textContent='در حال حذف...';
  try{
    const r=await api('/api/agents/'+encodeURIComponent(managedAgentId),{method:'DELETE'});
    closeDeleteAgent();
    closeAgentManage();
    $('job').textContent=r.message||'سرور خارج حذف شد';
    await refreshAll();
  }catch(e){
    btn.disabled=false;
    btn.textContent='حذف سرور';
    $('deleteAgentText').textContent='خطا: '+e.message;
  }
}
