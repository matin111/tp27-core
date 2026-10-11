import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path('/etc/tp27')
CORE_CONFIG = ROOT / 'config.json'
PORT_CONFIG = ROOT / 'port-maps.json'


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def default_state():
    return {'version': 1, 'mappings': [], 'active_l2tp_exit': None}


def state():
    cfg = load_json(PORT_CONFIG, default_state())
    cfg.setdefault('version', 1)
    cfg.setdefault('mappings', [])
    cfg.setdefault('active_l2tp_exit', None)
    return cfg


def core():
    return load_json(CORE_CONFIG, {'exits': []})


def get_exit(exit_id):
    for item in core().get('exits', []):
        if item.get('id') == exit_id:
            return item
    raise ValueError(f'Exit not found: {exit_id}')


def exit_number(exit_id):
    m = re.search(r'(\d+)$', exit_id or '')
    if m:
        n = int(m.group(1))
        if 1 <= n <= 999:
            return n
    exits = [x.get('id') for x in core().get('exits', [])]
    try:
        return exits.index(exit_id) + 1
    except ValueError:
        raise ValueError('Exit not found')


def profile(exit_id, cisco_port=8897, openvpn_port=9988, wireguard_port=55821):
    get_exit(exit_id)
    n = exit_number(exit_id)
    return [
        {'id': f'{exit_id}-cisco-tcp', 'exit_id': exit_id, 'service': 'cisco',
         'proto': 'tcp', 'listen_port': 11000 + n, 'target_port': int(cisco_port), 'enabled': True},
        {'id': f'{exit_id}-cisco-udp', 'exit_id': exit_id, 'service': 'cisco',
         'proto': 'udp', 'listen_port': 11000 + n, 'target_port': int(cisco_port), 'enabled': True},
        {'id': f'{exit_id}-openvpn-tcp', 'exit_id': exit_id, 'service': 'openvpn',
         'proto': 'tcp', 'listen_port': 12000 + n, 'target_port': int(openvpn_port), 'enabled': True},
        {'id': f'{exit_id}-wireguard-udp', 'exit_id': exit_id, 'service': 'wireguard',
         'proto': 'udp', 'listen_port': 13000 + n, 'target_port': int(wireguard_port), 'enabled': True},
    ]


def validate_mapping(item, existing=None):
    existing = existing or []
    if item.get('proto') not in {'tcp', 'udp'}:
        raise ValueError('proto must be tcp or udp')
    for key in ('listen_port', 'target_port'):
        port = int(item[key])
        if not 1 <= port <= 65535:
            raise ValueError(f'invalid {key}')
    get_exit(item['exit_id'])
    for old in existing:
        if not old.get('enabled', True):
            continue
        if old.get('id') == item.get('id'):
            continue
        if old.get('proto') == item.get('proto') and int(old.get('listen_port', 0)) == int(item['listen_port']):
            raise ValueError(f"port collision: {item['proto']}/{item['listen_port']}")


def apply_profile(exit_id, cisco_port=8897, openvpn_port=9988, wireguard_port=55821):
    cfg = state()
    wanted = profile(exit_id, cisco_port, openvpn_port, wireguard_port)
    kept = [m for m in cfg['mappings'] if m.get('exit_id') != exit_id or m.get('service') not in {'cisco','openvpn','wireguard'}]
    for item in wanted:
        validate_mapping(item, kept + wanted)
    cfg['mappings'] = kept + wanted
    atomic_json(PORT_CONFIG, cfg)
    reconcile()
    return wanted


def add_custom(exit_id, service, proto, listen_port, target_port):
    cfg = state()
    safe = re.sub(r'[^a-z0-9_-]', '-', service.lower())[:24] or 'custom'
    item = {'id': f'{exit_id}-{safe}-{proto}-{listen_port}', 'exit_id': exit_id,
            'service': safe, 'proto': proto, 'listen_port': int(listen_port),
            'target_port': int(target_port), 'enabled': True}
    validate_mapping(item, cfg['mappings'])
    cfg['mappings'] = [m for m in cfg['mappings'] if m.get('id') != item['id']] + [item]
    atomic_json(PORT_CONFIG, cfg)
    reconcile()
    return item


def set_l2tp(exit_id):
    if exit_id:
        get_exit(exit_id)
    cfg = state()
    cfg['active_l2tp_exit'] = exit_id or None
    atomic_json(PORT_CONFIG, cfg)
    reconcile()
    return cfg['active_l2tp_exit']


def ensure_chain(table, chain):
    rc, _, _ = run(['iptables', '-t', table, '-nL', chain], check=False)
    if rc != 0:
        run(['iptables', '-t', table, '-N', chain])


def ensure_hook(table, parent, chain):
    rc, _, _ = run(['iptables', '-t', table, '-C', parent, '-j', chain], check=False)
    if rc != 0:
        run(['iptables', '-t', table, '-I', parent, '1', '-j', chain])


def reconcile():
    cfg = state()
    c = core()
    exits = {e.get('id'): e for e in c.get('exits', [])}

    ensure_chain('nat', 'TP27_PRE')
    ensure_chain('nat', 'TP27_POST')
    ensure_chain('filter', 'TP27_FWD')
    ensure_hook('nat', 'PREROUTING', 'TP27_PRE')
    ensure_hook('nat', 'POSTROUTING', 'TP27_POST')
    ensure_hook('filter', 'FORWARD', 'TP27_FWD')

    run(['iptables', '-t', 'nat', '-F', 'TP27_PRE'])
    run(['iptables', '-t', 'nat', '-F', 'TP27_POST'])
    run(['iptables', '-t', 'filter', '-F', 'TP27_FWD'])

    for m in cfg.get('mappings', []):
        if not m.get('enabled', True):
            continue
        e = exits.get(m.get('exit_id'))
        if not e:
            continue
        proto = m['proto']
        listen_port = str(m['listen_port'])
        target_port = str(m['target_port'])
        remote_ip = str(e['remote_ip'])
        local_ip = str(e['local_ip']).split('/')[0]
        iface = str(e['interface'])
        run(['iptables','-t','nat','-A','TP27_PRE','-p',proto,'--dport',listen_port,
             '-j','DNAT','--to-destination',f'{remote_ip}:{target_port}'])
        run(['iptables','-t','nat','-A','TP27_POST','-o',iface,'-p',proto,'-d',remote_ip,
             '--dport',target_port,'-j','SNAT','--to-source',local_ip])
        run(['iptables','-t','filter','-A','TP27_FWD','-o',iface,'-p',proto,'-d',remote_ip,
             '--dport',target_port,'-j','ACCEPT'])
        run(['iptables','-t','filter','-A','TP27_FWD','-i',iface,'-m','conntrack','--ctstate',
             'ESTABLISHED,RELATED','-j','ACCEPT'])

    l2tp = cfg.get('active_l2tp_exit')
    if l2tp and l2tp in exits:
        e = exits[l2tp]
        remote_ip = str(e['remote_ip'])
        local_ip = str(e['local_ip']).split('/')[0]
        iface = str(e['interface'])
        for port in ('500','4500','1701'):
            run(['iptables','-t','nat','-A','TP27_PRE','-p','udp','--dport',port,
                 '-j','DNAT','--to-destination',f'{remote_ip}:{port}'])
            run(['iptables','-t','nat','-A','TP27_POST','-o',iface,'-p','udp','-d',remote_ip,
                 '--dport',port,'-j','SNAT','--to-source',local_ip])
            run(['iptables','-t','filter','-A','TP27_FWD','-o',iface,'-p','udp','-d',remote_ip,
                 '--dport',port,'-j','ACCEPT'])

    return cfg
