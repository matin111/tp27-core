#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT="TwinPanel"
UPDATER_VERSION=2
CHANNEL="${TWINPANEL_RELEASE_CHANNEL:-stable}"
PUBLIC_BASE="https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel"
CONFIG_URL="${TWINPANEL_BOOTSTRAP_CONFIG_URL:-$PUBLIC_BASE/config.json}"
LICENSE_API="${TWINPANEL_LICENSE_API_URL:-${TWINPANEL_LICENSE_API:-}}"
WORKDIR=""

say(){ printf '\n\033[1;35m%s\033[0m\n' "$*"; }
info(){ printf '\033[1;34m%s\033[0m\n' "$*"; }
warn(){ printf '\n\033[1;33m%s\033[0m\n' "$*" >&2; }
fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
cleanup(){ [[ -n "${WORKDIR:-}" && -d "$WORKDIR" ]] && rm -rf "$WORKDIR" || true; }
trap cleanup EXIT

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root / با دسترسی root اجرا کنید"

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
else
  fail "Unsupported system: /etc/os-release missing"
fi

[[ "${ID:-}" == "ubuntu" ]] || fail "Ubuntu 22.04/24.04 is required / فقط Ubuntu 22.04/24.04 پشتیبانی می‌شود"
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) fail "Unsupported Ubuntu version: ${VERSION_ID:-unknown}" ;;
esac

for cmd in curl python3 sha256sum tar awk sed; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing command: $cmd"
done

say "TwinPanel Official Bootstrap"
echo "فارسی: نصب رسمی TwinPanel از کانال انتشار معتبر"
echo "English: official TwinPanel installation from the entitled release channel"

WORKDIR="$(mktemp -d -t twinpanel-bootstrap-XXXXXX)"
CONFIG_FILE="$WORKDIR/config.json"

if [[ -z "$LICENSE_API" ]]; then
  if curl -fsSL --connect-timeout 8 --max-time 20 "$CONFIG_URL" -o "$CONFIG_FILE"; then
    LICENSE_API="$(python3 - "$CONFIG_FILE" <<'PY'
import json,sys
try:
    obj=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception:
    obj={}
print(str(obj.get('license_api_url') or '').strip())
PY
)"
  fi
fi

if [[ -z "$LICENSE_API" ]]; then
  cat >&2 <<'EOF'

TwinPanel license/distribution service is not published yet.
سرویس مرکزی لایسنس و انتشار TwinPanel هنوز در config عمومی فعال نشده است.

Vendor/testing override:
  export TWINPANEL_LICENSE_API_URL="https://license.example.com"
  curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/twinpanel/install.sh | sudo -E bash
EOF
  exit 2
fi
LICENSE_API="${LICENSE_API%/}"

INSTALL_DIR="/var/lib/twinpanel-license"
STATE_FILE="$INSTALL_DIR/state.json"
INSTALLATION_ID_FILE="/etc/twinpanel/installation-id"
PUBLIC_KEY_FILE="/etc/twinpanel/license-public.pem"
install -d -m 700 "$INSTALL_DIR" /etc/twinpanel

if [[ -s "$INSTALLATION_ID_FILE" ]]; then
  INSTALLATION_ID="$(tr -d '[:space:]' < "$INSTALLATION_ID_FILE")"
else
  INSTALLATION_ID="$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
  printf '%s\n' "$INSTALLATION_ID" > "$INSTALLATION_ID_FILE"
  chmod 600 "$INSTALLATION_ID_FILE"
fi

FINGERPRINT="$(python3 - <<'PY'
from pathlib import Path
import hashlib, os
values=[]
for raw in ('/etc/machine-id','/sys/class/dmi/id/product_uuid','/sys/class/dmi/id/board_serial'):
    try:
        value=Path(raw).read_text(encoding='utf-8',errors='ignore').strip()
    except Exception:
        value=''
    if value and value.lower() not in {'none','unknown','not specified'}:
        values.append(value)
values.append(os.uname().machine)
print(hashlib.sha256('|'.join(values).encode()).hexdigest())
PY
)"

LICENSE_KEY="${TWINPANEL_LICENSE_KEY:-}"
if [[ -z "$LICENSE_KEY" && -t 0 ]]; then
  echo
  read -r -p "License key [Enter = 7-day trial]: " LICENSE_KEY || true
fi

REQUEST_FILE="$WORKDIR/request.json"
RESPONSE_FILE="$WORKDIR/response.json"

python3 - "$REQUEST_FILE" "$PRODUCT" "$INSTALLATION_ID" "$FINGERPRINT" "$CHANNEL" "$UPDATER_VERSION" "$LICENSE_KEY" <<'PY'
import json,sys
path,product,installation_id,fingerprint,channel,updater_version,license_key=sys.argv[1:]
json.dump({
    'product': product,
    'installation_id': installation_id,
    'machine_fingerprint': fingerprint,
    'license_key': license_key or None,
    'requested_channel': channel,
    'updater_version': int(updater_version),
},open(path,'w',encoding='utf-8'),ensure_ascii=False)
PY
chmod 600 "$REQUEST_FILE"

say "Resolving license/trial and release"
HTTP_CODE="$(curl -sS --connect-timeout 10 --max-time 45 \
  -o "$RESPONSE_FILE" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data-binary "@$REQUEST_FILE" \
  "$LICENSE_API/v1/install/resolve" || true)"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "HTTP=$HTTP_CODE" >&2
  python3 - "$RESPONSE_FILE" <<'PY' >&2 || true
import json,sys
try:
    obj=json.load(open(sys.argv[1],encoding='utf-8'))
    print(obj.get('detail') or obj.get('message') or 'resolve failed')
except Exception:
    print('resolve failed')
PY
  fail "License/release resolve failed"
fi

