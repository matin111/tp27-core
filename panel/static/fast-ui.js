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
      const mappingCount=(x.mappings||[]).length;
      return `
      <div class="exit tunnel-card">
        <div class="exit-main">
          <div><span class="dot ${x.healthy?'good':'bad'}"></span><b>${esc(x.id)}</b></div>
          <div class="muted">${reverse?'Exit→Iran · ':''}${esc(x.endpoint)}:${esc(x.port)} · ${esc(x.interface)} · ${esc(x.remote_ip)}</div>
          <div class="badges">
            ${fast?`<span class="badge"><b>${reverse?'REVERSE FAST':'FAST'}</b> ${up}/${total} کانال</span>`:''}
            <span class="badge"><b>${state}</b></span>
            ${mappingCount===0?'<span class="badge"><b>بدون Port Mapping</b></span>':''}
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
  const x=tunnelById(d.id||'');
  const mappings=(x&&x.mappings)||[];
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
  html+=`<div class="diag-row"><span class="dot ${mappings.length?'good':'wait'}"></span><span>Port Mapping</span><b>${mappings.length?esc(mappings.length)+' فعال':'تعریف نشده'}</b></div>`;
  html+=`<div class="diag-meta">${esc(d.endpoint||'-')}:${esc(d.port||'-')} · ${esc(d.interface||'-')} · ${esc(d.remote_ip||'-')}</div>`;
  if(!mappings.length)html+='<div class="muted">این Link سالم است اما Port Mapping ندارد؛ برای عبور سرویس کاربران باید پورت‌های موردنیاز روی همین Tunnel ثبت شوند.</div>';
  if(d.local_log)html+=`<h4>لاگ ایران</h4><pre>${esc(d.local_log)}</pre>`;
  if(d.remote_status)html+=`<h4>وضعیت سرور خارج</h4><pre>${esc(d.remote_status)}</pre>`;
  html+=`<div class="actions wizard-actions"><button class="btn small" type="button" onclick="startTunnelStability('${esc(d.id||'')}')">تست پایداری ۱۲ دور</button></div><div id="stabilityBox" class="muted"></div>`;
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

let tp27StabilityToken=0;

function tunnelDoctorAdvice(d,x){
  const notes=[];
  if(!d.agent_online)notes.push('Agent خارج آفلاین است؛ مسیر کنترل سرور خارج را بررسی کنید.');
  if(!d.local_service)notes.push('سرویس Link ایران فعال نیست؛ قبل از تغییر Port Mapping سرویس را بررسی کنید.');
  if(!d.interface_up)notes.push('Interface تونل روی ایران وجود ندارد یا Down است.');
  if(d.interface_up&&!d.peer_ping)notes.push('Interface بالا است ولی Peer پاسخ نمی‌دهد؛ Data Plane را بررسی کنید.');
  if(Number(d.channels_total||0)>1&&Number(d.channels_healthy||0)<Number(d.channels_total||0))notes.push('Pool در حالت DEGRADED است؛ همه کانال‌ها سالم نیستند.');
  if(!((x&&x.mappings)||[]).length)notes.push('هیچ Port Mapping برای این Tunnel ثبت نشده است.');
  return notes;
}

async function startTunnelStability(id){
  if(!id)return;
  const token=++tp27StabilityToken;
  const box=$('stabilityBox');
  if(!box)return;
  const rounds=12;
  let attempts=0;
  let success=0;
  let degraded=0;
  let currentFailureStreak=0;
  let maxFailureStreak=0;
  let lastError='';
  box.textContent='تست پایداری شروع شد؛ ۰/۱۲';

  for(let i=0;i<rounds;i++){
    if(token!==tp27StabilityToken)return;
    attempts++;
    try{
      const d=await api('/api/exits/'+encodeURIComponent(id)+'/diagnose');
      if(d.healthy){
        success++;
        currentFailureStreak=0;
        if(d.degraded)degraded++;
      }else{
        currentFailureStreak++;
        maxFailureStreak=Math.max(maxFailureStreak,currentFailureStreak);
        const notes=tunnelDoctorAdvice(d,tunnelById(id));
        lastError=notes[0]||'Health Check ناموفق بود';
      }
    }catch(e){
      currentFailureStreak++;
      maxFailureStreak=Math.max(maxFailureStreak,currentFailureStreak);
      lastError=e.message||'خطای ارتباط با پنل';
    }
    const pct=Math.round((success/attempts)*100);
    box.innerHTML=`<b>پایداری ${pct}%</b> · دور ${attempts}/${rounds} · بیشترین شکست متوالی ${maxFailureStreak}${degraded?` · DEGRADED ${degraded} دور`:''}${lastError?`<br><span class="muted">آخرین خطا: ${esc(lastError)}</span>`:''}`;
    if(i<rounds-1)await sleep(750);
  }
}

function ensureFleetDoctorUi(){
  if($('fleetDoctorModal'))return;
  const topActions=document.querySelector('.top .actions');
  if(topActions&&!$('fleetDoctorBtn')){
    const btn=document.createElement('button');
    btn.id='fleetDoctorBtn';
    btn.className='btn';
    btn.type='button';
    btn.textContent='Doctor';
    btn.onclick=openFleetDoctor;
    topActions.insertBefore(btn,topActions.firstChild);
  }
  const modal=document.createElement('div');
  modal.id='fleetDoctorModal';
  modal.className='modal hidden';
  modal.setAttribute('role','dialog');
  modal.setAttribute('aria-modal','true');
  modal.innerHTML=`<div class="modal-card diag-card"><div class="section-title modal-head"><h3>Doctor تونل‌ها</h3><button class="btn small" type="button" onclick="closeFleetDoctor()">×</button></div><div class="muted">بررسی فقط خواندنی است و سرویس‌ها را Restart نمی‌کند.</div><div id="fleetDoctorBody" class="diag-body"></div><div class="actions wizard-actions"><button class="btn primary" type="button" onclick="runFleetDoctor()">بررسی دوباره</button><button class="btn" type="button" onclick="closeFleetDoctor()">بستن</button></div></div>`;
  document.body.appendChild(modal);
}

function openFleetDoctor(){
  ensureFleetDoctorUi();
  $('fleetDoctorModal').classList.remove('hidden');
  runFleetDoctor();
}

function closeFleetDoctor(){
  if($('fleetDoctorModal'))$('fleetDoctorModal').classList.add('hidden');
}

async function runFleetDoctor(){
  ensureFleetDoctorUi();
  const body=$('fleetDoctorBody');
  body.innerHTML='<div class="muted">در حال بررسی همه Tunnelها...</div>';
  try{
    await loadStatus();
    const exits=currentStatus.exits||[];
    if(!exits.length){body.innerHTML='<div class="muted">Tunnel فعالی وجود ندارد.</div>';return}
    const results=[];
    for(const x of exits){
      try{
        const d=await api('/api/exits/'+encodeURIComponent(x.id)+'/diagnose');
        results.push({x,d,error:null});
      }catch(e){
        results.push({x,d:null,error:e.message||'خطا'});
      }
    }
    const healthy=results.filter(r=>r.d&&r.d.healthy).length;
    const degraded=results.filter(r=>r.d&&r.d.degraded).length;
    let html=`<div class="diag-meta">سالم ${healthy}/${results.length}${degraded?` · DEGRADED ${degraded}`:''}</div>`;
    for(const r of results){
      if(r.error){
        html+=`<div class="diag-row"><span class="dot bad"></span><span>${esc(r.x.id)}</span><b>${esc(r.error)}</b></div>`;
        continue;
      }
      const notes=tunnelDoctorAdvice(r.d,r.x);
      const ok=r.d.healthy&&!r.d.degraded;
      html+=`<div class="diag-row"><span class="dot ${ok?'good':r.d.healthy?'wait':'bad'}"></span><span>${esc(r.x.id)} · ${esc(r.x.endpoint||'-')}</span><b>${r.d.healthy?(r.d.degraded?'DEGRADED':'ONLINE'):'OFFLINE'}</b></div>`;
      if(notes.length)html+=`<div class="muted">${notes.map(esc).join(' · ')}</div>`;
    }
    body.innerHTML=html;
  }catch(e){
    body.innerHTML='<div class="err">'+esc(e.message||'خطا در Doctor')+'</div>';
  }
}

const tp27CloseTunnelDiagBase=closeTunnelDiag;
closeTunnelDiag=function(){
  tp27StabilityToken++;
  tp27CloseTunnelDiagBase();
};

ensureFleetDoctorUi();
