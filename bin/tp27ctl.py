#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path("/etc/tp27/config.json")
STATUS = Path("/var/lib/tp27/status.json")
KEYDIR = Path("/etc/tp27/keys")
WGDIR = Path("/etc/wireguard")

def run(cmd, check=True, capture=True, input_text=None):
    p = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{err}")
    return (p.stdout or "").strip()

def require_root():
    if os.geteuid() != 0:
        raise SystemExit("ERROR: root required")

def load():
    if not CONFIG.exists():
        return {}
    return json.loads(CONFIG.read_text(encoding="utf-8"))

def save(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)

def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)

def default_iface():
    out = run(["ip", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"\bdev\s+(\S+)", out)
    if not m:
        raise RuntimeError("could not detect default interface")
    return m.group(1)

def public_ipv4():
    try:
        return run(["curl", "-4", "-fsS", "--max-time", "5", "https://ifconfig.me"])
    except Exception:
        out = run(["ip", "-4", "addr", "show", "dev", default_iface()])
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", out)
        return m.group(1) if m else ""

def ensure_node_key():
    KEYDIR.mkdir(parents=True, exist_ok=True)
    priv = KEYDIR / "node.private"
    pub = KEYDIR / "node.public"
    if not priv.exists():
        key = run(["wg", "genkey"])
        atomic_write(priv, key + "\n", 0o600)
    if not pub.exists():
        key = run(["wg", "pubkey"], input_text=priv.read_text())
        atomic_write(pub, key + "\n", 0o644)
    return priv, pub

def gen_pair_key(exit_id):
    d = KEYDIR / "exits"
    d.mkdir(parents=True, exist_ok=True)
    priv = d / f"{exit_id}.private"
    pub = d / f"{exit_id}.public"
    if not priv.exists():
        key = run(["wg", "genkey"])
        atomic_write(priv, key + "\n", 0o600)
    if not pub.exists():
        key = run(["wg", "pubkey"], input_text=priv.read_text())
        atomic_write(pub, key + "\n", 0o644)
    return priv, pub

def iface_for(exit_id):
    clean = re.sub(r"[^a-zA-Z0-9]", "", exit_id.lower())
    if not clean:
        raise ValueError("invalid exit id")
    return ("tp27" + clean)[:15]

def ensure_chain(table, chain, hook=None):
    rc = subprocess.run(["iptables", "-t", table, "-nL", chain],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc != 0:
        run(["iptables", "-t", table, "-N", chain])
    if hook:
        rc = subprocess.run(["iptables", "-t", table, "-C", hook, "-j", chain],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc != 0:
            run(["iptables", "-t", table, "-I", hook, "1", "-j", chain])

def ensure_firewall_base():
    ensure_chain("nat", "TP27_PRE", "PREROUTING")
    ensure_chain("nat", "TP27_POST", "POSTROUTING")
    ensure_chain("filter", "TP27_FWD", "FORWARD")
    ensure_chain("mangle", "TP27_FWD", "FORWARD")

def bootstrap(args):
    require_root()
    if args.role not in ("iran", "exit"):
        raise SystemExit("role must be iran or exit")
    ensure_node_key()
    cfg = load()
    cfg.setdefault("version", 1)
    cfg["role"] = args.role
    cfg.setdefault("node_id", socket.gethostname() + "-" + secrets.token_hex(3))
    cfg.setdefault("public_iface", default_iface())
    cfg.setdefault("public_ip", public_ipv4())
    if args.role == "iran":
        cfg.setdefault("exits", [])
        ensure_firewall_base()
    else:
        cfg.setdefault("exit", {})
    save(cfg)
    print(json.dumps({
        "role": cfg["role"],
        "node_id": cfg["node_id"],
        "public_ip": cfg["public_ip"],
        "public_iface": cfg["public_iface"],
    }, indent=2))

def exit_init(args):
    require_root()
    cfg = load()
    if cfg.get("role") != "exit":
        raise SystemExit("ERROR: node role is not exit")
    iface = iface_for(args.id)
    ipaddress.ip_interface(args.tunnel_ip)
    priv, pub = ensure_node_key()

    conf = f"""[Interface]
Address = {args.tunnel_ip}
PrivateKey = {priv.read_text().strip()}
ListenPort = {args.listen_port}
MTU = {args.mtu}
"""
    atomic_write(WGDIR / f"{iface}.conf", conf, 0o600)
    cfg["exit"] = {
        "id": args.id,
        "interface": iface,
        "listen_port": args.listen_port,
        "tunnel_ip": args.tunnel_ip,
        "mtu": args.mtu,
    }
    save(cfg)
    run(["systemctl", "enable", "--now", f"wg-quick@{iface}.service"], capture=True)
    print(f"EXIT_ID={args.id}")
    print(f"INTERFACE={iface}")
    print(f"EXIT_PUBLIC_KEY={pub.read_text().strip()}")
    print(f"TUNNEL_IP={args.tunnel_ip}")
    print(f"LISTEN_PORT={args.listen_port}")

def exit_info(args):
    cfg = load()
    _, pub = ensure_node_key()
    e = cfg.get("exit", {})
    print(f"ROLE={cfg.get('role')}")
    print(f"PUBLIC_IP={cfg.get('public_ip')}")
    print(f"EXIT_ID={e.get('id','')}")
    print(f"INTERFACE={e.get('interface','')}")
    print(f"EXIT_PUBLIC_KEY={pub.read_text().strip()}")
    print(f"TUNNEL_IP={e.get('tunnel_ip','')}")
    print(f"LISTEN_PORT={e.get('listen_port','')}")

def exit_add_peer(args):
    require_root()
    cfg = load()
    if cfg.get("role") != "exit":
        raise SystemExit("ERROR: node role is not exit")
    e = cfg.get("exit") or {}
    iface = e.get("interface")
    if not iface:
        raise SystemExit("ERROR: run exit-init first")
    ipaddress.ip_network(args.peer_ip, strict=False)
    conf_path = WGDIR / f"{iface}.conf"
    text = conf_path.read_text(encoding="utf-8")
    marker = f"# TP27_PEER {args.peer_public_key}"
    if marker not in text:
        text += f"""
{marker}
[Peer]
PublicKey = {args.peer_public_key}
AllowedIPs = {args.peer_ip}
PersistentKeepalive = 15
"""
        atomic_write(conf_path, text, 0o600)
    run(["systemctl", "restart", f"wg-quick@{iface}.service"])
    print("PEER_ADDED")

def add_exit(args):
    require_root()
    cfg = load()
    if cfg.get("role") != "iran":
        raise SystemExit("ERROR: node role is not iran")
    if any(e["id"] == args.id for e in cfg.get("exits", [])):
        raise SystemExit(f"ERROR: exit {args.id} already exists")

    ipaddress.ip_interface(args.local_ip)
    ipaddress.ip_address(args.remote_ip)
    iface = iface_for(args.id)
    priv, pub = gen_pair_key(args.id)

    conf = f"""[Interface]
Address = {args.local_ip}
PrivateKey = {priv.read_text().strip()}
ListenPort = 0
MTU = {args.mtu}

[Peer]
PublicKey = {args.exit_public_key}
Endpoint = {args.endpoint}:{args.port}
AllowedIPs = {args.remote_ip}/32
PersistentKeepalive = 15
"""
    atomic_write(WGDIR / f"{iface}.conf", conf, 0o600)

    e = {
        "id": args.id,
        "interface": iface,
        "endpoint": args.endpoint,
        "port": args.port,
        "local_ip": args.local_ip,
        "remote_ip": args.remote_ip,
        "exit_public_key": args.exit_public_key,
        "priority": args.priority,
        "mtu": args.mtu,
        "forwards": [],
    }
    cfg.setdefault("exits", []).append(e)
    save(cfg)
    run(["systemctl", "enable", "--now", f"wg-quick@{iface}.service"])
    print(f"EXIT_ADDED={args.id}")
    print(f"INTERFACE={iface}")
    print(f"IRAN_PUBLIC_KEY={pub.read_text().strip()}")
    print(f"IRAN_TUNNEL_IP={ipaddress.ip_interface(args.local_ip).ip}/32")
    print(f"EXIT_TUNNEL_IP={args.remote_ip}")

def find_exit(cfg, eid):
    for e in cfg.get("exits", []):
        if e.get("id") == eid:
            return e
    raise SystemExit(f"ERROR: exit not found: {eid}")

def iptables_add(table, chain, spec):
    cmd = ["iptables", "-t", table, "-C", chain] + spec
    rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc != 0:
        run(["iptables", "-t", table, "-A", chain] + spec)

def forward_add(args):
    require_root()
    cfg = load()
    if cfg.get("role") != "iran":
        raise SystemExit("ERROR: forwarding is configured on Iran node")
    ensure_firewall_base()
    e = find_exit(cfg, args.exit)
    iface = e["interface"]
    remote = e["remote_ip"]
    local_src = str(ipaddress.ip_interface(e["local_ip"]).ip)
    pub_if = cfg["public_iface"]

    comment = f"TP27:{args.exit}:{args.proto}:{args.listen}->{args.target}"

    pre = ["-i", pub_if, "-p", args.proto, "--dport", str(args.listen),
           "-m", "comment", "--comment", comment,
           "-j", "DNAT", "--to-destination", f"{remote}:{args.target}"]
    post = ["-o", iface, "-d", remote, "-p", args.proto, "--dport", str(args.target),
            "-m", "comment", "--comment", comment,
            "-j", "SNAT", "--to-source", local_src]
    fwd1 = ["-i", pub_if, "-o", iface, "-d", remote, "-p", args.proto,
            "--dport", str(args.target),
            "-m", "conntrack", "--ctstate", "NEW,ESTABLISHED",
            "-m", "comment", "--comment", comment, "-j", "ACCEPT"]
    fwd2 = ["-i", iface, "-o", pub_if, "-s", remote, "-p", args.proto,
            "--sport", str(args.target),
            "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
            "-m", "comment", "--comment", comment, "-j", "ACCEPT"]

    iptables_add("nat", "TP27_PRE", pre)
    iptables_add("nat", "TP27_POST", post)
    iptables_add("filter", "TP27_FWD", fwd1)
    iptables_add("filter", "TP27_FWD", fwd2)

    rec = {
        "proto": args.proto,
        "listen": args.listen,
        "target": args.target,
        "comment": comment,
    }
    if rec not in e.setdefault("forwards", []):
        e["forwards"].append(rec)
    save(cfg)
    print("FORWARD_ADDED")

def flush_l2tp():
    for table, chain in [("nat", "TP27_PRE"), ("nat", "TP27_POST"), ("filter", "TP27_FWD")]:
        out = run(["iptables", "-t", table, "-S", chain], check=False)
        for line in out.splitlines():
            if "TP27:L2TP-ACTIVE" not in line:
                continue
            # Convert "-A CHAIN ..." to deletion arguments safely enough for our generated rules.
            import shlex
            parts = shlex.split(line)
            if len(parts) >= 3 and parts[0] == "-A":
                run(["iptables", "-t", table, "-D", parts[1]] + parts[2:], check=False)

def l2tp_active(args):
    require_root()
    cfg = load()
    if cfg.get("role") != "iran":
        raise SystemExit("ERROR: run on Iran node")
    ensure_firewall_base()
    e = find_exit(cfg, args.exit)
    iface = e["interface"]
    remote = e["remote_ip"]
    local_src = str(ipaddress.ip_interface(e["local_ip"]).ip)
    pub_if = cfg["public_iface"]

    flush_l2tp()
    for port in (500, 4500, 1701):
        comment = f"TP27:L2TP-ACTIVE:{args.exit}:{port}"
        iptables_add("nat", "TP27_PRE",
            ["-i", pub_if, "-p", "udp", "--dport", str(port),
             "-m", "comment", "--comment", comment,
             "-j", "DNAT", "--to-destination", f"{remote}:{port}"])
        iptables_add("nat", "TP27_POST",
            ["-o", iface, "-d", remote, "-p", "udp", "--dport", str(port),
             "-m", "comment", "--comment", comment,
             "-j", "SNAT", "--to-source", local_src])
        iptables_add("filter", "TP27_FWD",
            ["-i", pub_if, "-o", iface, "-d", remote, "-p", "udp", "--dport", str(port),
             "-m", "comment", "--comment", comment, "-j", "ACCEPT"])
        iptables_add("filter", "TP27_FWD",
            ["-i", iface, "-o", pub_if, "-s", remote, "-p", "udp", "--sport", str(port),
             "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
             "-m", "comment", "--comment", comment, "-j", "ACCEPT"])
    cfg["active_l2tp_exit"] = args.exit
    save(cfg)
    print(f"L2TP_ACTIVE_EXIT={args.exit}")

def status(args):
    cfg = load()
    print(f"TP27 role={cfg.get('role','unconfigured')} node={cfg.get('node_id','')}")
    print(f"public_ip={cfg.get('public_ip','')} iface={cfg.get('public_iface','')}")
    if cfg.get("role") == "iran":
        print(f"exits={len(cfg.get('exits', []))} active_l2tp={cfg.get('active_l2tp_exit','none')}")
        state = {}
        if STATUS.exists():
            try:
                state = json.loads(STATUS.read_text())
            except Exception:
                pass
        smap = {x.get("id"): x for x in state.get("tunnels", [])}
        for e in sorted(cfg.get("exits", []), key=lambda x: -int(x.get("priority", 100))):
            s = smap.get(e["id"], {})
            print(
                f"{e['id']:12} iface={e['interface']:12} "
                f"endpoint={e['endpoint']}:{e['port']} "
                f"remote={e['remote_ip']:15} "
                f"health={str(s.get('healthy','unknown')):7} "
                f"rtt={s.get('latency_ms','-')}ms "
                f"hs_age={s.get('handshake_age_s','-')}s"
            )
    elif cfg.get("role") == "exit":
        e = cfg.get("exit", {})
        print(f"exit_id={e.get('id','')} iface={e.get('interface','')} tunnel={e.get('tunnel_ip','')}")
        print(run(["wg", "show"], check=False))

def doctor(args):
    cfg = load()
    checks = []
    def add(name, ok, detail=""):
        checks.append((name, ok, detail))
    add("config", CONFIG.exists(), str(CONFIG))
    add("wg", shutil.which("wg") is not None, shutil.which("wg") or "")
    add("iptables", shutil.which("iptables") is not None, shutil.which("iptables") or "")
    try:
        fwd = run(["sysctl", "-n", "net.ipv4.ip_forward"])
        add("ip_forward", fwd == "1", fwd)
    except Exception as e:
        add("ip_forward", False, str(e))
    if cfg.get("role") == "iran":
        for e in cfg.get("exits", []):
            rc = subprocess.run(["ip", "link", "show", "dev", e["interface"]],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            add(f"iface:{e['id']}", rc == 0, e["interface"])
    elif cfg.get("role") == "exit":
        e = cfg.get("exit", {})
        if e.get("interface"):
            rc = subprocess.run(["ip", "link", "show", "dev", e["interface"]],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            add("exit_iface", rc == 0, e["interface"])
    for name, ok, detail in checks:
        print(f"{'OK' if ok else 'FAIL':4} {name:20} {detail}")
    if not all(x[1] for x in checks):
        raise SystemExit(1)

def repair(args):
    require_root()
    cfg = load()
    units = []
    if cfg.get("role") == "iran":
        units = [f"wg-quick@{e['interface']}.service" for e in cfg.get("exits", [])]
        ensure_firewall_base()
    elif cfg.get("role") == "exit":
        e = cfg.get("exit", {})
        if e.get("interface"):
            units = [f"wg-quick@{e['interface']}.service"]
    for u in units:
        run(["systemctl", "restart", u], check=False)
    run(["systemctl", "restart", "tp27-agent.service"], check=False)
    print("REPAIR_COMPLETE")

def logs(args):
    os.execvp("journalctl", ["journalctl", "-u", "tp27-agent.service", "-n", str(args.lines), "--no-pager"])

def build_parser():
    p = argparse.ArgumentParser(prog="tp27ctl")
    sub = p.add_subparsers(dest="cmd", required=True)

    x = sub.add_parser("bootstrap")
    x.add_argument("--role", required=True)
    x.set_defaults(func=bootstrap)

    x = sub.add_parser("exit-init")
    x.add_argument("--id", required=True)
    x.add_argument("--listen-port", type=int, default=51877)
    x.add_argument("--tunnel-ip", required=True)
    x.add_argument("--mtu", type=int, default=1280)
    x.set_defaults(func=exit_init)

    x = sub.add_parser("exit-info")
    x.set_defaults(func=exit_info)

    x = sub.add_parser("exit-add-peer")
    x.add_argument("--peer-public-key", required=True)
    x.add_argument("--peer-ip", required=True)
    x.set_defaults(func=exit_add_peer)

    x = sub.add_parser("add-exit")
    x.add_argument("--id", required=True)
    x.add_argument("--endpoint", required=True)
    x.add_argument("--port", type=int, default=51877)
    x.add_argument("--exit-public-key", required=True)
    x.add_argument("--local-ip", required=True)
    x.add_argument("--remote-ip", required=True)
    x.add_argument("--priority", type=int, default=100)
    x.add_argument("--mtu", type=int, default=1280)
    x.set_defaults(func=add_exit)

    x = sub.add_parser("forward-add")
    x.add_argument("--exit", required=True)
    x.add_argument("--proto", choices=["tcp", "udp"], required=True)
    x.add_argument("--listen", type=int, required=True)
    x.add_argument("--target", type=int, required=True)
    x.set_defaults(func=forward_add)

    x = sub.add_parser("l2tp-active")
    x.add_argument("--exit", required=True)
    x.set_defaults(func=l2tp_active)

    x = sub.add_parser("status")
    x.set_defaults(func=status)

    x = sub.add_parser("doctor")
    x.set_defaults(func=doctor)

    x = sub.add_parser("repair")
    x.set_defaults(func=repair)

    x = sub.add_parser("logs")
    x.add_argument("--lines", type=int, default=100)
    x.set_defaults(func=logs)

    return p

def main():
    try:
        args = build_parser().parse_args()
        args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
