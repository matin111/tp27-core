#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    if text.count(old) != 1:
        raise SystemExit(f'anchor not unique in {path}: count={text.count(old)}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


def append_once(path, marker, block):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    if marker in text:
        return
    p.write_text(text.rstrip() + '\n\n' + block.strip() + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# panel/provision_ipv6.py
# ---------------------------------------------------------------------------
replace_once(
    'panel/provision_ipv6.py',
    'import asyncio\nimport ipaddress\n',
    'import asyncio\nimport hashlib\nimport ipaddress\n',
)
replace_once(
    'panel/provision_ipv6.py',
    "IP6GRE_MTU = 1420\nWIREGUARD_V6_MTU = 1380\nWIREGUARD_PORTS = (51820, 51888, 443)\n",
    "IP6GRE_MTU = 1420\nWIREGUARD_V6_MTU = 1380\nWIREGUARD_PORTS = (51820, 51888, 443)\nSIT_MTU = 1480\nSIT_IP6GRE_MTU = 1400\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "    return str(addr)\n\n\ndef local_public_ipv6() -> str:\n",
    "    return str(addr)\n\n\ndef _public_ipv4(value: str | None) -> str:\n"
    "    try:\n"
    "        addr = ipaddress.ip_address(str(value or '').strip())\n"
    "    except Exception:\n"
    "        return ''\n"
    "    if addr.version != 4 or not addr.is_global:\n"
    "        return ''\n"
    "    return str(addr)\n\n\n"
    "def _sit_iface(eid: str) -> str:\n"
    "    return ('s27' + re.sub(r'[^a-z0-9]', '', str(eid).lower()))[:15]\n\n\n"
    "def _sit_pair(eid: str, iran_v4: str, exit_v4: str):\n"
    "    digest = hashlib.sha256(f'sit6in4|{eid}|{iran_v4}|{exit_v4}'.encode()).hexdigest()\n"
    "    base = f'fd27:{digest[:4]}:{digest[4:8]}:{digest[8:12]}::/127'\n"
    "    net = ipaddress.ip_network(base, strict=False)\n"
    "    return str(net.network_address), str(net.network_address + 1)\n\n\n"
    "def _module_supported(name: str) -> bool:\n"
    "    if Path('/sys/module') .joinpath(name).exists():\n"
    "        return True\n"
    "    rc, _, _ = run(['modprobe', '-n', name], check=False)\n"
    "    return rc == 0\n\n\n"
    "def local_public_ipv6() -> str:\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "def _local_cfg_wg(eid, iface, local_ip, peer_ip, local_v6, remote_v6,\n",
    "def _local_cfg_sit_ip6gre(eid, iface, sit_iface, local_ip, peer_ip,\n"
    "                             iran_v4, exit_v4, local_v6, remote_v6,\n"
    "                             key, agent_id):\n"
    "    cfg = {\n"
    "        'role': 'sit-ip6gre-iran', 'transport': 'sit-ip6gre', 'mode': 'sit-ip6gre',\n"
    "        'id': eid, 'interface': iface, 'sit_interface': sit_iface,\n"
    "        'tunnel_ip': local_ip, 'peer_ip': peer_ip,\n"
    "        'local_public': iran_v4, 'remote_public': exit_v4,\n"
    "        'sit_local_v6': local_v6, 'sit_remote_v6': remote_v6,\n"
    "        'gre_key': int(key), 'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,\n"
    "        'txqueuelen': 2000, 'keepalive_interval': 4, 'keepalive_failures': 4,\n"
    "        'agent_id': agent_id,\n"
    "    }\n"
    "    atomic_json(EXITS / f'{eid}.json', cfg)\n"
    "    return cfg\n\n\n"
    "def _local_cfg_wg(eid, iface, local_ip, peer_ip, local_v6, remote_v6,\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "async def _upgrade_agent_once(agent_id, job_id):\n",
    "async def _remote_sit_status(agent_id, iran_v4):\n"
    "    command = queue_command(agent_id, 'status', {\n"
    "        'sit6in4_preflight_v1': True,\n"
    "        'iran_public_ipv4': iran_v4,\n"
    "    })\n"
    "    item = await wait_command(command['id'], timeout=35)\n"
    "    if item.get('state') != 'done' or not item.get('ok'):\n"
    "        return None\n"
    "    result = item.get('result') or {}\n"
    "    return result.get('sit6in4_preflight_v1')\n\n\n"
    "async def _upgrade_agent_once(agent_id, job_id):\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "    return {**probe, 'public_ipv6': exit_v6}\n\n\nasync def _remote_ip6gre",
    "    return {**probe, 'public_ipv6': exit_v6}\n\n\n"
    "async def _sit_preflight(agent_id, iran_v4, job_id):\n"
    "    probe = await _remote_sit_status(agent_id, iran_v4)\n"
    "    if not isinstance(probe, dict) or probe.get('api') != 1:\n"
    "        if not await _upgrade_agent_once(agent_id, job_id):\n"
    "            return None\n"
    "        deadline = time.monotonic() + 25\n"
    "        while time.monotonic() < deadline:\n"
    "            try:\n"
    "                probe = await _remote_sit_status(agent_id, iran_v4)\n"
    "            except Exception:\n"
    "                probe = None\n"
    "            if isinstance(probe, dict) and probe.get('api') == 1:\n"
    "                break\n"
    "            await asyncio.sleep(1)\n"
    "    if not isinstance(probe, dict) or probe.get('api') != 1:\n"
    "        return None\n"
    "    exit_v4 = _public_ipv4(probe.get('public_ipv4'))\n"
    "    if not exit_v4 or not probe.get('sit_supported') or not probe.get('ip6gre_supported'):\n"
    "        return None\n"
    "    return {**probe, 'public_ipv4': exit_v4}\n\n\n"
    "async def _remote_ip6gre",
)
replace_once(
    'panel/provision_ipv6.py',
    "async def _remote_wireguard(agent_id, eid, iran_v6, exit_v6, remote_ip,\n",
    "async def _remote_sit_ip6gre(agent_id, eid, iran_v4, exit_v4, remote_ip,\n"
    "                               local_addr, exit_sit_v6, iran_sit_v6, key):\n"
    "    command = queue_command(agent_id, 'provision_tls_exit', {\n"
    "        'id': eid, 'transport': 'sit-ip6gre',\n"
    "        'iran_public': iran_v4, 'exit_public': exit_v4,\n"
    "        'sit_local_v6': exit_sit_v6, 'sit_remote_v6': iran_sit_v6,\n"
    "        'tunnel_ip': remote_ip, 'peer_ip': local_addr,\n"
    "        'gre_key': int(key), 'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,\n"
    "        'txqueuelen': 2000,\n"
    "    })\n"
    "    item = await wait_command(command['id'], timeout=80)\n"
    "    if item.get('state') != 'done' or not item.get('ok'):\n"
    "        raise RuntimeError(item.get('error') or 'SIT/6in4 + IP6GRE روی سرور خارج ساخته نشد')\n"
    "    result = item.get('result') or {}\n"
    "    if not result.get('service_active'):\n"
    "        raise RuntimeError('SIT/6in4 خارج Active نشد')\n"
    "    return result\n\n\n"
    "async def _remote_wireguard(agent_id, eid, iran_v6, exit_v6, remote_ip,\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "def _save_success(job_id, core, eid, iface, local_ip, peer_ip, agent_id,\n",
    "async def _cleanup_sit(agent_id, eid, sit_iface):\n"
    "    legacy._cleanup_local(eid)\n"
    "    run(['ip', 'link', 'del', sit_iface], check=False)\n"
    "    await legacy._cleanup_remote(agent_id, eid)\n\n\n"
    "def _save_success(job_id, core, eid, iface, local_ip, peer_ip, agent_id,\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "async def provision_gre_via_agent(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):\n",
    "async def try_sit_fast_path(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):\n"
    "    agent = get_agent(agent_id)\n"
    "    if not agent or not agent.get('online'):\n"
    "        return False\n"
    "    core = load_json(CORE_CONFIG)\n"
    "    iran_v4 = _public_ipv4(core.get('public_ip'))\n"
    "    hinted_exit_v4 = _public_ipv4(agent.get('public_ip'))\n"
    "    if not iran_v4 or not hinted_exit_v4:\n"
    "        return False\n"
    "    if not _module_supported('sit') or not _module_supported('ip6_gre'):\n"
    "        job_set(job_id, message='SIT/6in4: Kernel ایران آماده نیست؛ ادامه با Coreهای فعلی')\n"
    "        return False\n"
    "    try:\n"
    "        probe = await _sit_preflight(agent_id, iran_v4, job_id)\n"
    "    except Exception as exc:\n"
    "        job_set(job_id, message=f'SIT/6in4 Preflight ناموفق: {exc} · fallback فعال')\n"
    "        return False\n"
    "    if not probe:\n"
    "        job_set(job_id, message='SIT/6in4: Exit قابلیت لازم را ندارد؛ ادامه با Coreهای فعلی')\n"
    "        return False\n"
    "    exit_v4 = probe['public_ipv4']\n"
    "    eid = _next_id(core, requested_id)\n"
    "    port_plan = validate_plan(mappings or [])\n"
    "    local_ip, remote_ip = legacy._allocate_pair(core)\n"
    "    local_addr = str(ipaddress.ip_interface(local_ip).ip)\n"
    "    remote_addr = str(ipaddress.ip_interface(remote_ip).ip)\n"
    "    iface = iface_for(eid)\n"
    "    sit_iface = _sit_iface(eid)\n"
    "    iran_sit_v6, exit_sit_v6 = _sit_pair(eid, iran_v4, exit_v4)\n"
    "    key = legacy._gre_key(eid, iran_v4, exit_v4)\n"
    "    job_set(job_id, state='running', message=f'SIT/6in4: تست Protocol 41 {iran_v4} ↔ {exit_v4}')\n"
    "    try:\n"
    "        legacy._cleanup_local(eid)\n"
    "        run(['ip', 'link', 'del', sit_iface], check=False)\n"
    "        _local_cfg_sit_ip6gre(\n"
    "            eid, iface, sit_iface, local_ip, remote_addr, iran_v4, exit_v4,\n"
    "            iran_sit_v6, exit_sit_v6, key, agent_id,\n"
    "        )\n"
    "        _start_local(eid)\n"
    "        await asyncio.sleep(0.25)\n"
    "        remote = await _remote_sit_ip6gre(\n"
    "            agent_id, eid, iran_v4, exit_v4, remote_ip, local_addr,\n"
    "            exit_sit_v6, iran_sit_v6, key,\n"
    "        )\n"
    "        healthy, ping_text = await legacy._health_peer(iface, remote_addr, tries=10)\n"
    "        if not healthy:\n"
    "            raise RuntimeError('Protocol 41 یا IP6GRE دوطرفه تأیید نشد')\n"
    "        rec = {\n"
    "            'id': eid, 'mode': 'sit-ip6gre', 'transport': 'sit-ip6gre',\n"
    "            'engine': 'kernel', 'interface': iface, 'sit_interface': sit_iface,\n"
    "            'endpoint': exit_v4, 'port': None, 'local_ip': local_ip,\n"
    "            'remote_ip': remote_addr, 'agent_id': agent_id, 'gre_key': key,\n"
    "            'mtu': SIT_IP6GRE_MTU, 'sit_mtu': SIT_MTU,\n"
    "            'public_local': iran_v4, 'public_remote': exit_v4,\n"
    "            'sit_local_v6': iran_sit_v6, 'sit_remote_v6': exit_sit_v6,\n"
    "            'underlay_family': 'ipv4-proto41', 'overlay_family': 'ipv6',\n"
    "            'service': f'tp27-link@{eid}.service',\n"
    "        }\n"
    "        core.setdefault('exits', [])\n"
    "        core['exits'] = [x for x in core['exits'] if x.get('id') != eid] + [rec]\n"
    "        atomic_json(CORE_CONFIG, core)\n"
    "        applied = apply_plan(eid, port_plan) if port_plan else []\n"
    "        job_set(\n"
    "            job_id, state='done',\n"
    "            message=f'{eid} SIT/6in4 → IP6GRE ONLINE · {local_addr} ↔ {remote_addr}',\n"
    "            result={**rec, 'mappings': applied, 'remote': {**remote, 'ping': ping_text},\n"
    "                    'speed_verification_required': True},\n"
    "        )\n"
    "        return True\n"
    "    except Exception as exc:\n"
    "        job_set(job_id, message=f'SIT/6in4 سالم نشد: {exc} · ادامه با Coreهای IPv4')\n"
    "        await _cleanup_sit(agent_id, eid, sit_iface)\n"
    "        return False\n\n\n"
    "async def provision_gre_via_agent(job_id, agent_id, requested_id=None, mtu=1400, mappings=None):\n",
)
replace_once(
    'panel/provision_ipv6.py',
    "        if await try_ipv6_fast_path(job_id, agent_id, requested_id, mtu, mappings):\n            return\n",
    "        if await try_ipv6_fast_path(job_id, agent_id, requested_id, mtu, mappings):\n"
    "            return\n"
    "        if await try_sit_fast_path(job_id, agent_id, requested_id, mtu, mappings):\n"
    "            return\n",
)

# ---------------------------------------------------------------------------
# agent/tp27-exit-agent.py
# ---------------------------------------------------------------------------
replace_once(
    'agent/tp27-exit-agent.py',
    "VERSION = '1.8.0'\n",
    "VERSION = '1.9.0'\n",
)
replace_once(
    'agent/tp27-exit-agent.py',
    "def _iface(eid):\n    return ('t27' + re.sub(r'[^a-z0-9]', '', eid.lower()))[:15]\n",
    "def _iface(eid):\n"
    "    return ('t27' + re.sub(r'[^a-z0-9]', '', eid.lower()))[:15]\n\n\n"
    "def _sit_iface(eid):\n"
    "    return ('s27' + re.sub(r'[^a-z0-9]', '', eid.lower()))[:15]\n\n\n"
    "def _ula_ipv6(value, name):\n"
    "    addr = ipaddress.ip_address(str(value or '').strip().split('%', 1)[0])\n"
    "    if addr.version != 6 or addr not in ipaddress.ip_network('fd00::/8'):\n"
    "        raise ValueError(f'{name} must be ULA IPv6')\n"
    "    return str(addr)\n",
)
replace_once(
    'agent/tp27-exit-agent.py',
    "    run(['ip', 'link', 'del', _iface(eid)], timeout=10, check=False)\n",
    "    run(['ip', 'link', 'del', _iface(eid)], timeout=10, check=False)\n"
    "    run(['ip', 'link', 'del', _sit_iface(eid)], timeout=10, check=False)\n",
)
replace_once(
    'agent/tp27-exit-agent.py',
    "    if (args or {}).get('ipv6_preflight_v1'):\n",
    "    if (args or {}).get('sit6in4_preflight_v1'):\n"
    "        modprobe = shutil_which('modprobe')\n"
    "        sit_supported = Path('/sys/module/sit').exists()\n"
    "        ip6gre_supported = Path('/sys/module/ip6_gre').exists()\n"
    "        if modprobe and not sit_supported:\n"
    "            src, _, _ = run([modprobe, '-n', 'sit'], timeout=5, check=False)\n"
    "            sit_supported = src == 0\n"
    "        if modprobe and not ip6gre_supported:\n"
    "            grc, _, _ = run([modprobe, '-n', 'ip6_gre'], timeout=5, check=False)\n"
    "            ip6gre_supported = grc == 0\n"
    "        result['sit6in4_preflight_v1'] = {\n"
    "            'api': 1, 'public_ipv4': public_ip(),\n"
    "            'sit_supported': bool(sit_supported),\n"
    "            'ip6gre_supported': bool(ip6gre_supported),\n"
    "            'protocol41_test': 'not_run',\n"
    "        }\n"
    "    if (args or {}).get('ipv6_preflight_v1'):\n",
)
replace_once(
    'agent/tp27-exit-agent.py',
    "def _action_provision_ipv6_exit(args, transport):\n",
    "def _action_provision_sit_ip6gre_exit(args):\n"
    "    eid = safe_id(str(args.get('id', '')))\n"
    "    ensure_runtime()\n"
    "    iran_public = _ipv4(args.get('iran_public'), 'iran_public')\n"
    "    exit_public = _ipv4(args.get('exit_public') or public_ip(), 'exit_public')\n"
    "    sit_local_v6 = _ula_ipv6(args.get('sit_local_v6'), 'sit_local_v6')\n"
    "    sit_remote_v6 = _ula_ipv6(args.get('sit_remote_v6'), 'sit_remote_v6')\n"
    "    if ipaddress.ip_network(sit_local_v6 + '/127', strict=False) != ipaddress.ip_network(sit_remote_v6 + '/127', strict=False):\n"
    "        raise ValueError('SIT IPv6 endpoints must share /127')\n"
    "    tunnel_ip = str(args.get('tunnel_ip', '')).strip()\n"
    "    peer_ip = _ipv4(args.get('peer_ip'), 'peer_ip')\n"
    "    iface_addr = ipaddress.ip_interface(tunnel_ip)\n"
    "    if iface_addr.version != 4:\n"
    "        raise ValueError('tunnel_ip must be IPv4')\n"
    "    mtu = int(args.get('mtu', 1400))\n"
    "    sit_mtu = int(args.get('sit_mtu', 1480))\n"
    "    if not 1000 <= mtu <= 1432:\n"
    "        raise ValueError('SIT/IP6GRE MTU must be between 1000 and 1432')\n"
    "    if not 1280 <= sit_mtu <= 1480:\n"
    "        raise ValueError('SIT MTU must be between 1280 and 1480')\n"
    "    gre_key = int(args.get('gre_key', 0))\n"
    "    if not 1 <= gre_key <= 4294967295:\n"
    "        raise ValueError('invalid GRE key')\n"
    "    EXITS.mkdir(parents=True, exist_ok=True)\n"
    "    _stop_exit_services(eid)\n"
    "    run(['ip', 'link', 'del', _sit_iface(eid)], timeout=10, check=False)\n"
    "    cfg = {\n"
    "        'role': 'sit-ip6gre-exit', 'transport': 'sit-ip6gre', 'mode': 'sit-ip6gre',\n"
    "        'id': eid, 'interface': _iface(eid), 'sit_interface': _sit_iface(eid),\n"
    "        'tunnel_ip': tunnel_ip, 'peer_ip': peer_ip,\n"
    "        'local_public': exit_public, 'remote_public': iran_public,\n"
    "        'sit_local_v6': sit_local_v6, 'sit_remote_v6': sit_remote_v6,\n"
    "        'gre_key': gre_key, 'mtu': mtu, 'sit_mtu': sit_mtu,\n"
    "        'txqueuelen': int(args.get('txqueuelen', 2000)),\n"
    "        'keepalive_interval': 4, 'keepalive_failures': 4,\n"
    "    }\n"
    "    atomic_json(EXITS / (eid + '.json'), cfg)\n"
    "    run(['systemctl', 'enable', 'tp27-link@' + eid + '.service'], timeout=20, check=False)\n"
    "    rc, out, err = run(['systemctl', 'restart', 'tp27-link@' + eid + '.service'], timeout=40, check=False)\n"
    "    if rc != 0:\n"
    "        raise RuntimeError(err or out or 'SIT/6in4 exit link start failed')\n"
    "    time.sleep(1.2)\n"
    "    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'tp27-link@' + eid + '.service']).returncode == 0\n"
    "    ping_rc, ping_out, ping_err = run(['ping', '-I', _iface(eid), '-c', '2', '-W', '2', peer_ip], timeout=8, check=False)\n"
    "    _, addr_out, _ = run(['ip', '-br', '-4', 'addr', 'show', 'dev', _iface(eid)], timeout=10, check=False)\n"
    "    _, sit_out, _ = run(['ip', '-d', 'link', 'show', 'dev', _sit_iface(eid)], timeout=10, check=False)\n"
    "    _, link_out, _ = run(['ip', '-s', 'link', 'show', 'dev', _iface(eid)], timeout=10, check=False)\n"
    "    return {\n"
    "        'id': eid, 'mode': 'sit-ip6gre', 'transport': 'sit-ip6gre',\n"
    "        'service_active': active, 'health_ping': ping_rc == 0,\n"
    "        'ping_output': ping_out or ping_err, 'interface': _iface(eid),\n"
    "        'sit_interface': _sit_iface(eid), 'interface_state': addr_out,\n"
    "        'sit_state': sit_out, 'link_state': link_out, 'tunnel_ip': tunnel_ip,\n"
    "        'peer_ip': peer_ip, 'gre_key': gre_key, 'public_ipv4': exit_public,\n"
    "        'sit_local_v6': sit_local_v6, 'sit_remote_v6': sit_remote_v6,\n"
    "        'protocol41_test': 'runtime-health', 'agent_version': VERSION,\n"
    "    }\n\n\n"
    "def _action_provision_ipv6_exit(args, transport):\n",
)
replace_once(
    'agent/tp27-exit-agent.py',
    "    if transport in {'ip6gre', 'wireguard-v6'}:\n        return _action_provision_ipv6_exit(args, transport)\n\n    eid = safe_id(str(args.get('id', '')))\n",
    "    if transport == 'sit-ip6gre':\n"
    "        return _action_provision_sit_ip6gre_exit(args)\n"
    "    if transport in {'ip6gre', 'wireguard-v6'}:\n"
    "        return _action_provision_ipv6_exit(args, transport)\n\n"
    "    eid = safe_id(str(args.get('id', '')))\n",
)
# The same dispatch anchor appears twice (GRE action then TLS action). Patch the
# second occurrence explicitly after the first replacement changed one copy.
replace_once(
    'agent/tp27-exit-agent.py',
    "def action_provision_tls_exit(args):\n    transport = str(args.get('transport') or 'framed-tls').strip().lower()\n    if transport in {'ip6gre', 'wireguard-v6'}:\n        return _action_provision_ipv6_exit(args, transport)\n",
    "def action_provision_tls_exit(args):\n"
    "    transport = str(args.get('transport') or 'framed-tls').strip().lower()\n"
    "    if transport == 'sit-ip6gre':\n"
    "        return _action_provision_sit_ip6gre_exit(args)\n"
    "    if transport in {'ip6gre', 'wireguard-v6'}:\n"
    "        return _action_provision_ipv6_exit(args, transport)\n",
)

# Plain agent should advertise the new capability generation after upgrade.
replace_once(
    'agent/tp27-exit-agent-plain.py',
    "SPEED_AGENT_VERSION = '1.7.0'\n",
    "SPEED_AGENT_VERSION = '1.9.0'\n",
)

# ---------------------------------------------------------------------------
# bin/tp27-link-mtls
# ---------------------------------------------------------------------------
replace_once(
    'bin/tp27-link-mtls',
    'WG_REMOTE_PORT="0"\nSOCAT_PID=""\n',
    'WG_REMOTE_PORT="0"\nSIT_IFACE=""\nSIT_LOCAL_PUBLIC=""\nSIT_REMOTE_PUBLIC=""\nSIT_LOCAL_V6=""\nSIT_REMOTE_V6=""\nSIT_MTU="1480"\nSOCAT_PID=""\n',
)
replace_once(
    'bin/tp27-link-mtls',
    "cleanup_gre_underlay_firewall() {\n",
    "setup_sit_underlay_firewall() {\n"
    "  [[ -n \"$IPT\" && -n \"$SIT_LOCAL_PUBLIC\" && -n \"$SIT_REMOTE_PUBLIC\" ]] || return 0\n"
    "  ensure_chain_hook filter INPUT TP27_SIT_IN\n"
    "  ensure_chain_hook filter OUTPUT TP27_SIT_OUT\n"
    "  ensure_rule filter TP27_SIT_IN -p 41 -s \"$SIT_REMOTE_PUBLIC/32\" -d \"$SIT_LOCAL_PUBLIC/32\" -j ACCEPT\n"
    "  ensure_rule filter TP27_SIT_OUT -p 41 -s \"$SIT_LOCAL_PUBLIC/32\" -d \"$SIT_REMOTE_PUBLIC/32\" -j ACCEPT\n"
    "}\n\n"
    "cleanup_sit_underlay_firewall() {\n"
    "  [[ -n \"$IPT\" && -n \"$SIT_LOCAL_PUBLIC\" && -n \"$SIT_REMOTE_PUBLIC\" ]] || return 0\n"
    "  remove_rule filter TP27_SIT_IN -p 41 -s \"$SIT_REMOTE_PUBLIC/32\" -d \"$SIT_LOCAL_PUBLIC/32\" -j ACCEPT\n"
    "  remove_rule filter TP27_SIT_OUT -p 41 -s \"$SIT_LOCAL_PUBLIC/32\" -d \"$SIT_REMOTE_PUBLIC/32\" -j ACCEPT\n"
    "}\n\n"
    "cleanup_gre_underlay_firewall() {\n",
)
replace_once(
    'bin/tp27-link-mtls',
    "  cleanup_ipv6_underlay_firewall || true\n  cleanup_gre_underlay_firewall || true\n",
    "  cleanup_ipv6_underlay_firewall || true\n"
    "  cleanup_sit_underlay_firewall || true\n"
    "  cleanup_gre_underlay_firewall || true\n",
)
replace_once(
    'bin/tp27-link-mtls',
    "  ip link del \"$IFACE\" 2>/dev/null || true\n  cleanup_fou || true\n",
    "  ip link del \"$IFACE\" 2>/dev/null || true\n"
    "  [[ -n \"${SIT_IFACE:-}\" ]] && ip link del \"$SIT_IFACE\" 2>/dev/null || true\n"
    "  cleanup_fou || true\n",
)
replace_once(
    'bin/tp27-link-mtls',
    "if [[ \"$ROLE\" == \"ip6gre-iran\" || \"$ROLE\" == \"ip6gre-exit\" ]]; then\n",
    "if [[ \"$ROLE\" == \"sit-ip6gre-iran\" || \"$ROLE\" == \"sit-ip6gre-exit\" ]]; then\n"
    "  SIT_IFACE=\"$(jq -r '.sit_interface' \"$CFG\")\"\n"
    "  SIT_LOCAL_PUBLIC=\"$(jq -r '.local_public' \"$CFG\")\"\n"
    "  SIT_REMOTE_PUBLIC=\"$(jq -r '.remote_public' \"$CFG\")\"\n"
    "  SIT_LOCAL_V6=\"$(jq -r '.sit_local_v6' \"$CFG\")\"\n"
    "  SIT_REMOTE_V6=\"$(jq -r '.sit_remote_v6' \"$CFG\")\"\n"
    "  SIT_MTU=\"$(jq -r '.sit_mtu // 1480' \"$CFG\")\"\n"
    "  GRE_KEY=\"$(jq -r '.gre_key' \"$CFG\")\"\n"
    "  TXQLEN=\"$(jq -r '.txqueuelen // 2000' \"$CFG\")\"\n"
    "  KEEPALIVE_INTERVAL=\"$(jq -r '.keepalive_interval // 4' \"$CFG\")\"\n"
    "  KEEPALIVE_FAILURES=\"$(jq -r '.keepalive_failures // 4' \"$CFG\")\"\n"
    "  V6_LOCAL_PUBLIC=\"$SIT_LOCAL_V6\"\n"
    "  V6_REMOTE_PUBLIC=\"$SIT_REMOTE_V6\"\n\n"
    "  python3 - \"$SIT_LOCAL_PUBLIC\" \"$SIT_REMOTE_PUBLIC\" \"$SIT_LOCAL_V6\" \"$SIT_REMOTE_V6\" \"$GRE_KEY\" \"$SIT_MTU\" \"$MTU\" <<'PY'\n"
    "import ipaddress, sys\n"
    "l4=ipaddress.ip_address(sys.argv[1]); r4=ipaddress.ip_address(sys.argv[2])\n"
    "l6=ipaddress.ip_address(sys.argv[3]); r6=ipaddress.ip_address(sys.argv[4])\n"
    "if l4.version != 4 or r4.version != 4: raise SystemExit('SIT requires IPv4 endpoints')\n"
    "ula=ipaddress.ip_network('fd00::/8')\n"
    "if l6.version != 6 or r6.version != 6 or l6 not in ula or r6 not in ula: raise SystemExit('SIT requires ULA IPv6 endpoints')\n"
    "if ipaddress.ip_network(str(l6) + '/127', strict=False) != ipaddress.ip_network(str(r6) + '/127', strict=False): raise SystemExit('SIT endpoints must share /127')\n"
    "key=int(sys.argv[5]); sit_mtu=int(sys.argv[6]); mtu=int(sys.argv[7])\n"
    "if not 1 <= key <= 4294967295: raise SystemExit('invalid GRE key')\n"
    "if not 1280 <= sit_mtu <= 1480: raise SystemExit('invalid SIT MTU')\n"
    "if not 1000 <= mtu <= 1432: raise SystemExit('invalid SIT/IP6GRE MTU')\n"
    "PY\n\n"
    "  command -v modprobe >/dev/null 2>&1 && modprobe sit 2>/dev/null || true\n"
    "  command -v modprobe >/dev/null 2>&1 && modprobe ip6_gre 2>/dev/null || true\n"
    "  ip link del \"$SIT_IFACE\" 2>/dev/null || true\n"
    "  setup_sit_underlay_firewall\n"
    "  ip tunnel add \"$SIT_IFACE\" mode sit local \"$SIT_LOCAL_PUBLIC\" remote \"$SIT_REMOTE_PUBLIC\" ttl 64\n"
    "  ip link set dev \"$SIT_IFACE\" mtu \"$SIT_MTU\" txqueuelen \"$TXQLEN\"\n"
    "  ip -6 addr add \"${SIT_LOCAL_V6}/127\" dev \"$SIT_IFACE\"\n"
    "  ip link set dev \"$SIT_IFACE\" up\n"
    "  ip -6 route replace \"${SIT_REMOTE_V6}/128\" dev \"$SIT_IFACE\" src \"$SIT_LOCAL_V6\"\n"
    "  setup_ipv6_underlay_firewall\n"
    "  ip link add \"$IFACE\" type ip6gre local \"$SIT_LOCAL_V6\" remote \"$SIT_REMOTE_V6\" key \"$GRE_KEY\" hoplimit 64\n"
    "  ip link set dev \"$IFACE\" mtu \"$MTU\" txqueuelen \"$TXQLEN\"\n"
    "  ip addr add \"$ADDR\" dev \"$IFACE\"\n"
    "  ip link set dev \"$IFACE\" up\n"
    "  setup_common_interface\n\n"
    "  echo \"TP27_SIT_IP6GRE_READY role=$ROLE iface=$IFACE sit=$SIT_IFACE outer=$SIT_LOCAL_PUBLIC->$SIT_REMOTE_PUBLIC v6=$SIT_LOCAL_V6->$SIT_REMOTE_V6 addr=$ADDR peer=$PEER_IP mtu=$MTU sit_mtu=$SIT_MTU key=$GRE_KEY\" >&2\n"
    "  ip -br -6 addr show dev \"$SIT_IFACE\" >&2 || true\n"
    "  ip -6 route show dev \"$SIT_IFACE\" >&2 || true\n"
    "  ip -br -4 addr show dev \"$IFACE\" >&2 || true\n"
    "  run_kernel_keepalive SITIP6GRE \"$KEEPALIVE_INTERVAL\" \"$KEEPALIVE_FAILURES\"\n"
    "fi\n\n"
    "if [[ \"$ROLE\" == \"ip6gre-iran\" || \"$ROLE\" == \"ip6gre-exit\" ]]; then\n",
)

# ---------------------------------------------------------------------------
# tests + docs
# ---------------------------------------------------------------------------
(ROOT / 'tests/test_sit6in4_fast_bootstrap_contract.py').write_text(r'''from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_builder_tries_native_ipv6_then_sit_then_existing_fallback():
    text = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    wrapper = text[text.index('async def provision_gre_via_agent'):]
    assert wrapper.index('try_ipv6_fast_path') < wrapper.index('try_sit_fast_path')
    assert wrapper.index('try_sit_fast_path') < wrapper.index('fallback.provision_gre_via_agent')
    assert "'underlay_family': 'ipv4-proto41'" in text
    assert "'speed_verification_required': True" in text


def test_sit_addresses_are_ula_and_deterministic():
    text = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    assert "f'fd27:{digest[:4]}:{digest[4:8]}:{digest[8:12]}::/127'" in text
    assert "hashlib.sha256(f'sit6in4|{eid}|{iran_v4}|{exit_v4}'" in text


def test_agent_exposes_capability_and_scoped_sit_transport():
    agent = (ROOT / 'agent/tp27-exit-agent.py').read_text(encoding='utf-8')
    assert "sit6in4_preflight_v1" in agent
    assert "protocol41_test': 'not_run'" in agent
    assert "transport == 'sit-ip6gre'" in agent
    assert "'role': 'sit-ip6gre-exit'" in agent
    assert "must be ULA IPv6" in agent


def test_runtime_uses_protocol41_and_nested_ip6gre_with_cleanup():
    runtime = (ROOT / 'bin/tp27-link-mtls').read_text(encoding='utf-8')
    assert 'TP27_SIT_IP6GRE_READY' in runtime
    assert 'mode sit local "$SIT_LOCAL_PUBLIC" remote "$SIT_REMOTE_PUBLIC"' in runtime
    assert 'type ip6gre local "$SIT_LOCAL_V6" remote "$SIT_REMOTE_V6"' in runtime
    assert 'TP27_SIT_IN -p 41' in runtime
    assert 'cleanup_sit_underlay_firewall' in runtime
    assert 'ip link del "$SIT_IFACE"' in runtime


def test_plain_agent_generation_bumped_for_capability_refresh():
    plain = (ROOT / 'agent/tp27-exit-agent-plain.py').read_text(encoding='utf-8')
    assert "SPEED_AGENT_VERSION = '1.9.0'" in plain
''', encoding='utf-8')

append_once(
    'docs/IPV6_FAST_CORE.md',
    '## SIT / 6in4 bootstrap',
    '''## SIT / 6in4 bootstrap

When the Iran node has no native IPv6, TP27 may test an IPv6 bootstrap over IPv4 protocol 41:

1. Create a point-to-point SIT/6in4 interface between the two public IPv4 endpoints.
2. Assign deterministic TP27 ULA `/127` addresses only to that private bootstrap link.
3. Run IP6GRE over the SIT IPv6 pair while preserving the existing TP27 IPv4 `/30` payload network.
4. Accept the candidate only after bidirectional TP27 peer health succeeds.
5. On any failure, remove the service/interface/firewall state and continue with the existing IPv4 transport builder.

The outer firewall exception is scoped to IP protocol 41 and the exact Iran/Exit IPv4 pair. No existing tunnel is migrated. SIT/IP6GRE is health-selected, not assumed to be faster than direct GRE; throughput should be verified on the real pair before treating it as a preferred production carrier.''',
)

print('SIT6IN4_PATCH=OK')
