import re

from ports import PORT_CONFIG, atomic_json, get_exit, reconcile, state


def _service_name(value):
    safe = re.sub(r'[^a-z0-9_-]', '-', str(value or 'custom').lower()).strip('-')[:24]
    return safe or 'custom'


def _expand_openvpn_dual_protocol(mappings):
    """OpenVPN deployments may listen on TCP or UDP.

    The panel historically created only the protocol selected by the UI, which
    made UDP-only OpenVPN servers look broken while Cisco kept working. For
    OpenVPN rows, create the missing TCP/UDP twin with the same Iran/Exit
    ports. Explicit rows are preserved and never duplicated.
    """
    rows = [dict(x) for x in (mappings or [])]
    keys = {
        (
            _service_name(x.get('service')),
            str(x.get('proto', '')).lower().strip(),
            int(x.get('listen_port', 0) or 0),
            int(x.get('target_port', 0) or 0),
        )
        for x in rows
    }
    additions = []
    for raw in rows:
        service = _service_name(raw.get('service'))
        proto = str(raw.get('proto', '')).lower().strip()
        if service != 'openvpn' or proto not in {'tcp', 'udp'}:
            continue
        twin = 'udp' if proto == 'tcp' else 'tcp'
        listen = int(raw.get('listen_port', 0) or 0)
        target = int(raw.get('target_port', 0) or 0)
        key = (service, twin, listen, target)
        if key in keys:
            continue
        copy = dict(raw)
        copy['proto'] = twin
        additions.append(copy)
        keys.add(key)
    return rows + additions


def validate_plan(mappings, exclude_exit=None):
    cfg = state()
    existing = [m for m in cfg.get('mappings', [])
                if m.get('enabled', True) and m.get('exit_id') != exclude_exit]
    normalized = []
    occupied = {(m.get('proto'), int(m.get('listen_port', 0))): m for m in existing}

    for index, raw in enumerate(_expand_openvpn_dual_protocol(mappings), start=1):
        proto = str(raw.get('proto', '')).lower().strip()
        if proto not in {'tcp', 'udp'}:
            raise ValueError(f'ردیف {index}: پروتکل باید TCP یا UDP باشد')
        listen = int(raw.get('listen_port', 0))
        target = int(raw.get('target_port', 0))
        if not 1 <= listen <= 65535:
            raise ValueError(f'ردیف {index}: پورت ایران نامعتبر است')
        if not 1 <= target <= 65535:
            raise ValueError(f'ردیف {index}: پورت خارج نامعتبر است')
        service = _service_name(raw.get('service'))
        key = (proto, listen)
        old = occupied.get(key)
        if old:
            owner = old.get('exit_id') or old.get('service') or 'mapping'
            raise ValueError(
                f'پورت {proto.upper()}/{listen} روی IP ایران قبلاً برای {owner} رزرو شده است'
            )
        item = {
            'service': service,
            'proto': proto,
            'listen_port': listen,
            'target_port': target,
            'port_mode': 'preserve' if listen == target else 'custom',
            'enabled': True,
        }
        occupied[key] = item
        normalized.append(item)

    return normalized


def apply_plan(exit_id, mappings):
    get_exit(exit_id)
    normalized = validate_plan(mappings, exclude_exit=exit_id)
    cfg = state()
    kept = [m for m in cfg.get('mappings', []) if m.get('exit_id') != exit_id]
    built = []
    for index, item in enumerate(normalized, start=1):
        built.append({
            'id': f"{exit_id}-{item['service']}-{item['proto']}-{item['listen_port']}-{index}",
            'exit_id': exit_id,
            **item,
        })
    cfg['mappings'] = kept + built
    atomic_json(PORT_CONFIG, cfg)
    reconcile()
    return built


# ---------------------------------------------------------------------------
# Read-only External Carrier Monitor
# ---------------------------------------------------------------------------
# This adapter intentionally does not create, configure, restart, or alter an
# external carrier. It only stores monitor metadata and reads Linux interface,
# systemd, and optional peer-ping health so externally managed links can be
# observed from the TP27 panel without changing the data plane.

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import agent_control
from auth import session_ok
from provision import run

_EXTERNAL_MONITOR_FILE = Path('/etc/tp27/external-carriers.json')
_EXTERNAL_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,32}')
_EXTERNAL_IFACE_RE = re.compile(r'[A-Za-z0-9_.-]{1,15}')
_EXTERNAL_SERVICE_RE = re.compile(r'[A-Za-z0-9_.@-]{1,96}\.service')


