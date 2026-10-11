#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/etc/tp27')
CONFIG = ROOT / 'config.json'
EXITS = ROOT / 'exits'
CERTS = ROOT / 'certs'
RAW = os.environ.get('TP27_REPO_RAW', 'https://raw.githubusercontent.com/matin111/tp27-core/main')

def run(cmd, check=True, capture=True):
    p = subprocess.run(cmd, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return (p.stdout or '').strip()

def root():
    if os.geteuid() != 0:
        raise SystemExit('ERROR: root required')

def load():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}

def save(c):
    ROOT.mkdir(parents=True, exist_ok=True)
    t = CONFIG.with_suffix('.tmp')
    t.write_text(json.dumps(c, indent=2) + '\n')
    os.chmod(t, 0o600)
    os.replace(t, CONFIG)

def public_ip():
    try:
        return run(['curl','-4','-fsS','--max-time','5','https://ifconfig.me'])
    except Exception:
        out = run(['ip','-4','route','get','1.1.1.1'])
        m = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', out)
        return m.group(1) if m else ''

def bootstrap(a):
    root()
    c = load()
    if c.get('version') != 2:
        c = {'version':2,'role':a.role,'node_id':socket.gethostname(),'public_ip':public_ip(),'exits':[]}
    else:
        c['role'] = a.role
        c.setdefault('public_ip', public_ip())
        c.setdefault('exits', [])
    save(c)
    print(f"TP27 v0.2 role={a.role} public_ip={c.get('public_ip')}")

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = path.with_suffix('.tmp')
    t.write_text(json.dumps(obj, indent=2) + '\n')
    os.chmod(t, 0o600)
    os.replace(t, path)

def valid_id(x):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', x):
        raise SystemExit('invalid id')
    return x

def iface_for(i):
    clean = re.sub(r'[^a-z0-9]', '', i.lower())
    return ('t27' + clean)[:15]

def exit_provision(a):
    root(); valid_id(a.id)
    c = load()
    if c.get('role') != 'exit':
        raise SystemExit('ERROR: install node as exit first')
    ipaddress.ip_interface(a.tunnel_ip)
    iface = iface_for(a.id)
    cert = CERTS / f'{a.id}.crt'
    key = CERTS / f'{a.id}.key'
    CERTS.mkdir(parents=True, exist_ok=True)
    if not cert.exists() or not key.exists():
        ip = c.get('public_ip') or public_ip()
        run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes',
             '-days','3650','-subj',f'/CN={ip}',
             '-addext',f'subjectAltName=IP:{ip}',
             '-keyout',str(key),'-out',str(cert)], capture=True)
        os.chmod(key, 0o600); os.chmod(cert, 0o644)
    cfg = {'role':'exit','id':a.id,'interface':iface,'tunnel_ip':a.tunnel_ip,
           'listen_port':a.listen_port,'mtu':a.mtu,'cert':str(cert),'key':str(key)}
    write_json(EXITS / f'{a.id}.json', cfg)
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now',f'tp27-link@{a.id}.service'])
    time.sleep(1)
    print(f'EXIT_ID={a.id}')
    print(f'INTERFACE={iface}')
    print(f'TUNNEL_IP={a.tunnel_ip}')
    print(f'LISTEN_PORT={a.listen_port}')
    print(f'CERT={cert}')

def next_values(c):
    used = {e['id'] for e in c.get('exits', [])}
    n = 1
    while f'exit{n:02d}' in used:
        n += 1
    base = ipaddress.ip_network('10.254.250.0/24')
    subnets = list(base.subnets(new_prefix=30))
    net = subnets[n-1]
    hosts = list(net.hosts())
    return f'exit{n:02d}', str(hosts[1]) + '/30', str(hosts[0]) + '/30', 44329 + n

def ssh(remote, command, capture=False):
    cmd = ['ssh','-o','StrictHostKeyChecking=accept-new',f'root@{remote}',command]
    return run(cmd, capture=True) if capture else run(cmd, capture=False)

def scp(remote, rpath, lpath):
    run(['scp','-q','-o','StrictHostKeyChecking=accept-new',f'root@{remote}:{rpath}',str(lpath)], capture=False)

