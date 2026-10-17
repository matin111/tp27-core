#!/usr/bin/env bash
set -Eeuo pipefail

PRODUCT="twinpanel"
UPDATER_VERSION=1
LICENSE_API="${TWINPANEL_LICENSE_API:-}"
CHANNEL="${TWINPANEL_RELEASE_CHANNEL:-stable}"
WORKDIR=""

say(){ printf '\n\033[1;35m%s\033[0m\n' "$*"; }
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
echo "English: official TwinPanel install from the entitled release channel"

if [[ -z "$LICENSE_API" ]]; then
  cat >&2 <<'EOF'

License/distribution API is not configured yet.
سرویس مرکزی لایسنس/انتشار هنوز برای این نصب مشخص نشده است.

Vendor setup:
  export TWINPANEL_LICENSE_API="https://YOUR-LICENSE-DOMAIN"

Then run this installer again.
EOF
  exit 2
fi

MACHINE_ID="$(cat /etc/machine-id 2>/dev/null || true)"
DMI_UUID="$(cat /sys/class/dmi/id/product_uuid 2>/dev/null || true)"
[[ -n "$MACHINE_ID" || -n "$DMI_UUID" ]] || fail "Unable to build machine identity"

INSTALL_DIR="/var/lib/twinpanel-license"
STATE_FILE="$INSTALL_DIR/state.json"
install -d -m 700 "$INSTALL_DIR"

if [[ -s "$STATE_FILE" ]]; then
  INSTALLATION_ID="$(python3 - "$STATE_FILE" <<'PY'
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding='utf-8')).get('installation_id','')
except Exception:
    value=''
print(value)
PY
)"
else
  INSTALLATION_ID=""
fi

if [[ -z "$INSTALLATION_ID" ]]; then
  INSTALLATION_ID="$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)"
fi

FINGERPRINT="$(printf '%s\n%s\n' "$MACHINE_ID" "$DMI_UUID" | sha256sum | awk '{print $1}')"

LICENSE_KEY="${TWINPANEL_LICENSE_KEY:-}"
if [[ -z "$LICENSE_KEY" && -t 0 ]]; then
  read -r -p "License key [Enter = 7-day trial]: " LICENSE_KEY || true
fi

REQUEST_FILE="$(mktemp)"
RESPONSE_FILE="$(mktemp)"
WORKDIR="$(mktemp -d)"

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

say "Resolving license/trial and release"
HTTP_CODE="$(curl -sS --connect-timeout 10 --max-time 45 \
  -o "$RESPONSE_FILE" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  --data-binary "@$REQUEST_FILE" \
  "${LICENSE_API%/}/v1/install/resolve" || true)"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "HTTP=$HTTP_CODE" >&2
  cat "$RESPONSE_FILE" >&2 || true
  fail "License/release resolve failed"
fi

mapfile -t VALUES < <(python3 - "$RESPONSE_FILE" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8'))
required=['ok','license_state','version','download_url','sha256','entitlement_token']
missing=[k for k in required if not obj.get(k)]
if missing:
    raise SystemExit('missing fields: '+','.join(missing))
print('1' if obj.get('ok') else '0')
print(obj.get('license_state',''))
print(obj.get('version',''))
print(obj.get('download_url',''))
print(obj.get('sha256',''))
print(obj.get('entitlement_token',''))
print(obj.get('trial_expires_at') or obj.get('license_expires_at') or '')
print(obj.get('seconds_remaining') if obj.get('seconds_remaining') is not None else '')
PY
)

[[ "${VALUES[0]:-0}" == "1" ]] || fail "Entitlement denied"
LICENSE_STATE="${VALUES[1]}"
VERSION="${VALUES[2]}"
DOWNLOAD_URL="${VALUES[3]}"
EXPECTED_SHA="${VALUES[4]}"
ENTITLEMENT_TOKEN="${VALUES[5]}"
EXPIRES_AT="${VALUES[6]}"
SECONDS_REMAINING="${VALUES[7]}"

PACKAGE="$WORKDIR/twinpanel-${VERSION}.tar.gz"
EXTRACT="$WORKDIR/release"
mkdir -p "$EXTRACT"

say "Downloading TwinPanel ${VERSION}"
curl -fL --retry 3 --connect-timeout 10 --max-time 1200 \
  -H "Authorization: Bearer ${ENTITLEMENT_TOKEN}" \
  "$DOWNLOAD_URL" -o "$PACKAGE"

ACTUAL_SHA="$(sha256sum "$PACKAGE" | awk '{print $1}')"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || fail "Package SHA256 mismatch"

say "Package verified"
tar -xzf "$PACKAGE" -C "$EXTRACT"

ENTRY="$(find "$EXTRACT" -maxdepth 3 -type f -path '*/installer/install-product.sh' -print -quit)"
[[ -n "$ENTRY" ]] || fail "installer/install-product.sh missing from release package"
chmod +x "$ENTRY"

STATE_TMP="$WORKDIR/state.json"
python3 - "$STATE_TMP" "$INSTALLATION_ID" "$FINGERPRINT" "$LICENSE_STATE" "$EXPIRES_AT" "$SECONDS_REMAINING" "$VERSION" "$CHANNEL" "$ENTITLEMENT_TOKEN" "$LICENSE_API" <<'PY'
import json,sys,datetime
(path,installation_id,fingerprint,state,expires,remaining,version,channel,token,api)=sys.argv[1:]
obj={
  'installation_id': installation_id,
  'machine_fingerprint': fingerprint,
  'license_state': state,
  'expires_at': expires or None,
  'seconds_remaining': int(remaining) if remaining else None,
  'installed_version': version,
  'release_channel': channel,
  'entitlement_token': token,
  'license_api': api,
  'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
json.dump(obj,open(path,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
PY
install -m 600 "$STATE_TMP" "$STATE_FILE"

export TWINPANEL_LICENSE_API="$LICENSE_API"
export TWINPANEL_INSTALLATION_ID="$INSTALLATION_ID"
export TWINPANEL_ENTITLEMENT_TOKEN="$ENTITLEMENT_TOKEN"
export TWINPANEL_RELEASE_VERSION="$VERSION"
export TWINPANEL_RELEASE_CHANNEL="$CHANNEL"

say "Installing TwinPanel ${VERSION}"
bash "$ENTRY"

say "Installation complete"
echo "Version: $VERSION"
echo "License state: $LICENSE_STATE"
[[ -n "$EXPIRES_AT" ]] && echo "Expires: $EXPIRES_AT"
[[ -n "$SECONDS_REMAINING" ]] && echo "Seconds remaining: $SECONDS_REMAINING"
echo "License state file: $STATE_FILE"
