#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from auth import session_ok

router = APIRouter()
_CHECK_SEMAPHORE = asyncio.Semaphore(3)
_USERNAME_RE = re.compile(r'[A-Za-z0-9_.-]{1,32}')

_REMOTE_READONLY_SCRIPT = r'''
set +e

kv() {
  key="$1"
  shift
  value="$*"
  value="$(printf '%s' "$value" | tr '\n\r' '  ')"
  printf '%s=%s\n' "$key" "$value"
}

kv HOSTNAME "$(hostname 2>/dev/null || true)"
kv KERNEL "$(uname -r 2>/dev/null || true)"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  kv OS "${PRETTY_NAME:-${NAME:-unknown}}"
else
  kv OS "unknown"
fi

uid="$(id -u 2>/dev/null || echo unknown)"
kv UID "$uid"

if [ -c /dev/net/tun ]; then kv TUN yes; else kv TUN no; fi
if command -v ip >/dev/null 2>&1; then kv IPROUTE yes; else kv IPROUTE no; fi
if command -v systemctl >/dev/null 2>&1; then kv SYSTEMD yes; else kv SYSTEMD no; fi

if command -v nft >/dev/null 2>&1; then
  kv FIREWALL nft
elif command -v iptables >/dev/null 2>&1; then
  kv FIREWALL iptables
else
  kv FIREWALL none
fi

caphex="$(awk '/^CapEff:/ {print $2}' /proc/self/status 2>/dev/null)"
if [ -n "$caphex" ]; then
  capdec=$((16#$caphex))
  if (( (capdec & (1 << 12)) != 0 )); then kv CAP_NET_ADMIN yes; else kv CAP_NET_ADMIN no; fi
  if (( (capdec & (1 << 13)) != 0 )); then kv CAP_NET_RAW yes; else kv CAP_NET_RAW no; fi
else
  kv CAP_NET_ADMIN unknown
  kv CAP_NET_RAW unknown
fi

kv IP_FORWARD "$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
kv RP_FILTER_ALL "$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null || echo unknown)"
kv RP_FILTER_DEFAULT "$(sysctl -n net.ipv4.conf.default.rp_filter 2>/dev/null || echo unknown)"

default_line="$(ip route show default 2>/dev/null | head -n1)"
kv DEFAULT_ROUTE "$default_line"
default_iface="$(printf '%s\n' "$default_line" | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
kv DEFAULT_IFACE "$default_iface"
if [ -n "$default_iface" ] && [ -r "/sys/class/net/$default_iface/mtu" ]; then
  kv MTU "$(cat "/sys/class/net/$default_iface/mtu" 2>/dev/null || echo unknown)"
else
  kv MTU unknown
fi

module_state() {
  name="$1"
  if grep -qw "$name" /proc/modules 2>/dev/null; then
    printf 'loaded'
    return
  fi
  if command -v modprobe >/dev/null 2>&1 && modprobe -n "$name" >/dev/null 2>&1; then
    printf 'available'
    return
  fi
  printf 'unknown'
}

kv GRE "$(module_state ip_gre)"
kv FOU "$(module_state fou)"
kv WIREGUARD "$(module_state wireguard)"

if command -v systemd-detect-virt >/dev/null 2>&1; then
  virt="$(systemd-detect-virt 2>/dev/null || true)"
  [ -n "$virt" ] || virt="none"
  kv VIRT "$virt"
else
  kv VIRT unknown
fi

# This deliberately does not send packets with a forged source address.
kv FORGED_EGRESS_TEST not_run
'''


class ServerCompatibilityInput(BaseModel):
    host: str = Field(min_length=3, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default='root', min_length=1, max_length=32)
    password: Optional[str] = Field(default=None, max_length=512)
    private_key: Optional[str] = Field(default=None, max_length=20000)


def _guard(request: Request) -> None:
    if not session_ok(request.cookies.get('tp27_session', '')):
        raise HTTPException(status_code=401, detail='authentication required')


def _validate_target(body: ServerCompatibilityInput) -> tuple[str, str]:
    try:
        ip = ipaddress.ip_address(body.host.strip())
    except ValueError as exc:
        raise ValueError('IP سرور نامعتبر است') from exc

    if not ip.is_global:
        raise ValueError('برای این بررسی فقط IP عمومی مجاز است')

    username = body.username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError('نام کاربری SSH نامعتبر است')

    if not (body.password or body.private_key):
        raise ValueError('رمز SSH یا Private Key لازم است')

    return str(ip), username


