from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENEW = ROOT / 'bin' / 'tp27-domain-cert-renew'
ENABLE = ROOT / 'enable-domain-ssl.sh'
IP_SSL = ROOT / 'enable-ip-ssl.sh'
SERVICE = ROOT / 'systemd' / 'tp27-domain-cert-renew.service'
TIMER = ROOT / 'systemd' / 'tp27-domain-cert-renew.timer'


def test_domain_renewal_is_locked_and_uses_ssh_acme_egress():
    text = RENEW.read_text(encoding='utf-8')
    assert 'flock -n 9' in text
    assert 'tp27-acme-ssh-forward.service' in text
    assert '--resolve "${ACME_HOST}:443:${ACME_LOCAL}"' in text
    assert 'TP27-DOMAIN-ACME-TEMP' in text
    assert 'timeout 240 "$CERTBOT" certonly' in text
    assert '--force-renewal' in text
    assert 'certbot renew' not in text


def test_domain_renewal_validates_before_seamless_haproxy_reload():
    text = RENEW.read_text(encoding='utf-8')
    assert 'DNS:$TP27_DOMAIN' in text
    assert '-checkend 604800' in text
    assert 'haproxy -c -f "$HAPROXY_CFG"' in text
    assert 'systemctl reload haproxy' in text
    assert 'systemctl restart tp27-panel.service' not in text
    assert 'NO_TUNNEL_MODIFIED' in text


def test_domain_activation_retires_overlapping_ip_renewal_paths():
    text = ENABLE.read_text(encoding='utf-8')
    assert 'systemctl disable --now tp27-cert-renew.timer' in text
    assert 'systemctl disable --now tp27-certbot-renew.timer' in text
    assert 'tp27-panel-letsencrypt-hook.sh' in text
    assert 'tp27-domain-cert-renew.timer' in text
    assert '--domain-only' in text
    assert 'server tp27panel 127.0.0.1:18787' in text
    assert 'NO_TUNNEL_MODIFIED' in text


def test_legacy_ip_ssl_refuses_domain_mode_downgrade():
    text = IP_SSL.read_text(encoding='utf-8')
    assert 'DOMAIN_MODE="/etc/tp27/domain-ssl.env"' in text
    assert 'TP27_ALLOW_IP_SSL_DOWNGRADE' in text
    assert 'Refusing to downgrade' in text


def test_domain_renew_systemd_contract():
    service = SERVICE.read_text(encoding='utf-8')
    timer = TIMER.read_text(encoding='utf-8')
    assert 'Type=oneshot' in service
    assert 'ExecStart=/usr/local/lib/tp27/tp27-domain-cert-renew' in service
    assert 'TimeoutStartSec=300' in service
    assert 'OnCalendar=*-*-* 03:17:00' in timer
    assert 'RandomizedDelaySec=1800' in timer
    assert 'Persistent=true' in timer
