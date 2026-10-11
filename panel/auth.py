import hashlib
import hmac
import json
import time
from pathlib import Path

PANEL_CONFIG = Path('/etc/tp27/panel.json')


def load_config():
    return json.loads(PANEL_CONFIG.read_text())


def verify_password(password: str) -> bool:
    cfg = load_config()
    salt = bytes.fromhex(cfg['password_salt'])
    expected = bytes.fromhex(cfg['password_hash'])
    got = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 240000)
    return hmac.compare_digest(got, expected)


def make_session() -> str:
    cfg = load_config()
    exp = int(time.time()) + 43200
    msg = str(exp).encode()
    sig = hmac.new(bytes.fromhex(cfg['session_secret']), msg, hashlib.sha256).hexdigest()
    return f'{exp}.{sig}'


def session_ok(token: str) -> bool:
    try:
        cfg = load_config()
        exp_s, sig = token.split('.', 1)
        if int(exp_s) < time.time():
            return False
        expected = hmac.new(bytes.fromhex(cfg['session_secret']), exp_s.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False
