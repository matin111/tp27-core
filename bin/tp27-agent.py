#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path("/etc/tp27/config.json")
STATE = Path("/var/lib/tp27/status.json")
LOG = Path("/var/log/tp27/agent.log")

CHECK_INTERVAL = 10
REPAIR_AFTER = 3
STALE_HANDSHAKE = 180

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def log(msg):
    line = f"{now_iso()} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, timeout=8):
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 255, "", str(e)

def iface_exists(name):
    rc, _, _ = run(["ip", "link", "show", "dev", name])
    return rc == 0

def ping(ip, iface=None):
    cmd = ["ping", "-n", "-c", "2", "-W", "1"]
    if iface:
        cmd += ["-I", iface]
    cmd.append(ip)
    rc, out, err = run(cmd, timeout=5)
    latency = None
    if rc == 0 and "min/avg/max" in out:
        try:
            latency = float(out.split("=")[-1].strip().split("/")[1])
        except Exception:
            pass
    return rc == 0, latency, err or out[-200:]

def latest_handshake(iface):
    rc, out, _ = run(["wg", "show", iface, "latest-handshakes"])
    if rc != 0 or not out:
        return 0
    vals = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                vals.append(int(parts[-1]))
            except ValueError:
                pass
    return max(vals) if vals else 0

def repair_iface(iface):
    log(f"repair requested iface={iface}")
    run(["systemctl", "restart", f"wg-quick@{iface}.service"], timeout=15)

def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"config read error: {e}")
        return {}

def write_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)

def monitor_iran(cfg, failures):
    results = []
    now = int(time.time())
    for e in cfg.get("exits", []):
        eid = e["id"]
        iface = e["interface"]
        remote = e["remote_ip"]
        exists = iface_exists(iface)
        hs = latest_handshake(iface) if exists else 0
        hs_age = None if hs <= 0 else max(0, now - hs)
        ok_ping, latency, detail = ping(remote, iface if exists else None)
        healthy = bool(exists and ok_ping and hs and hs_age <= STALE_HANDSHAKE)

        if healthy:
            failures[eid] = 0
        else:
            failures[eid] = failures.get(eid, 0) + 1
            if failures[eid] >= REPAIR_AFTER:
                repair_iface(iface)
                failures[eid] = 0

        results.append({
            "id": eid,
            "interface": iface,
            "endpoint": e.get("endpoint"),
            "remote_ip": remote,
            "healthy": healthy,
            "latency_ms": latency,
            "handshake_age_s": hs_age,
            "detail": detail if not healthy else "",
            "checked_at": now_iso(),
        })
    return results

def monitor_exit(cfg, failures):
    e = cfg.get("exit", {})
    if not e:
        return []
    iface = e.get("interface")
    if not iface:
        return []
    exists = iface_exists(iface)
    hs = latest_handshake(iface) if exists else 0
    now = int(time.time())
    hs_age = None if hs <= 0 else max(0, now - hs)
    healthy = bool(exists and (hs_age is None or hs_age <= STALE_HANDSHAKE))
    if healthy:
        failures["exit"] = 0
    else:
        failures["exit"] = failures.get("exit", 0) + 1
        if failures["exit"] >= REPAIR_AFTER:
            repair_iface(iface)
            failures["exit"] = 0
    return [{
        "id": e.get("id", "exit"),
        "interface": iface,
        "healthy": healthy,
        "handshake_age_s": hs_age,
        "checked_at": now_iso(),
    }]

def main():
    failures = {}
    log("agent started")
    while True:
        cfg = load_config()
        role = cfg.get("role")
        if role == "iran":
            tunnels = monitor_iran(cfg, failures)
        elif role == "exit":
            tunnels = monitor_exit(cfg, failures)
        else:
            tunnels = []
        write_state({
            "role": role,
            "node_id": cfg.get("node_id"),
            "updated_at": now_iso(),
            "tunnels": tunnels,
        })
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