mapfile -t VALUES < <(python3 - "$RESPONSE_FILE" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
status=obj.get('status') or obj.get('license_state') or ''
url=obj.get('package_url') or obj.get('download_url') or ''
token=obj.get('signed_entitlement') or obj.get('entitlement_token') or ''
public_key=obj.get('license_public_key_pem') or ''
required={
    'ok':obj.get('ok'),
    'status':status,
    'version':obj.get('version'),
    'package_url':url,
    'sha256':obj.get('sha256'),
    'signed_entitlement':token,
    'license_public_key_pem':public_key,
}
missing=[name for name,value in required.items() if not value]
if missing:
    raise SystemExit('missing fields: '+','.join(missing))
print('1' if obj.get('ok') else '0')
print(status)
print(obj.get('version',''))
print(url)
print(obj.get('sha256',''))
print(token)
print(obj.get('trial_started_at') or '')
print(obj.get('trial_expires_at') or '')
print(obj.get('expires_at') or obj.get('license_expires_at') or '')
print(obj.get('seconds_remaining') if obj.get('seconds_remaining') is not None else '')
print(obj.get('release_channel') or obj.get('channel') or 'stable')
print(json.dumps(public_key))
PY
)

[[ "${VALUES[0]:-0}" == "1" ]] || fail "Entitlement denied"
STATUS="${VALUES[1]}"
VERSION="${VALUES[2]}"
PACKAGE_URL="${VALUES[3]}"
EXPECTED_SHA="${VALUES[4],,}"
SIGNED_ENTITLEMENT="${VALUES[5]}"
TRIAL_STARTED_AT="${VALUES[6]}"
TRIAL_EXPIRES_AT="${VALUES[7]}"
EXPIRES_AT="${VALUES[8]}"
SECONDS_REMAINING="${VALUES[9]}"
RELEASE_CHANNEL="${VALUES[10]}"
PUBLIC_KEY_JSON="${VALUES[11]}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "Invalid release SHA256"

python3 - "$PUBLIC_KEY_FILE" "$PUBLIC_KEY_JSON" <<'PY'
import json,os,sys
path,value=sys.argv[1:]
key=json.loads(value)
if 'BEGIN PUBLIC KEY' not in key:
    raise SystemExit('invalid license public key')
with open(path,'w',encoding='utf-8') as handle:
    handle.write(key.rstrip()+'\n')
os.chmod(path,0o644)
PY

STATE_TMP="$WORKDIR/state.json"
python3 - "$STATE_TMP" "$INSTALLATION_ID" "$STATUS" "$TRIAL_STARTED_AT" "$TRIAL_EXPIRES_AT" "$EXPIRES_AT" "$SECONDS_REMAINING" "$VERSION" "$RELEASE_CHANNEL" "$SIGNED_ENTITLEMENT" "$LICENSE_API" <<'PY'
import datetime,json,sys
(path,installation_id,status,trial_started,trial_expires,expires,remaining,version,channel,token,api)=sys.argv[1:]
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
obj={
    'status': status,
    'installation_id': installation_id,
    'signed_entitlement': token,
    'validated_at': now,
    'validation': 'online',
    'trial_started_at': trial_started or None,
    'trial_expires_at': trial_expires or None,
    'expires_at': expires or None,
    'seconds_remaining': int(remaining) if remaining else None,
    'installed_version': version,
    'release_channel': channel,
    'license_api': api,
}
json.dump(obj,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
PY
install -m 600 "$STATE_TMP" "$STATE_FILE"

PACKAGE="$WORKDIR/twinpanel-${VERSION}.tar.gz"
EXTRACT="$WORKDIR/release"
mkdir -p "$EXTRACT"

say "Downloading TwinPanel $VERSION"
curl -fL --retry 3 --connect-timeout 10 --max-time 1200 \
  -H "Authorization: Bearer $SIGNED_ENTITLEMENT" \
  "$PACKAGE_URL" -o "$PACKAGE"

ACTUAL_SHA="$(sha256sum "$PACKAGE" | awk '{print tolower($1)}')"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || fail "Package SHA256 mismatch"

say "Package verified"
python3 - "$PACKAGE" "$EXTRACT" <<'PY'
from pathlib import Path
import sys,tarfile
archive=Path(sys.argv[1])
dest=Path(sys.argv[2]).resolve()
with tarfile.open(archive,'r:gz') as tar:
    for member in tar.getmembers():
        target=(dest/member.name).resolve()
        if target != dest and dest not in target.parents:
            raise SystemExit('unsafe release archive')
    tar.extractall(dest)
PY

ENTRY="$(find "$EXTRACT" -maxdepth 4 -type f -path '*/installer/install.sh' -print -quit)"
[[ -n "$ENTRY" ]] || fail "installer/install.sh missing from release package"
chmod +x "$ENTRY"

export TWINPANEL_LICENSE_API_URL="$LICENSE_API"
export TWINPANEL_RELEASE_CHANNEL="$RELEASE_CHANNEL"
export TWINPANEL_RELEASE_VERSION="$VERSION"

say "Installing TwinPanel $VERSION"
bash "$ENTRY" --official

say "Installation complete"
echo "Version: $VERSION"
echo "License state: $STATUS"
[[ -n "$EXPIRES_AT" ]] && echo "Expires: $EXPIRES_AT"
[[ -n "$TRIAL_EXPIRES_AT" ]] && echo "Trial expires: $TRIAL_EXPIRES_AT"
[[ -n "$SECONDS_REMAINING" ]] && echo "Seconds remaining: $SECONDS_REMAINING"
echo "Panel credentials: /root/twinpanel-initial-admin.txt"
echo "License state: $STATE_FILE"
echo "نسخه: $VERSION"
echo "وضعیت لایسنس: $STATUS"
