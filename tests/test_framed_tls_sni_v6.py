#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / 'bin' / 'tp27-framed-tls.py').read_text(encoding='utf-8')
CONTROLLER = (ROOT / 'panel' / 'provision_resilient.py').read_text(encoding='utf-8')


def test_framed_runtime_uses_dns_sni_instead_of_none():
    assert "RUNTIME_VERSION = '1.1.2'" in RUNTIME
    assert "DEFAULT_TLS_SERVER_NAME = 'www.cloudflare.com'" in RUNTIME
    assert "server_hostname=server_name" in RUNTIME
    assert "server_hostname=None" not in RUNTIME


def test_framed_runtime_rejects_ip_sni_and_keeps_pin_authentication():
    assert "tls_server_name must be a DNS hostname, not an IP address" in RUNTIME
    assert "actual = hashlib.sha256(ssl_obj.getpeercert(binary_form=True)).hexdigest()" in RUNTIME
    assert "if actual != expected:" in RUNTIME
    assert "certificate pin mismatch" in RUNTIME


def test_controller_health_gate_contract_is_unchanged():
    assert 'HEALTH_PING_COUNT = 5' in CONTROLLER
    assert 'HEALTH_REQUIRED_STREAK = 2' in CONTROLLER
    assert "healthy, ping_text = await _health_peer" in CONTROLLER
