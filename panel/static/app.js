const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let agents=[];

async function api(url,opt={}){
  const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  const j=await r.json();
  if(!r.ok)throw new Error(j.detail||j.error||'خطا');
  return j;
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
          ${x.agent_id?`<div class="muted">Agent: ${esc(x.agent_id)}</div>`:''}
          ${mappingBadges(x.mappings)}
        </div>
      </div>`).join(''):'<div class="muted">بدون تونل</div>';
  }catch(e){$('exitList').innerHTML='<div class="muted">'+esc(e.message)+'</div>'}
}

async function loadAgents(){
  try{
    const data=await api('/api/agents');
    agents=data.agents||[];
    $('agentCount').textContent=agents.filter(x=>x.online).length;
    $('agentList').innerHTML=agents.length?agents.map(a=>`
      <div class="exit">
        <div class="exit-main">
          <div><span class="dot ${a.online?'good':'bad'}"></span><b>${esc(a.hostname||a.agent_id)}</b></div>
          <div class="muted">${esc(a.public_ip||'-')} · ${esc(a.agent_id)} · v${esc(a.version||'-')}</div>
        </div>
      </div>`).join(''):'<div class="muted">بدون Agent</div>';

    const sel=$('agentId');
    const current=sel.value;
    sel.innerHTML='<option value="">SSH دستی</option>'+agents.map(a=>`<option value="${esc(a.agent_id)}" ${a.online?'':'disabled'}>${esc(a.hostname||a.agent_id)} · ${esc(a.public_ip||'-')}${a.online?'':' · offline'}</option>`).join('');
    if([...sel.options].some(o=>o.value===current&&!o.disabled))sel.value=current;
    syncAgentSelection();
  }catch(e){$('agentList').innerHTML='<div class="muted">'+esc(e.message)+'</div>'}
}

function syncAgentSelection(){
  const aid=$('agentId').value;
  const a=agents.find(x=>x.agent_id===aid);
  if(a){
    $('host').value=a.public_ip||'';
    $('host').readOnly=true;
    $('sshFallback').open=false;
    $('sshFallback').classList.add('disabled-block');
  }else{
    $('host').readOnly=false;
    $('sshFallback').classList.remove('disabled-block');
  }
}

async function showEnrollment(){
  const box=$('enrollmentBox');
  try{
    const r=await api('/api/agents/enrollment');
    const cmd=`curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install-exit-agent.sh | bash -s -- ${location.origin} ${r.token}`;
    box.innerHTML=`<b>Token</b><br><code>${esc(r.token)}</code><br><br><b>Install</b><br><code>${esc(cmd)}</code>`;
    box.classList.remove('hidden');
  }catch(e){box.textContent=e.message;box.classList.remove('hidden')}
}

async function rotateEnrollment(){
  const box=$('enrollmentBox');
  try{
    const r=await api('/api/agents/enrollment/rotate',{method:'POST',body:'{}'});
    const cmd=`curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install-exit-agent.sh | bash -s -- ${location.origin} ${r.token}`;
    box.innerHTML=`<b>Token</b><br><code>${esc(r.token)}</code><br><br><b>Install</b><br><code>${esc(cmd)}</code>`;
    box.classList.remove('hidden');
  }catch(e){box.textContent=e.message;box.classList.remove('hidden')}
}

function connectionPayload(){
  return{
    agent_id:$('agentId').value||null,
    host:$('host').value.trim(),
    port:Number($('sshPort').value||22),
    username:$('sshUser').value.trim()||'root',
    password:$('password').value||null,
    private_key:$('privateKey').value.trim()||null
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
  const r=await api('/api/preflight',{method:'POST',body:JSON.stringify(connectionPayload())});
  if(!r.ok)throw new Error(r.error||r.stderr||'اتصال ناموفق بود');
  if(show)$('job').textContent=(r.mode==='agent'?'Agent':'SSH')+': OK';
  return r;
}

async function preflight(){
  const j=$('job');j.textContent='تست اتصال...';
  try{await runPreflight(true)}catch(e){j.textContent='خطا: '+e.message}
}

async function addExit(){
  const j=$('job');
  j.textContent='بررسی اتصال...';
  try{
    await runPreflight(false);
    j.textContent='شروع ساخت تونل...';
    const r=await api('/api/exits',{method:'POST',body:JSON.stringify(payload())});
    pollJob(r.job_id);
  }catch(e){j.textContent='خطا: '+e.message}
}

async function pollJob(id){
  try{
    const r=await api('/api/jobs/'+id);
    $('job').innerHTML='<b>'+esc(r.state)+'</b><br>'+r.messages.map(x=>esc(x.text)).join('<br>');
    if(r.state==='done'){
      $('password').value='';$('privateKey').value='';refreshAll();return;
    }
    if(r.state==='failed')return;
    setTimeout(()=>pollJob(id),1500);
  }catch(e){$('job').textContent=e.message}
}

async function refreshAll(){await Promise.all([loadStatus(),loadAgents()])}

$('agentId').addEventListener('change',syncAgentSelection);
standardPorts();
refreshAll();
setInterval(refreshAll,10000);
