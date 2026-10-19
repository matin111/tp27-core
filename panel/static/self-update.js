(()=>{
'use strict';

const UPDATE_POLL_MS=1800;
let updateBusy=false;
let pollTimer=null;

function u(id){return document.getElementById(id)}
function shortSha(v){return /^[0-9a-f]{40}$/i.test(String(v||''))?String(v).slice(0,12):String(v||'-')}
function sleep(ms){return new Promise(r=>setTimeout(r,ms))}

async function req(path,opt={}){
  const r=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});
  let data={};
  try{data=await r.json()}catch(_){data={detail:await r.text().catch(()=> '')}}
  if(!r.ok)throw new Error(data.detail||data.error||`HTTP ${r.status}`);
  return data;
}

function injectCss(){
  if(u('tp27SelfUpdateStyle'))return;
  const s=document.createElement('style');
  s.id='tp27SelfUpdateStyle';
  s.textContent=`
  .upd-btn.has-update{border-color:#34d39988!important;box-shadow:0 0 0 4px #34d39912 inset}.upd-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--p-muted);margin-inline-end:6px}.upd-btn.has-update .upd-dot{background:#34d399;box-shadow:0 0 0 5px #34d39914}.upd-modal{width:min(760px,96vw)}.upd-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}.upd-kpi{border:1px solid var(--p-border);border-radius:14px;background:var(--p-surface-2);padding:12px}.upd-kpi span,.upd-kpi b{display:block}.upd-kpi span{font-size:10px;color:var(--p-muted)}.upd-kpi b{margin-top:6px;font-size:13px;direction:ltr}.upd-state{border:1px solid var(--p-border);border-radius:14px;padding:12px;background:var(--p-surface-2);line-height:1.9;font-size:11px}.upd-state.good{border-color:#34d39955}.upd-state.warn{border-color:#fbbf2455}.upd-state.bad{border-color:#fb718555}.upd-progress{height:7px;background:var(--p-surface);border-radius:999px;overflow:hidden;margin-top:9px}.upd-progress i{display:block;height:100%;width:0;background:linear-gradient(90deg,#8b5cf6,#22d3ee);transition:width .3s}.upd-backups{margin-top:12px;max-height:170px;overflow:auto;border:1px solid var(--p-border);border-radius:12px}.upd-backup{display:flex;justify-content:space-between;gap:10px;padding:9px 10px;border-bottom:1px solid var(--p-border);font-size:10px}.upd-backup:last-child{border-bottom:0}.upd-msg{font-size:10px;color:var(--p-muted);line-height:1.8;min-height:20px;margin-top:8px}@media(max-width:700px){.upd-grid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(s);
}

function ensureModal(){
  if(u('tp27UpdateModal'))return;
  const m=document.createElement('div');
  m.id='tp27UpdateModal';
  m.className='modal hidden';
  m.setAttribute('role','dialog');
  m.setAttribute('aria-modal','true');
  m.innerHTML=`<div class="modal-card upd-modal">
    <div class="section-title modal-head"><div><h3 style="margin:0">بروزرسانی پنل</h3><div class="muted tiny" style="margin-top:4px">Backup → Update → Health Check → Rollback خودکار در صورت خطا</div></div><button class="btn small" id="updClose" type="button">×</button></div>
    <div class="upd-grid"><div class="upd-kpi"><span>نسخه نصب‌شده</span><b id="updCurrent">-</b></div><div class="upd-kpi"><span>آخرین نسخه GitHub</span><b id="updLatest">-</b></div></div>
    <div id="updState" class="upd-state"><b id="updStateTitle">آماده</b><div id="updStateText" class="muted">در حال بررسی...</div><div class="upd-progress"><i id="updProgress"></i></div></div>
    <div id="updMsg" class="upd-msg"></div>
    <div class="actions" style="margin-top:12px"><button id="updCheck" class="btn" type="button">بررسی نسخه</button><button id="updApply" class="btn primary" type="button">بروزرسانی خودکار</button><button id="updRollback" class="btn" type="button">بازگشت به نسخه قبل</button></div>
    <div style="margin-top:14px"><strong style="font-size:11px">Backupهای اخیر</strong><div id="updBackups" class="upd-backups"></div></div>
  </div>`;
  document.body.appendChild(m);
  u('updClose').onclick=()=>{if(!updateBusy)m.classList.add('hidden')};
  u('updCheck').onclick=()=>refreshStatus(true);
  u('updApply').onclick=applyUpdate;
  u('updRollback').onclick=rollbackUpdate;
}

function progressFor(state){
  return ({queued:10,backup:22,downloading:38,installing:68,rolling_back:82,done:100,rolled_back:100,failed:100,idle:0})[state]??5;
}
function stateClass(state){return state==='done'||state==='rolled_back'?'good':state==='failed'?'bad':state==='rolling_back'?'warn':''}
function terminal(state){return ['idle','done','failed','rolled_back',null,undefined].includes(state)}

function render(data){
  ensureModal();
  const job=data.job||{};
  u('updCurrent').textContent=shortSha(data.current);
  u('updLatest').textContent=shortSha(data.latest);
  u('updStateTitle').textContent=job.state==='idle'?'آماده':({queued:'در صف',backup:'Backup',downloading:'دریافت',installing:'نصب',rolling_back:'Rollback',done:'کامل شد',failed:'ناموفق',rolled_back:'Rollback شد'})[job.state]||job.state||'آماده';
  u('updStateText').textContent=job.message||data.check_error||'آماده';
  u('updState').className='upd-state '+stateClass(job.state);
  u('updProgress').style.width=progressFor(job.state)+'%';
  const backups=data.backups||[];
  u('updBackups').innerHTML=backups.length?backups.map(x=>`<div class="upd-backup"><span>${x.name}</span><b>${shortSha(x.before)}</b></div>`).join(''):'<div class="upd-backup"><span class="muted">هنوز Backup ثبت نشده است</span></div>';
  const available=!!data.update_available;
  const btn=u('premiumPanelUpdateBtn');
  if(btn){btn.classList.toggle('has-update',available);btn.title=available?'نسخه جدید پنل آماده است':'پنل روی آخرین نسخه است'}
  updateBusy=!terminal(job.state);
  u('updApply').disabled=updateBusy||!available;
  u('updRollback').disabled=updateBusy||!backups.length;
  u('updCheck').disabled=updateBusy;
  if(data.check_error)u('updMsg').textContent=data.check_error;
  else if(available)u('updMsg').textContent='نسخه جدید موجود است. بروزرسانی از داخل پنل انجام می‌شود و Tunnelهای فعال Restart نمی‌شوند.';
  else u('updMsg').textContent='نسخه نصب‌شده با GitHub همگام است.';
}

async function refreshStatus(showError=false){
  try{
    const data=await req('/api/system/update/status');
    render(data);
    if(!terminal((data.job||{}).state))startPolling();
    else stopPolling();
    return data;
  }catch(e){
    if(showError&&u('updMsg'))u('updMsg').textContent='خطا: '+(e.message||e);
    throw e;
  }
}

function startPolling(){
  if(pollTimer)return;
  pollTimer=setInterval(async()=>{
    try{
      const data=await refreshStatus(false);
      if(terminal((data.job||{}).state)){
        stopPolling();
        if((data.job||{}).state==='done')setTimeout(()=>location.reload(),1200);
      }
    }catch(_){
      // The panel restarts during update. Keep polling instead of treating the temporary outage as failure.
    }
  },UPDATE_POLL_MS);
}
function stopPolling(){if(pollTimer){clearInterval(pollTimer);pollTimer=null}}

async function applyUpdate(){
  if(updateBusy)return;
  if(!confirm('بروزرسانی پنل اجرا شود؟ قبل از تغییر Backup ساخته می‌شود و در صورت Health Check ناموفق Rollback خودکار انجام می‌شود.'))return;
  updateBusy=true;
  u('updApply').disabled=true;
  u('updMsg').textContent='در حال شروع بروزرسانی...';
  try{
    const r=await req('/api/system/update/apply',{method:'POST',body:'{}'});
    if(r.already_latest){u('updMsg').textContent='پنل همین حالا روی آخرین نسخه است.';updateBusy=false;await refreshStatus(false);return}
    startPolling();
  }catch(e){updateBusy=false;u('updMsg').textContent='خطا: '+(e.message||e);await refreshStatus(false).catch(()=>{})}
}

async function rollbackUpdate(){
  if(updateBusy)return;
  if(!confirm('آخرین Backup پنل بازگردانده شود؟ تنظیمات Tunnel و Port Mapping حذف نمی‌شوند.'))return;
  updateBusy=true;
  u('updRollback').disabled=true;
  try{
    await req('/api/system/update/rollback',{method:'POST',body:'{}'});
    startPolling();
  }catch(e){updateBusy=false;u('updMsg').textContent='خطا: '+(e.message||e);await refreshStatus(false).catch(()=>{})}
}

function openUpdate(){ensureModal();u('tp27UpdateModal').classList.remove('hidden');refreshStatus(true).catch(()=>{})}

function inject(){
  injectCss();
  ensureModal();
  if(u('premiumPanelUpdateBtn'))return true;
  const actions=document.querySelector('.premium-actions');
  if(!actions)return false;
  const b=document.createElement('button');
  b.id='premiumPanelUpdateBtn';
  b.className='btn upd-btn';
  b.type='button';
  b.innerHTML='<span class="upd-dot"></span>آپدیت پنل';
  b.onclick=openUpdate;
  actions.appendChild(b);
  refreshStatus(false).catch(()=>{});
  return true;
}

let tries=0;
const timer=setInterval(()=>{tries++;if(inject()||tries>80)clearInterval(timer)},250);
window.tp27OpenPanelUpdate=openUpdate;
})();
