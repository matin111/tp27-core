#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:-}"
RAW="${TP27_REPO_RAW:-https://raw.githubusercontent.com/matin111/tp27-core/main}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
[[ "$ROLE" == "iran" || "$ROLE" == "exit" ]] || {
  echo "Usage: $0 iran|exit"
  exit 2
}

wait_for_apt() {
  command -v fuser >/dev/null 2>&1 || return 0
  for _ in $(seq 1 90); do
    if ! fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/cache/apt/archives/lock >/dev/null 2>&1; then
      return 0
    fi
    echo "Waiting for apt lock..."
    sleep 2
  done
  echo "ERROR: apt remained busy" >&2
  return 1
}

export DEBIAN_FRONTEND=noninteractive
wait_for_apt
apt-get update -qq
wait_for_apt
apt-get install -y -qq iperf3 curl ca-certificates

if [[ "$ROLE" == "iran" ]]; then
  [[ -d /opt/tp27-panel/static ]] || { echo "ERROR: TP27 panel not found"; exit 3; }

  curl -fsSL "$RAW/systemd/tp27-benchmark.service" -o /etc/systemd/system/tp27-benchmark.service
  curl -fsSL "$RAW/panel/static/benchmark-ui.js" -o /opt/tp27-panel/static/benchmark-ui.js
  curl -fsSL "$RAW/panel/static/benchmark-auto.js" -o /opt/tp27-panel/static/benchmark-auto.js
  curl -fsSL "$RAW/panel/static/index.html" -o /opt/tp27-panel/static/index.html

  systemctl daemon-reload
  systemctl enable tp27-benchmark.service >/dev/null
  systemctl restart tp27-benchmark.service

  echo "============================================================"
  echo " TP27 BENCHMARK IRAN READY"
  echo " iperf3: $(iperf3 --version | head -1)"
  echo " service: $(systemctl is-active tp27-benchmark.service)"
  echo " listen: TCP/52701 only from 10.253.0.0/16"
  echo " fleet bootstrap: automatic from panel"
  echo " panel data planes: untouched"
  echo "============================================================"
  exit 0
fi

[[ -d /opt/tp27-exit-agent ]] || { echo "ERROR: TP27 exit agent not found"; exit 4; }

curl -fsSL "$RAW/agent/tp27_benchmark_addon.py" -o /opt/tp27-exit-agent/tp27_benchmark_addon.py
curl -fsSL "$RAW/agent/tp27-exit-agent-multicore.py" -o /opt/tp27-exit-agent/tp27-exit-agent-multicore.py
curl -fsSL "$RAW/agent/tp27-exit-agent-plain.py" -o /opt/tp27-exit-agent/tp27-exit-agent-plain.py
chmod 700 \
  /opt/tp27-exit-agent/tp27_benchmark_addon.py \
  /opt/tp27-exit-agent/tp27-exit-agent-multicore.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py

PY="/opt/tp27-exit-agent/venv/bin/python"
[[ -x "$PY" ]] || PY=python3
"$PY" -m py_compile \
  /opt/tp27-exit-agent/tp27_benchmark_addon.py \
  /opt/tp27-exit-agent/tp27-exit-agent-multicore.py \
  /opt/tp27-exit-agent/tp27-exit-agent-plain.py

SERVICE=""
if systemctl is-enabled --quiet tp27-exit-agent.service 2>/dev/null; then
  SERVICE="tp27-exit-agent.service"
elif systemctl is-enabled --quiet tp27-exit-agent-plain.service 2>/dev/null; then
  SERVICE="tp27-exit-agent-plain.service"
elif systemctl is-active --quiet tp27-exit-agent.service 2>/dev/null; then
  SERVICE="tp27-exit-agent.service"
elif systemctl is-active --quiet tp27-exit-agent-plain.service 2>/dev/null; then
  SERVICE="tp27-exit-agent-plain.service"
fi
[[ -n "$SERVICE" ]] || { echo "ERROR: active TP27 agent service not found"; exit 5; }

systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || {
  systemctl status "$SERVICE" --no-pager -l || true
  exit 6
}

echo "============================================================"
echo " TP27 BENCHMARK EXIT READY"
echo " iperf3: $(iperf3 --version | head -1)"
echo " service: $SERVICE ($(systemctl is-active "$SERVICE"))"
echo " existing tunnel services: untouched"
echo "============================================================"
