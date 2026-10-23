from pathlib import Path

installer = Path('install-panel-managed.sh')
s = installer.read_text()

old = """sleep 2
systemctl is-active --quiet tp27-panel.service || { systemctl status tp27-panel.service --no-pager -l; exit 3; }
curl -kfsS --connect-timeout 2 --max-time 5 https://127.0.0.1:8787/login >/dev/null
"""
new = """# Validate the Python application before asking systemd to serve it. py_compile
# alone cannot catch import-time failures such as circular imports or missing names.
(
  cd \"$PANEL\"
  PYTHONPATH=\"$PANEL\" \"$PANEL/venv/bin/python\" -c \"import app; assert getattr(app, 'app', None) is not None; print('PRE_RESTART_APP_IMPORT=OK')\"
)

# systemd Type=simple reports active as soon as the process starts; Uvicorn may
# still be importing the app and may not have bound :8787 yet. Use a bounded
# readiness gate instead of treating a single early connection refusal as a
# broken release.
PANEL_READY=0
for attempt in $(seq 1 30); do
  if systemctl is-active --quiet tp27-panel.service && \\
     curl -kfsS --connect-timeout 2 --max-time 4 https://127.0.0.1:8787/login >/dev/null 2>&1; then
    PANEL_READY=1
    echo \"PANEL_READY=OK attempt=$attempt\"
    break
  fi
  sleep 1
done

if [[ \"$PANEL_READY\" -ne 1 ]]; then
  echo \"ERROR: tp27-panel did not become HTTPS-ready within 30s\" >&2
  systemctl status tp27-panel.service --no-pager -l >&2 || true
  journalctl -u tp27-panel.service -n 120 --no-pager >&2 || true
  exit 3
fi
"""

if s.count(old) != 1:
    raise SystemExit(f'installer readiness anchor count={s.count(old)}')
installer.write_text(s.replace(old, new, 1))