def add_exit(a):
    root()
    c = load()
    if c.get('role') != 'iran':
        raise SystemExit('ERROR: run on Iran node')
    ipaddress.ip_address(a.endpoint)
    eid, local_ip, remote_ip, port = next_values(c)
    if a.id:
        eid = valid_id(a.id)
    if a.port:
        port = a.port
    iface = iface_for(eid)
    print(f'[1/6] installing TP27 exit on {a.endpoint}')
    ssh(a.endpoint, f'curl -fsSL {RAW}/install.sh | bash -s -- exit')
    print('[2/6] provisioning remote L3 endpoint')
    ssh(a.endpoint, f'tp27 exit-provision --id {eid} --listen-port {port} --tunnel-ip {remote_ip} --mtu {a.mtu}')
    print('[3/6] fetching pinned TLS certificate')
    local_cert = CERTS / f'{eid}.crt'
    CERTS.mkdir(parents=True, exist_ok=True)
    scp(a.endpoint, f'/etc/tp27/certs/{eid}.crt', local_cert)
    os.chmod(local_cert, 0o644)
    cfg = {'role':'iran','id':eid,'interface':iface,'tunnel_ip':local_ip,
           'remote_ip':str(ipaddress.ip_interface(remote_ip).ip),
           'endpoint':a.endpoint,'listen_port':port,'mtu':a.mtu,'ca_file':str(local_cert)}
    write_json(EXITS / f'{eid}.json', cfg)
    rec = {'id':eid,'interface':iface,'endpoint':a.endpoint,'port':port,
           'local_ip':local_ip,'remote_ip':str(ipaddress.ip_interface(remote_ip).ip)}
    c.setdefault('exits', [])
    c['exits'] = [x for x in c['exits'] if x.get('id') != eid] + [rec]
    save(c)
    print('[4/6] starting local tunnel')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now',f'tp27-link@{eid}.service'])
    print('[5/6] waiting for data plane')
    ok = False
    for _ in range(15):
        time.sleep(1)
        rc = subprocess.run(['ping','-c','1','-W','1',rec['remote_ip']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc == 0:
            ok = True
            break
    print('[6/6] health check')
    if not ok:
        print('DEGRADED: tunnel service is running but peer ping failed')
        print(f'Check: journalctl -u tp27-link@{eid}.service -n 100 --no-pager')
        raise SystemExit(3)
    print(f"ONLINE: {eid} {local_ip} <-> {rec['remote_ip']} via TLS/TCP {a.endpoint}:{port}")

def status(a):
    c = load()
    print(f"TP27 v{c.get('version','?')} role={c.get('role','unconfigured')} public_ip={c.get('public_ip','')}")
    if c.get('role') == 'iran':
        for e in c.get('exits', []):
            svc = f"tp27-link@{e['id']}.service"
            active = subprocess.run(['systemctl','is-active','--quiet',svc]).returncode == 0
            peer = e['remote_ip']
            ping = subprocess.run(['ping','-c','1','-W','1',peer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            print(f"{e['id']:10} {'ONLINE' if active and ping else 'DEGRADED':9} iface={e['interface']} peer={peer} endpoint={e['endpoint']}:{e['port']}")
    else:
        for p in sorted(EXITS.glob('*.json')):
            e = json.loads(p.read_text())
            svc = f"tp27-link@{e['id']}.service"
            active = subprocess.run(['systemctl','is-active','--quiet',svc]).returncode == 0
            print(f"{e['id']:10} {'LISTENING' if active else 'DOWN':9} iface={e['interface']} port={e['listen_port']}")

def doctor(a):
    checks = [
      ('config', CONFIG.exists(), str(CONFIG)),
      ('socat', shutil.which('socat') is not None, shutil.which('socat') or ''),
      ('openssl', shutil.which('openssl') is not None, shutil.which('openssl') or ''),
      ('ip_forward', run(['sysctl','-n','net.ipv4.ip_forward'], check=False) == '1', ''),
    ]
    for n, ok, d in checks:
        print(f"{'OK' if ok else 'FAIL':4} {n:18} {d}")
    if not all(x[1] for x in checks):
        raise SystemExit(1)

def install_cmd(a):
    root()
    os.execvp('bash', ['bash','-c',f'curl -fsSL {RAW}/install.sh | bash -s -- {a.role}'])

def logs(a):
    os.execvp('journalctl', ['journalctl','-u',f'tp27-link@{a.id}.service','-n',str(a.lines),'--no-pager'])

def main():
    p = argparse.ArgumentParser(prog='tp27')
    s = p.add_subparsers(dest='cmd', required=True)
    x = s.add_parser('bootstrap'); x.add_argument('--role', choices=['iran','exit'], required=True); x.set_defaults(fn=bootstrap)
    x = s.add_parser('install'); x.add_argument('role', choices=['iran','exit']); x.set_defaults(fn=install_cmd)
    x = s.add_parser('exit-provision'); x.add_argument('--id', required=True); x.add_argument('--listen-port', type=int, required=True); x.add_argument('--tunnel-ip', required=True); x.add_argument('--mtu', type=int, default=1280); x.set_defaults(fn=exit_provision)
    x = s.add_parser('add-exit'); x.add_argument('endpoint'); x.add_argument('--id'); x.add_argument('--port', type=int); x.add_argument('--mtu', type=int, default=1280); x.set_defaults(fn=add_exit)
    x = s.add_parser('status'); x.set_defaults(fn=status)
    x = s.add_parser('doctor'); x.set_defaults(fn=doctor)
    x = s.add_parser('logs'); x.add_argument('id'); x.add_argument('--lines', type=int, default=100); x.set_defaults(fn=logs)
    a = p.parse_args()
    try:
        a.fn(a)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
