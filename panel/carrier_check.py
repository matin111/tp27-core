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

_REMOTE_READONLY_SCRIPT = r"""
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

if command -v python3 >/dev/null 2>&1; then
  raw_socket="$(python3 - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    s.close()
    print("yes")
except PermissionError:
    print("no")
except Exception:
    print("unknown")
PY
)"
  packet_socket="$(python3 - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    s.close()
    print("yes")
except PermissionError:
    print("no")
except Exception:
    print("unknown")
PY
)"
else
  raw_socket=unknown
  packet_socket=unknown
fi
kv RAW_SOCKET_CREATE "$raw_socket"
kv PACKET_SOCKET_CREATE "$packet_socket"

kv IP_FORWARD "$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo unknown)"
kv RP_FILTER_ALL "$(sysctl -n net.ipv4.conf.all.rp_filter 2>/dev/null || echo unknown)"
kv RP_FILTER_DEFAULT "$(sysctl -n net.ipv4.conf.default.rp_filter 2>/dev/null || echo unknown)"
kv IP_NONLOCAL_BIND "$(sysctl -n net.ipv4.ip_nonlocal_bind 2>/dev/null || echo unknown)"

default_line="$(ip route show default 2>/dev/null | head -n1)"
kv DEFAULT_ROUTE "$default_line"
default_iface="$(printf '%s\n' "$default_line" | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
kv DEFAULT_IFACE "$default_iface"
if [ -n "$default_iface" ] && [ -r "/sys/class/net/$default_iface/mtu" ]; then
  kv MTU "$(cat "/sys/class/net/$default_iface/mtu" 2>/dev/null || echo unknown)"
else
  kv MTU unknown
fi
if [ -n "$default_iface" ]; then
  kv RP_FILTER_IFACE "$(sysctl -n "net.ipv4.conf.${default_iface}.rp_filter" 2>/dev/null || echo unknown)"
  kv ACCEPT_LOCAL_IFACE "$(sysctl -n "net.ipv4.conf.${default_iface}.accept_local" 2>/dev/null || echo unknown)"
else
  kv RP_FILTER_IFACE unknown
  kv ACCEPT_LOCAL_IFACE unknown
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

if [ -r /sys/class/dmi/id/sys_vendor ]; then
  kv DMI_VENDOR "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"
else
  kv DMI_VENDOR unknown
fi
if [ -r /sys/class/dmi/id/product_name ]; then
  kv DMI_PRODUCT "$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
else
  kv DMI_PRODUCT unknown
fi
if [ -d /etc/cloud ]; then kv CLOUD_INIT yes; else kv CLOUD_INIT no; fi
if [ -r /proc/self/ns/net ]; then kv NETNS yes; else kv NETNS unknown; fi

# Safety boundary: capability/readiness only. No forged-source packet is sent.
kv FORGED_EGRESS_TEST not_run
"""


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
    evidence: list[str] = []

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
    if values.get('RAW_SOCKET_CREATE') == 'no':
        blockers.append('ساخت Raw IPv4 Socket توسط Kernel رد شد')
    elif values.get('RAW_SOCKET_CREATE') == 'unknown':
        warnings.append('ساخت Raw IPv4 Socket به‌صورت قطعی ارزیابی نشد')
    if values.get('PACKET_SOCKET_CREATE') == 'no':
        blockers.append('ساخت AF_PACKET Raw Socket توسط Kernel رد شد')
    elif values.get('PACKET_SOCKET_CREATE') == 'unknown':
        warnings.append('ساخت AF_PACKET Raw Socket به‌صورت قطعی ارزیابی نشد')
    if not values.get('DEFAULT_ROUTE'):
        blockers.append('Default route پیدا نشد')

    if values.get('IP_FORWARD') != '1':
        warnings.append('IPv4 forwarding فعال نیست')
    strict_rpf = any(
        values.get(key) == '1'
        for key in ('RP_FILTER_ALL', 'RP_FILTER_DEFAULT', 'RP_FILTER_IFACE')
    )
    if strict_rpf:
        warnings.append('rp_filter روی حالت strict دیده شد و برای مسیرهای نامتقارن نیاز به بازبینی دارد')
    if values.get('FIREWALL') == 'none':
        warnings.append('nftables/iptables پیدا نشد')
    if values.get('GRE') == 'unknown':
        warnings.append('پشتیبانی GRE از بررسی read-only قطعی نشد')
    if values.get('FOU') == 'unknown':
        warnings.append('پشتیبانی FOU از بررسی read-only قطعی نشد')

    virt = (values.get('VIRT') or '').lower()
    if virt in {'lxc', 'openvz', 'docker', 'podman', 'container-other'}:
        evidence.append('محیط Container/VPS محدودشده تشخیص داده شد؛ Provider ممکن است Raw Networking را محدود کند')
    elif virt and virt not in {'unknown', 'none'}:
        evidence.append(f'نوع مجازی‌سازی گزارش‌شده: {values.get("VIRT")}')

    vendor = values.get('DMI_VENDOR')
    product = values.get('DMI_PRODUCT')
    if vendor and vendor != 'unknown':
        evidence.append(f'DMI Vendor: {vendor}')
    if product and product != 'unknown':
        evidence.append(f'DMI Product: {product}')
    if values.get('CLOUD_INIT') == 'yes':
        evidence.append('cloud-init روی سیستم وجود دارد؛ سیاست واقعی Source Validation همچنان از داخل VPS قابل اثبات نیست')

    score = max(0, 100 - (20 * len(blockers)) - (5 * len(warnings)))
    if blockers:
        level = 'limited'
        title = 'Host Limited'
        summary = 'چند قابلیت ضروری Raw/L3 روی خود سرور در دسترس نیست.'
    elif warnings:
        level = 'needs-config'
        title = 'Host Ready با تنظیم'
        summary = 'قابلیت‌های اصلی موجود است، اما چند تنظیم شبکه قبل از استفاده Production باید بازبینی شود.'
    else:
        level = 'ready'
        title = 'Host Ready'
        summary = 'از نظر Linux/Kernel و Raw Socket، میزبان آماده به نظر می‌رسد.'

    raw_status = 'ready'
    if values.get('CAP_NET_RAW') == 'no' or values.get('RAW_SOCKET_CREATE') == 'no':
        raw_status = 'blocked'
    elif values.get('CAP_NET_RAW') != 'yes' or values.get('RAW_SOCKET_CREATE') != 'yes':
        raw_status = 'unknown'

    kernel_status = 'ready' if not warnings else 'needs-config'
    host_status = 'limited' if blockers else 'ready'
    overall = 'host_limited' if blockers else 'host_ready_provider_unknown'

    checks = [
        {'key': 'root', 'label': 'دسترسی root', 'ok': values.get('UID') == '0', 'value': values.get('UID', 'unknown')},
        {'key': 'tun', 'label': 'TUN/TAP', 'ok': yes('TUN'), 'value': values.get('TUN', 'unknown')},
        {'key': 'net_admin', 'label': 'CAP_NET_ADMIN', 'ok': yes('CAP_NET_ADMIN'), 'value': values.get('CAP_NET_ADMIN', 'unknown')},
        {'key': 'net_raw', 'label': 'CAP_NET_RAW', 'ok': yes('CAP_NET_RAW'), 'value': values.get('CAP_NET_RAW', 'unknown')},
        {'key': 'raw_socket', 'label': 'Raw IPv4 Socket', 'ok': values.get('RAW_SOCKET_CREATE') == 'yes', 'value': values.get('RAW_SOCKET_CREATE', 'unknown')},
        {'key': 'packet_socket', 'label': 'AF_PACKET Raw Socket', 'ok': values.get('PACKET_SOCKET_CREATE') == 'yes', 'value': values.get('PACKET_SOCKET_CREATE', 'unknown')},
        {'key': 'iproute', 'label': 'iproute2', 'ok': yes('IPROUTE'), 'value': values.get('IPROUTE', 'unknown')},
        {'key': 'forward', 'label': 'IPv4 Forwarding', 'ok': values.get('IP_FORWARD') == '1', 'value': values.get('IP_FORWARD', 'unknown')},
        {'key': 'rpf', 'label': 'rp_filter خروجی', 'ok': values.get('RP_FILTER_IFACE') in {'0', '2'}, 'value': values.get('RP_FILTER_IFACE', 'unknown')},
        {'key': 'nonlocal', 'label': 'ip_nonlocal_bind', 'ok': values.get('IP_NONLOCAL_BIND') == '1', 'value': values.get('IP_NONLOCAL_BIND', 'unknown')},
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
            'rp_filter_interface': values.get('RP_FILTER_IFACE') or None,
            'ip_nonlocal_bind': values.get('IP_NONLOCAL_BIND') or None,
            'accept_local_interface': values.get('ACCEPT_LOCAL_IFACE') or None,
            'wireguard': values.get('WIREGUARD') or None,
            'dmi_vendor': values.get('DMI_VENDOR') or None,
            'dmi_product': values.get('DMI_PRODUCT') or None,
        },
        'spoof_readiness': {
            'overall': overall,
            'host_runtime': host_status,
            'raw_socket': raw_status,
            'kernel_network': kernel_status,
            'provider_source_validation': 'unknown',
            'forged_egress_test': values.get('FORGED_EGRESS_TEST', 'not_run'),
            'provider_evidence': evidence,
            'verdict': (
                'قابلیت‌های محلی میزبان مناسب‌اند؛ نتیجه دیتاسنتر هنوز نامشخص است.'
                if not blockers
                else 'خود میزبان قبل از هر ارزیابی Provider نیاز به اصلاح دارد.'
            ),
        },
        'provider_egress': {
            'status': 'unknown',
            'label': 'Provider Source Validation',
            'detail': (
                'این گزارش فقط آمادگی Host را مشخص می‌کند. اجازه یا مسدودبودن Source Address جعلی '
                'در شبکه بالادستی بدون تست فعال Provider قابل اثبات نیست؛ هیچ Packet جعلی ارسال نشده است.'
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


_CHECKER_PAGE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spoof Readiness · TP27</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
*{box-sizing:border-box}
body{margin:0;background:#090910;color:#f5f2ff}
.wrap{max-width:1120px;margin:28px auto;padding:16px}
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
.limited,.blocked{background:#401f26;color:#ff9a9a}
.unknown,.not_run{background:#25243a;color:#c9c4e8}
.checks{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:16px}
.check{border:1px solid #302c45;border-radius:14px;padding:13px;background:#101019}
.ok{color:#7de9aa}.fail{color:#ff9393}
.lists,.readiness{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.readiness{grid-template-columns:repeat(4,minmax(0,1fr))}
.readiness .check b{display:block;margin-bottom:7px}
ul{margin:8px 0;padding-right:22px;line-height:1.8}
.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}
.meta div{background:#101019;border:1px solid #302c45;border-radius:12px;padding:10px}
.hidden{display:none}
@media(max-width:760px){.grid,.checks,.lists,.meta,.readiness{grid-template-columns:1fr}.wrap{margin:8px auto;padding:10px}h1{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="top"><a href="/">← پنل اصلی</a><span class="muted">TP27 Spoof Readiness</span></div>
    <h1>Spoof Readiness / Server Compatibility</h1>
    <p class="muted">IP و SSH سرور را بده. TP27 به‌صورت read-only بررسی می‌کند خود VPS از نظر Raw Socket، Kernel، Routing و قابلیت‌های L3 آماده هست یا نه.</p>
    <div class="notice">این صفحه «آمادگی میزبان» را می‌سنجد، نه اثبات عبور IP جعلی از دیتاسنتر. هیچ Packet با Source جعلی ارسال نمی‌شود و Provider Source Validation تا زمانی که مدرک خارجی/آزمایش مجاز جداگانه وجود نداشته باشد «نامشخص» می‌ماند.</div>
    <div class="grid" style="margin-top:16px">
      <label>IP عمومی سرور<input id="host" placeholder="203.0.113.10" autocomplete="off"></label>
      <label>پورت SSH<input id="port" type="number" value="22" min="1" max="65535"></label>
      <label>نام کاربری SSH<input id="username" value="root" autocomplete="username"></label>
      <label>رمز SSH<input id="password" type="password" autocomplete="current-password" placeholder="در صورت استفاده از رمز"></label>
    </div>
    <label style="margin-top:12px">Private Key اختیاری<textarea id="private_key" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label>
    <p class="muted">Credential فقط برای همین درخواست استفاده می‌شود و در فایل تنظیمات Checker ذخیره نمی‌شود.</p>
    <button id="run" class="primary">بررسی Spoof Readiness</button>
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

function stateLabel(v){
  return ({
    ready:'READY',
    limited:'LIMITED',
    blocked:'BLOCKED',
    unknown:'UNKNOWN',
    'needs-config':'NEEDS CONFIG',
    not_run:'NOT RUN'
  })[v] || String(v||'UNKNOWN').toUpperCase();
}

function stateClass(v){
  if(v==='ready') return 'ready';
  if(v==='limited'||v==='blocked') return 'limited';
  if(v==='needs-config') return 'needs-config';
  return 'unknown';
}

function render(d){
  const checks=(d.checks||[]).map(x=>`<div class="check"><b>${esc(x.label)}</b><div class="${x.ok?'ok':'fail'}">${x.ok?'✓ مناسب':'✕ نیاز به بررسی'}</div><div class="muted">${esc(x.value)}</div></div>`).join('');
  const blockers=(d.blockers||[]).length?`<ul>${d.blockers.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<div class="muted">موردی نیست</div>';
  const warnings=(d.warnings||[]).length?`<ul>${d.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<div class="muted">موردی نیست</div>';
  const s=d.server||{};
  const r=d.spoof_readiness||{};
  const evidence=(r.provider_evidence||[]).length?`<ul>${r.provider_evidence.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<div class="muted">مدرک محلی خاصی پیدا نشد</div>';
  $('result').innerHTML=`
    <div class="result-head">
      <div><span class="badge ${esc(d.level)}">${esc(d.title)}</span><h2 style="margin-top:12px">${esc(d.summary)}</h2></div>
      <div class="score">${esc(d.score)}%</div>
    </div>

    <h3>Spoof Readiness</h3>
    <div class="readiness">
      <div class="check"><b>Host Runtime</b><span class="badge ${stateClass(r.host_runtime)}">${esc(stateLabel(r.host_runtime))}</span></div>
      <div class="check"><b>Raw Socket</b><span class="badge ${stateClass(r.raw_socket)}">${esc(stateLabel(r.raw_socket))}</span></div>
      <div class="check"><b>Kernel / Network</b><span class="badge ${stateClass(r.kernel_network)}">${esc(stateLabel(r.kernel_network))}</span></div>
      <div class="check"><b>Provider Validation</b><span class="badge unknown">${esc(stateLabel(r.provider_source_validation))}</span></div>
    </div>
    <div class="notice" style="margin-top:12px"><b>نتیجه:</b> ${esc(r.verdict||'')}</div>

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
      <div><b>rp_filter Interface</b><br><span class="muted">${esc(s.rp_filter_interface||'-')}</span></div>
      <div><b>ip_nonlocal_bind</b><br><span class="muted">${esc(s.ip_nonlocal_bind||'-')}</span></div>
    </div>

    <div class="card" style="margin-top:14px;margin-bottom:0">
      <h3>Provider Evidence</h3>
      ${evidence}
      <div class="notice" style="margin-top:12px">
        <b>${esc(d.provider_egress?.label||'Provider Source Validation')}</b><br>
        ${esc(d.provider_egress?.detail||'')}
        <br><b>Forged egress test:</b> ${esc(stateLabel(r.forged_egress_test))}
      </div>
    </div>`;
  $('result').classList.remove('hidden');
}

$('run').onclick=async()=>{
  const btn=$('run');
  btn.disabled=true;
  $('status').textContent='در حال بررسی SSH، Raw Socket و قابلیت‌های شبکه...';
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
</html>"""


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
