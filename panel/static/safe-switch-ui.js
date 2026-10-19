(()=>{
'use strict';

async function run(id,button){
  if(!id||button.dataset.safeBusy==='1')return;
  const row=(window.currentStatus&&currentStatus.exits||[]).find(x=>x.id===id);
  const current=String((row&&row.transport)||(row&&row.mode)||'GRE').toLowerCase();
  const target=current==='gre-fou'?'GRE Native':'GRE/UDP FOU';
  if(!confirm(`Tunnel «${id}» به ${target} تعویض شود؟\nمسیر فعلی تا سالم شدن مسیر جدید حذف نمی‌شود.`))return;
  button.dataset.safeBusy='1';
  button.disabled=true;
  button.textContent='در حال تعویض...';
  try{
    const result=await api('/api/exits/'+encodeURIComponent(id)+'/switch',{method:'POST',body:'{}'});
    const job=document.getElementById('job');
    if(job)job.textContent='تعویض امن شروع شد؛ مسیر قبلی همچنان فعال است...';
    if(typeof closeTunnelManage==='function')closeTunnelManage();
    if(typeof pollJob==='function')pollJob(result.job_id,0);
  }catch(e){
    alert('تعویض امن شروع نشد: '+(e.message||e));
    button.disabled=false;
    button.dataset.safeBusy='0';
    button.textContent='تعویض امن';
  }
}

function bind(){
  document.querySelectorAll('.premium-rebuild-btn').forEach(button=>{
    const card=button.closest('.tunnel-card');
    const id=card&&card.dataset?card.dataset.exitId:'';
    button.textContent='تعویض امن';
    button.title='ابتدا مسیر جایگزین ساخته و Health Check می‌شود؛ سپس Cutover انجام می‌شود.';
    button.onclick=()=>run(id,button);
  });
  const button=document.getElementById('upgradeFastBtn');
  if(button){
    let id='';try{id=managedTunnelId||''}catch(_){ }
    button.classList.remove('hidden');
    button.disabled=false;
    button.textContent='تعویض امن Transport';
    button.onclick=()=>run(id,button);
  }
}

bind();
setInterval(bind,700);
})();
