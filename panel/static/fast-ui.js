async function loadStatus(){
  try{
    const s=await api('/api/status');
    currentStatus=s;
    $('publicIp').textContent=s.public_ip||'-';
    $('exitCount').textContent=s.exits.length;
    $('onlineCount').textContent=s.exits.filter(x=>x.healthy).length;
    $('exitList').innerHTML=s.exits.length?s.exits.map(x=>{
      const total=Number(x.channels_total||1);
      const up=Number(x.channels_healthy||0);
      const fast=x.mode==='fast-pool'||x.mode==='reverse-fast-pool'||total>1;
      const reverse=x.mode==='reverse-fast-pool'||x.transport_direction==='exit-to-iran';
      const state=x.healthy?(x.degraded?'DEGRADED':'ONLINE'):'OFFLINE';
      return `
      <div class="exit tunnel-card">
        <div class="exit-main">
          <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${esc(x.id)}</b></div>
          <div class="muted">${reverse?'Exit→Iran · ':''}${esc(x.endpoint)}:${esc(x.port)} · ${esc(x.interface)} · ${esc(x.remote_ip)}</div>
          <div class="badges">
            ${fast?`<span class="badge"><b>${reverse?'REVERSE FAST':'FAST'}</b> ${up}/${total} کانال</span>`:''}
            <span class="badge"><b>${state}</b></span>
          </div>
          ${mappingBadges(x.mappings)}
        </div>
        <div class="actions tunnel-actions">
          <button class="btn small primary" type="button" onclick="openTunnelManage('${esc(x.id)}')">مدیریت</button>
          <button class="btn small" type="button" onclick="diagnoseTunnel('${esc(x.id)}')">بررسی</button>
          <button class="btn small" type="button" onclick="repairTunnel('${esc(x.id)}')">Restart</button>
        </div>
      </div>`;
    }).join(''):'<div class="muted">بدون تونل</div>';
  }catch(e){$('exitList').innerHTML='<div class="muted">'+esc(e.message)+'</div>'}
}

function renderDiagnosis(d){
  $('diagTitle').textContent='بررسی تونل '+(d.id||'');
  let html='';
  html+=diagFlag('Agent سرور خارج',!!d.agent_online);
  html+=diagFlag('حداقل یک Link ایران',!!d.local_service);
  html+=diagFlag('Interface ایران',!!d.interface_up);
  html+=diagFlag('Ping تونل',!!d.peer_ping);
  if(Number(d.channels_total||0)>1){
    html+=`<div class="diag-row"><span class="dot ${d.channels_healthy?'good':'bad'}"></span><span>Fast Channels</span><b>${esc(d.channels_healthy)}/${esc(d.channels_total)}</b></div>`;
    (d.channels||[]).forEach(ch=>{
      html+=`<div class="diag-row"><span class="dot ${ch.healthy?'good':'bad'}"></span><span>${esc(ch.id||'-')} · ${esc(ch.interface||'-')} · ${esc(ch.remote_ip||'-')}</span><b>${ch.healthy?'ONLINE':'DOWN'}</b></div>`;
    });
  }
  html+=`<div class="diag-meta">${esc(d.endpoint||'-')}:${esc(d.port||'-')} · ${esc(d.interface||'-')} · ${esc(d.remote_ip||'-')}</div>`;
  if(d.local_log)html+=`<h4>لاگ ایران</h4><pre>${esc(d.local_log)}</pre>`;
  if(d.remote_status)html+=`<h4>وضعیت سرور خارج</h4><pre>${esc(d.remote_status)}</pre>`;
  $('diagBody').innerHTML=html;
  $('repairBtn').textContent=d.healthy?'Restart Link':'تعمیر خودکار';
  $('repairBtn').disabled=false;
}

const tp27OpenTunnelManageBase=openTunnelManage;
openTunnelManage=function(id){
  tp27OpenTunnelManageBase(id);
  const x=tunnelById(id);
  const btn=$('upgradeFastBtn');
  if(btn){
    const alreadyFast=!!(x&&(x.mode==='fast-pool'||x.mode==='reverse-fast-pool'||Number(x.channels_total||1)>1));
    btn.classList.toggle('hidden',alreadyFast);
    btn.disabled=alreadyFast;
  }
};

function localAgentV12(version){
  const n=(String(version||'0').match(/\d+/g)||[]).map(Number);
  const a=n[0]||0,b=n[1]||0,c=n[2]||0;
  return a>1||(a===1&&(b>2||(b===2&&c>=0)));
}

async function upgradeManagedTunnelFast(){
  if(!managedTunnelId)return;
  const x=tunnelById(managedTunnelId);
  const box=$('manageMessage');
  if(!x){box.textContent='خطا: اطلاعات Tunnel پیدا نشد';return}
  if(x.mode==='fast-pool'||x.mode==='reverse-fast-pool'||Number(x.channels_total||1)>1){box.textContent='این Tunnel از قبل FAST است';return}
  if(!x.agent_id){box.textContent='خطا: این Tunnel به Agent متصل نیست';return}
  const agent=agents.find(a=>a.agent_id===x.agent_id);
  if(!agent||!agent.online){box.textContent='خطا: Agent سرور خارج آفلاین است';return}
  if(!localAgentV12(agent.version)){
    box.textContent=`ابتدا Agent سرور خارج را از بخش مدیریت سرور به v1.2.0 ارتقا بدهید. نسخه فعلی: v${agent.version||'?'}`;
    return;
  }

  const mappings=(x.mappings||[]).map(m=>({
    service:m.service,
    proto:m.proto,
    listen_port:Number(m.listen_port),
    target_port:Number(m.target_port)
  }));
  const id=x.id;
  const btn=$('upgradeFastBtn');
  if(btn)btn.disabled=true;
  box.textContent='در حال تبدیل Tunnel قدیمی به REVERSE FAST...';

  try{
    const removed=await api('/api/exits/'+encodeURIComponent(id),{method:'DELETE'});
    box.textContent='Link قدیمی جمع شد؛ در حال ساخت Reverse Fast Pool چهارکاناله...';
    const body={
      agent_id:x.agent_id,
      host:x.endpoint||agent.public_ip||'',
      port:22,
      username:'root',
      password:null,
      private_key:null,
      id:id,
      tunnel_port:null,
      mtu:1280,
      mappings:mappings
    };
    const created=await api('/api/exits',{method:'POST',body:JSON.stringify(body)});
    closeTunnelManage();
    $('job').textContent=(removed.warning?removed.warning+' · ':'')+'در حال ساخت REVERSE FAST...';
    pollJob(created.job_id);
  }catch(e){
    box.textContent='خطا در تبدیل به REVERSE FAST: '+e.message;
    if(btn)btn.disabled=false;
    await refreshAll();
  }
}
