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
      const fast=x.mode==='fast-pool'||total>1;
      const state=x.healthy?(x.degraded?'DEGRADED':'ONLINE'):'OFFLINE';
      return `
      <div class="exit tunnel-card">
        <div class="exit-main">
          <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${esc(x.id)}</b></div>
          <div class="muted">${esc(x.endpoint)}:${esc(x.port)} · ${esc(x.interface)} · ${esc(x.remote_ip)}</div>
          <div class="badges">
            ${fast?`<span class="badge"><b>FAST</b> ${up}/${total} کانال</span>`:''}
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