class ExternalCarrierMonitorInput(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    interface: str = Field(min_length=1, max_length=15)
    service: Optional[str] = Field(default=None, max_length=96)
    peer_ip: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=160)


def _external_guard(request: Request):
    if not session_ok(request.cookies.get('tp27_session', '')):
        raise HTTPException(status_code=401, detail='authentication required')


def _external_load():
    try:
        data = json.loads(_EXTERNAL_MONITOR_FILE.read_text(encoding='utf-8'))
        rows = data.get('items', []) if isinstance(data, dict) else []
        return [x for x in rows if isinstance(x, dict)]
    except Exception:
        return []


def _external_save(rows):
    _EXTERNAL_MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _EXTERNAL_MONITOR_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps({'items': rows}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    os.replace(tmp, _EXTERNAL_MONITOR_FILE)


def _external_validate(body: ExternalCarrierMonitorInput):
    eid = body.id.strip()
    iface = body.interface.strip()
    service = (body.service or '').strip() or None
    peer_ip = (body.peer_ip or '').strip() or None
    if not _EXTERNAL_ID_RE.fullmatch(eid):
        raise ValueError('شناسه مانیتور نامعتبر است')
    if not _EXTERNAL_IFACE_RE.fullmatch(iface):
        raise ValueError('نام Interface نامعتبر است')
    if service and not _EXTERNAL_SERVICE_RE.fullmatch(service):
        raise ValueError('نام systemd service نامعتبر است')
    if peer_ip:
        import ipaddress
        peer_ip = str(ipaddress.ip_address(peer_ip))
    return {
        'id': eid,
        'interface': iface,
        'service': service,
        'peer_ip': peer_ip,
        'note': (body.note or '').strip()[:160] or None,
    }


def _external_status(row):
    iface = str(row.get('interface') or '')
    service = str(row.get('service') or '')
    peer_ip = str(row.get('peer_ip') or '')

    iface_rc, iface_out, iface_err = run(['ip', '-br', 'link', 'show', 'dev', iface], check=False)
    addr_rc, addr_out, _ = run(['ip', '-br', 'addr', 'show', 'dev', iface], check=False)
    _, link_out, _ = run(['ip', '-s', 'link', 'show', 'dev', iface], check=False)

    service_active = None
    service_text = ''
    if service:
        svc_rc, svc_out, svc_err = run(['systemctl', 'is-active', service], check=False)
        service_active = svc_rc == 0
        service_text = (svc_out or svc_err or '').strip()

    ping_ok = None
    ping_text = ''
    if peer_ip and iface_rc == 0:
        ping_rc, ping_out, ping_err = run([
            'ping', '-I', iface, '-c', '3', '-i', '0.2', '-W', '1', '-w', '3', peer_ip,
        ], check=False)
        ping_ok = ping_rc == 0
        ping_text = (ping_out or ping_err or '')[-1200:]

    healthy = iface_rc == 0
    if service_active is False:
        healthy = False
    if ping_ok is False:
        healthy = False

    return {
        **row,
        'healthy': healthy,
        'interface_up': iface_rc == 0,
        'interface_state': (iface_out or iface_err or '').strip(),
        'address_state': addr_out.strip() if addr_rc == 0 else '',
        'service_active': service_active,
        'service_text': service_text,
        'peer_ping': ping_ok,
        'ping_text': ping_text,
        'link_state': link_out[-2400:],
    }


_EXTERNAL_MONITOR_PAGE = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>External Carrier Monitor · TP27</title>
<style>:root{color-scheme:dark light;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}body{margin:0;background:#0b0b12;color:#f4f2ff}.wrap{max-width:920px;margin:32px auto;padding:16px}.card{background:#151522;border:1px solid #2d2a42;border-radius:20px;padding:20px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}label{display:grid;gap:6px;color:#bbb4cf;font-size:13px}input,button{font:inherit;border-radius:11px;border:1px solid #3a3554;background:#0f0f18;color:#fff;padding:11px}button{cursor:pointer}.primary{background:#7c5cff;border-color:#7c5cff;font-weight:700}.muted{color:#aaa4be;line-height:1.7}.row{display:flex;gap:10px;align-items:center;justify-content:space-between}.item{padding:14px;border:1px solid #302c45;border-radius:14px;margin-top:10px}.ok{color:#71e6a0}.bad{color:#ff8585}a{color:#bcaeff}@media(max-width:680px){.grid{grid-template-columns:1fr}.wrap{margin:10px auto;padding:10px}}</style></head><body><div class="wrap"><div class="card"><div class="row"><a href="/">← پنل اصلی</a><button onclick="load()">تازه‌سازی</button></div><h1>External Carrier Monitor</h1><p class="muted">این بخش فقط وضعیت Carrierهایی را که خارج از TP27 ساخته و مدیریت شده‌اند نمایش می‌دهد؛ هیچ تنظیم شبکه یا سرویس را تغییر نمی‌دهد.</p><div class="grid"><label>شناسه<input id="id" placeholder="carrier01"></label><label>Interface<input id="iface" placeholder="wg-ext0"></label><label>Systemd service اختیاری<input id="service" placeholder="my-carrier.service"></label><label>Peer IP اختیاری<input id="peer" placeholder="10.10.10.2"></label><label>یادداشت<input id="note" placeholder="مسیر آزمایشی"></label></div><p><button class="primary" id="save">ثبت مانیتور</button></p><div id="msg" class="muted">آماده</div></div><div class="card"><h2>Carrierها</h2><div id="list" class="muted">در حال دریافت...</div></div></div><script>
const $=x=>document.getElementById(x);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));async function api(u,o={}){const r=await fetch(u,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...o});const t=r.headers.get('content-type')||'';const b=t.includes('json')?await r.json():{detail:await r.text()};if(!r.ok)throw new Error(b.detail||b.error||'خطا');return b}async function load(){try{const d=await api('/api/external-carriers');$('list').innerHTML=d.items.length?d.items.map(x=>`<div class="item"><div class="row"><b>${esc(x.id)}</b><span class="${x.healthy?'ok':'bad'}">${x.healthy?'سالم':'نیاز به بررسی'}</span></div><div class="muted">${esc(x.interface)} · ${esc(x.service||'بدون service')} · ${x.peer_ping===null?'Ping غیرفعال':(x.peer_ping?'Ping OK':'Ping FAIL')}</div><div class="muted">${esc(x.interface_state||'')}</div><button onclick="del('${esc(x.id)}')">حذف مانیتور</button></div>`).join(''):'موردی ثبت نشده'}catch(e){$('list').textContent='خطا: '+e.message}}async function del(id){if(!confirm('فقط رکورد مانیتور حذف شود؟'))return;try{await api('/api/external-carriers/'+encodeURIComponent(id),{method:'DELETE'});load()}catch(e){$('msg').textContent='خطا: '+e.message}}$('save').onclick=async()=>{try{const b={id:$('id').value.trim(),interface:$('iface').value.trim(),service:$('service').value.trim()||null,peer_ip:$('peer').value.trim()||null,note:$('note').value.trim()||null};await api('/api/external-carriers',{method:'POST',body:JSON.stringify(b)});$('msg').textContent='ثبت شد';load()}catch(e){$('msg').textContent='خطا: '+e.message}};load();</script></body></html>'''


@agent_control.router.get('/external-carriers', response_class=HTMLResponse)
async def external_carriers_page(request: Request):
    _external_guard(request)
    return HTMLResponse(_EXTERNAL_MONITOR_PAGE)


@agent_control.router.get('/api/external-carriers')
async def external_carriers_status(request: Request):
    _external_guard(request)
    return {'items': [_external_status(row) for row in _external_load()]}


@agent_control.router.post('/api/external-carriers')
async def external_carriers_register(request: Request, body: ExternalCarrierMonitorInput):
    _external_guard(request)
    try:
        item = _external_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rows = [x for x in _external_load() if x.get('id') != item['id']]
    rows.append(item)
    _external_save(rows)
    return {'ok': True, 'item': _external_status(item)}


@agent_control.router.delete('/api/external-carriers/{carrier_id}')
async def external_carriers_delete(request: Request, carrier_id: str):
    _external_guard(request)
    if not _EXTERNAL_ID_RE.fullmatch(carrier_id or ''):
        raise HTTPException(status_code=400, detail='شناسه نامعتبر است')
    rows = _external_load()
    kept = [x for x in rows if x.get('id') != carrier_id]
    if len(kept) == len(rows):
        raise HTTPException(status_code=404, detail='مانیتور پیدا نشد')
    _external_save(kept)
    return {'ok': True, 'id': carrier_id}
