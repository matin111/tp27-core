(()=>{
'use strict';

const style=document.createElement('style');
style.textContent='.premium-rebuild-btn,#upgradeFastBtn{visibility:hidden!important;pointer-events:none!important}.premium-rebuild-btn.tp27-safe-ready,#upgradeFastBtn.tp27-safe-ready{visibility:visible!important;pointer-events:auto!important}';
document.head.appendChild(style);

function rowFor(id){
  try{return (currentStatus.exits||[]).find(x=>x.id===id)||null}catch(_){return null}
}

async function run(id,button){
  if(!id||button.dataset.safeBusy==='1')return;
  const row=rowFor(id);
  const current=String((row&&row.transport)||(row&&row.mode)||'GRE').toLowerCase();
  const target=current==='gre-fou'?'GRE Native':'GRE/UDP FOU';
  if(!confirm(`Tunnel «${id}» به ${target} تعویض شود؟\nمسیر فعلی تا سالم شدن مسیر جدید حذف نمی‌شود.`))return;
  button.dataset.safeBusy='1';
  button.disabled=true;
  if(button.textContent!=='در حال تعویض...')button.textContent='در حال تعویض...';
  try{
    const result=await api('/api/exits/'+encodeURIComponent(id)+'/switch',{method:'POST',body:'{}'});
    const job=document.getElementById('job');
    if(job&&job.textContent!=='تعویض امن شروع شد؛ مسیر قبلی همچنان فعال است...')job.textContent='تعویض امن شروع شد؛ مسیر قبلی همچنان فعال است...';
    if(typeof closeTunnelManage==='function'&&button.id==='upgradeFastBtn')closeTunnelManage();
    if(typeof pollJob==='function')pollJob(result.job_id,0);
  }catch(e){
    alert('تعویض امن شروع نشد: '+(e.message||e));
    button.disabled=false;
    button.dataset.safeBusy='0';
    if(button.textContent!=='تعویض امن')button.textContent='تعویض امن';
  }
}

function bindCardButtons(){
  document.querySelectorAll('.premium-rebuild-btn:not(.tp27-safe-ready)').forEach(button=>{
    if(button.dataset.safeBusy==='1')return;
    const card=button.closest('.tunnel-card');
    const id=card&&card.dataset?card.dataset.exitId:'';
    if(!id)return;
    if(button.textContent!=='تعویض امن')button.textContent='تعویض امن';
    button.title='ابتدا مسیر جایگزین ساخته و Health Check می‌شود؛ سپس Cutover انجام می‌شود.';
    button.onclick=()=>run(id,button);
    button.classList.add('tp27-safe-ready');
  });
}

function bindManageButton(){
  const button=document.getElementById('upgradeFastBtn');
  if(!button||button.dataset.safeBusy==='1')return;
  let id='';try{id=managedTunnelId||''}catch(_){ }
  if(!id)return;
  button.classList.remove('hidden');
  button.disabled=false;
  if(button.textContent!=='تعویض امن Transport')button.textContent='تعویض امن Transport';
  button.title='بدون حذف اولیه Tunnel فعلی';
  if(button.dataset.safeExitId!==id){
    button.dataset.safeExitId=id;
    button.onclick=()=>run(id,button);
  }
  button.classList.add('tp27-safe-ready');
}

function bind(){
  bindCardButtons();
  bindManageButton();
}

bind();
setInterval(bind,1200);
})();