def _parse_probe(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (text or '').splitlines():
        if '=' not in raw:
            continue
        key, value = raw.split('=', 1)
        key = key.strip()
        if key and key not in values:
            values[key] = value.strip()
    return values


def _capability_result(values: dict[str, str]) -> dict:
    blockers: list[str] = []
    warnings: list[str] = []

    def yes(key: str) -> bool:
        return values.get(key) == 'yes'

    if values.get('UID') != '0':
        blockers.append('کاربر SSH دسترسی root ندارد')
    if not yes('TUN'):
        blockers.append('/dev/net/tun در دسترس نیست')
    if not yes('IPROUTE'):
        blockers.append('iproute2 روی سرور موجود نیست')
    if values.get('CAP_NET_ADMIN') != 'yes':
        blockers.append('CAP_NET_ADMIN در دسترس نیست')
    if values.get('CAP_NET_RAW') != 'yes':
        blockers.append('CAP_NET_RAW در دسترس نیست')
    if not values.get('DEFAULT_ROUTE'):
        blockers.append('Default route پیدا نشد')

    if values.get('IP_FORWARD') != '1':
        warnings.append('IPv4 forwarding فعال نیست')
    if values.get('RP_FILTER_ALL') == '1' or values.get('RP_FILTER_DEFAULT') == '1':
        warnings.append('rp_filter روی حالت strict است و برای مسیرهای نامتقارن ممکن است نیاز به بازبینی داشته باشد')
    if values.get('FIREWALL') == 'none':
        warnings.append('nftables/iptables پیدا نشد')
    if values.get('GRE') == 'unknown':
        warnings.append('پشتیبانی GRE از بررسی read-only قطعی نشد')
    if values.get('FOU') == 'unknown':
        warnings.append('پشتیبانی FOU از بررسی read-only قطعی نشد')

    score = max(0, 100 - (22 * len(blockers)) - (6 * len(warnings)))
    if blockers:
        level = 'limited'
        title = 'ناسازگاری میزبان'
        summary = 'برای Carrierهای سطح پایین چند قابلیت ضروری روی این سرور در دسترس نیست.'
    elif warnings:
        level = 'needs-config'
        title = 'مناسب، با نیاز به تنظیم'
        summary = 'قابلیت‌های اصلی موجود است اما چند تنظیم شبکه قبل از استفاده Production باید بررسی شود.'
    else:
        level = 'ready'
        title = 'سازگاری میزبان بالا'
        summary = 'از نظر قابلیت‌های محلی Linux، سرور برای Carrierهای Raw/L3 آماده به نظر می‌رسد.'

    checks = [
        {'key': 'root', 'label': 'دسترسی root', 'ok': values.get('UID') == '0', 'value': values.get('UID', 'unknown')},
        {'key': 'tun', 'label': 'TUN/TAP', 'ok': yes('TUN'), 'value': values.get('TUN', 'unknown')},
        {'key': 'net_admin', 'label': 'CAP_NET_ADMIN', 'ok': yes('CAP_NET_ADMIN'), 'value': values.get('CAP_NET_ADMIN', 'unknown')},
        {'key': 'net_raw', 'label': 'CAP_NET_RAW', 'ok': yes('CAP_NET_RAW'), 'value': values.get('CAP_NET_RAW', 'unknown')},
        {'key': 'iproute', 'label': 'iproute2', 'ok': yes('IPROUTE'), 'value': values.get('IPROUTE', 'unknown')},
        {'key': 'forward', 'label': 'IPv4 Forwarding', 'ok': values.get('IP_FORWARD') == '1', 'value': values.get('IP_FORWARD', 'unknown')},
        {'key': 'gre', 'label': 'GRE kernel support', 'ok': values.get('GRE') in {'loaded', 'available'}, 'value': values.get('GRE', 'unknown')},
        {'key': 'fou', 'label': 'FOU kernel support', 'ok': values.get('FOU') in {'loaded', 'available'}, 'value': values.get('FOU', 'unknown')},
        {'key': 'firewall', 'label': 'Firewall tooling', 'ok': values.get('FIREWALL') in {'nft', 'iptables'}, 'value': values.get('FIREWALL', 'unknown')},
    ]

    return {
        'level': level,
        'title': title,
        'summary': summary,
        'score': score,
        'blockers': blockers,
        'warnings': warnings,
        'checks': checks,
        'server': {
            'hostname': values.get('HOSTNAME') or None,
            'os': values.get('OS') or None,
            'kernel': values.get('KERNEL') or None,
            'virtualization': values.get('VIRT') or None,
            'default_interface': values.get('DEFAULT_IFACE') or None,
            'mtu': values.get('MTU') or None,
            'rp_filter_all': values.get('RP_FILTER_ALL') or None,
            'rp_filter_default': values.get('RP_FILTER_DEFAULT') or None,
            'wireguard': values.get('WIREGUARD') or None,
        },
        'provider_egress': {
            'status': 'unknown',
            'label': 'فیلتر خروجی دیتاسنتر / Source Validation',
            'detail': (
                'از داخل خود سرور نمی‌توان با اطمینان مشخص کرد شبکه بالادستی اجازه عبور '
                'Source Address جعلی می‌دهد یا نه. این Checker عمداً ترافیک با Source جعلی ارسال نمی‌کند.'
            ),
        },
        'read_only': True,
    }


async def _run_check(body: ServerCompatibilityInput) -> dict:
    host, username = _validate_target(body)

    from provision import open_ssh, ssh_banner_probe, ssh_error_text

    banner = await ssh_banner_probe(host, body.port)
    if not banner.get('tcp_ok'):
        exc = banner.get('exception') or TimeoutError('TCP probe failed')
        raise ValueError(ssh_error_text(exc, tcp_ok=False, banner_ok=False))
    if not banner.get('banner_ok'):
        raise ValueError('TCP وصل شد اما SSH Banner معتبر دریافت نشد')

    conn = None
    try:
        conn = await open_ssh(
            host,
            body.port,
            username,
            body.password,
            body.private_key,
        )
        command = 'bash -lc ' + shlex.quote(_REMOTE_READONLY_SCRIPT)
        result = await conn.run(command, check=False, timeout=25)
        if result.exit_status != 0 and not result.stdout:
            raise ValueError((result.stderr or 'اجرای بررسی read-only ناموفق بود').strip())
        values = _parse_probe(result.stdout)
        report = _capability_result(values)
        report['target'] = {
            'host': host,
            'port': body.port,
            'username': username,
            'ssh_banner': banner.get('banner', ''),
            'host_key_fingerprint': conn.get_server_host_key().get_fingerprint(),
        }
        return report
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(ssh_error_text(exc, tcp_ok=True, banner_ok=True)) from exc
    finally:
        if conn is not None:
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass


_CHECKER_PAGE = r'''<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>بررسی سازگاری سرور · TP27</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
*{box-sizing:border-box}
body{margin:0;background:#090910;color:#f5f2ff}
.wrap{max-width:1080px;margin:28px auto;padding:16px}
.card{background:#151522;border:1px solid #302b48;border-radius:22px;padding:22px;margin-bottom:14px;box-shadow:0 18px 60px #0004}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px}
h1{margin:18px 0 8px;font-size:30px}
h2{margin:0 0 14px}
p{line-height:1.8}
.muted{color:#aaa4be}
.notice{background:#10101a;border:1px solid #36304f;border-radius:14px;padding:12px 14px;color:#c7c0da}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
label{display:grid;gap:7px;color:#bbb4cf;font-size:13px}
input,textarea,button{font:inherit;border-radius:12px;border:1px solid #40395d;background:#0f0f18;color:#fff;padding:12px}
textarea{min-height:120px;resize:vertical}
button{cursor:pointer}
.primary{background:#7c5cff;border-color:#7c5cff;font-weight:800}
.primary:disabled{opacity:.55;cursor:wait}
a{color:#bdaeff}
.result-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.score{font-size:34px;font-weight:900}
.badge{padding:7px 11px;border-radius:999px;font-size:12px;font-weight:800}
.ready{background:#173627;color:#7ff0ad}
.needs-config{background:#3b3016;color:#ffd77a}
.limited{background:#401f26;color:#ff9a9a}
.checks{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:16px}
.check{border:1px solid #302c45;border-radius:14px;padding:13px;background:#101019}
.ok{color:#7de9aa}.fail{color:#ff9393}
.lists{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
ul{margin:8px 0;padding-right:22px;line-height:1.8}
.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}
.meta div{background:#101019;border:1px solid #302c45;border-radius:12px;padding:10px}
.hidden{display:none}
@media(max-width:760px){.grid,.checks,.lists,.meta{grid-template-columns:1fr}.wrap{margin:8px auto;padding:10px}h1{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="top"><a href="/">← پنل اصلی</a><span class="muted">TP27 Server Compatibility</span></div>
    <h1>بررسی سازگاری سرور برای Carrier / Raw Networking</h1>
    <p class="muted">IP و دسترسی SSH سرور را بده. TP27 فقط بررسی‌های read-only انجام می‌دهد و هیچ پکیج، Route، Firewall یا سرویس را تغییر نمی‌دهد.</p>
    <div class="notice">این بررسی قابلیت‌های خود Linux و Kernel را می‌سنجد. برای تشخیص عبور Source Address جعلی از شبکه دیتاسنتر، تست فعال جعل IP انجام نمی‌شود؛ آن بخش در نتیجه با وضعیت «نامشخص» نمایش داده می‌شود.</div>
    <div class="grid" style="margin-top:16px">
      <label>IP عمومی سرور<input id="host" placeholder="203.0.113.10" autocomplete="off"></label>
      <label>پورت SSH<input id="port" type="number" value="22" min="1" max="65535"></label>
      <label>نام کاربری SSH<input id="username" value="root" autocomplete="username"></label>
      <label>رمز SSH<input id="password" type="password" autocomplete="current-password" placeholder="در صورت استفاده از رمز"></label>
    </div>
    <label style="margin-top:12px">Private Key اختیاری<textarea id="private_key" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label>
    <p class="muted">Credential فقط برای همین درخواست استفاده می‌شود و در فایل تنظیمات Checker ذخیره نمی‌شود.</p>
    <button id="run" class="primary">بررسی سرور</button>
    <span id="status" class="muted" style="margin-right:10px">آماده</span>
  </div>

  <div id="result" class="card hidden"></div>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function detailText(v){
  if(Array.isArray(v)) return v.map(x=>detailText(x)).join(' | ');
  if(v && typeof v==='object') return v.msg || v.detail || JSON.stringify(v);
  return String(v ?? 'خطای نامشخص');
}

async function api(url, options={}){
  const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json'},...options});
  const t=r.headers.get('content-type')||'';
  const b=t.includes('json')?await r.json():{detail:await r.text()};
  if(!r.ok) throw new Error(detailText(b.detail || b.error || b));
  return b;
}

function render(d){
  const checks=(d.checks||[]).map(x=>`<div class="check"><b>${esc(x.label)}</b><div class="${x.ok?'ok':'fail'}">${x.ok?'✓ مناسب':'✕ نیاز به بررسی'}</div><div class="muted">${esc(x.value)}</div></div>`).join('');
  const blockers=(d.blockers||[]).length?`<ul>${d.blockers.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<div class="muted">موردی نیست</div>';
  const warnings=(d.warnings||[]).length?`<ul>${d.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<div class="muted">موردی نیست</div>';
  const s=d.server||{};
  $('result').innerHTML=`
    <div class="result-head">
      <div><span class="badge ${esc(d.level)}">${esc(d.title)}</span><h2 style="margin-top:12px">${esc(d.summary)}</h2></div>
      <div class="score">${esc(d.score)}%</div>
    </div>
    <div class="checks">${checks}</div>
    <div class="lists">
      <div><h3>موانع</h3>${blockers}</div>
      <div><h3>هشدارها</h3>${warnings}</div>
    </div>
    <div class="meta">
      <div><b>Hostname</b><br><span class="muted">${esc(s.hostname||'-')}</span></div>
      <div><b>OS / Kernel</b><br><span class="muted">${esc(s.os||'-')} · ${esc(s.kernel||'-')}</span></div>
      <div><b>Default Interface / MTU</b><br><span class="muted">${esc(s.default_interface||'-')} · ${esc(s.mtu||'-')}</span></div>
      <div><b>Virtualization</b><br><span class="muted">${esc(s.virtualization||'-')}</span></div>
    </div>
    <div class="notice" style="margin-top:14px"><b>${esc(d.provider_egress?.label||'Provider Egress')}</b><br>${esc(d.provider_egress?.detail||'')}</div>`;
  $('result').classList.remove('hidden');
}

$('run').onclick=async()=>{
  const btn=$('run');
  btn.disabled=true;
  $('status').textContent='در حال بررسی SSH و قابلیت‌های سرور...';
  $('result').classList.add('hidden');
  try{
    const body={
      host:$('host').value.trim(),
      port:Number($('port').value||22),
      username:$('username').value.trim()||'root',
      password:$('password').value||null,
      private_key:$('private_key').value.trim()||null
    };
    const d=await api('/api/server-compatibility-check',{method:'POST',body:JSON.stringify(body)});
    render(d);
    $('status').textContent='بررسی کامل شد';
  }catch(e){
    $('status').textContent='خطا: '+e.message;
  }finally{
    btn.disabled=false;
  }
};
</script>
</body>
</html>'''


@router.get('/external-carriers', response_class=HTMLResponse)
async def compatibility_page(request: Request):
    _guard(request)
    return HTMLResponse(_CHECKER_PAGE)


@router.post('/api/server-compatibility-check')
async def server_compatibility_check(request: Request, body: ServerCompatibilityInput):
    _guard(request)
    try:
        async with _CHECK_SEMAPHORE:
            return await _run_check(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
