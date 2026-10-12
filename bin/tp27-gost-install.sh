#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="3.3.0"
DEST="${TP27_GOST_BIN:-/usr/local/bin/tp27-gost}"
BASE_URL="https://github.com/go-gost/gost/releases/download/v${VERSION}"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

if [[ -x "$DEST" ]]; then
  CURRENT="$($DEST -V 2>&1 || true)"
  if grep -q "${VERSION}" <<<"$CURRENT"; then
    echo "GOST_VERSION=$VERSION"
    echo "GOST_BINARY=$DEST"
    echo "GOST_INSTALL=ALREADY_OK"
    exit 0
  fi
fi

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64|amd64)
    ASSET="gost_${VERSION}_linux_amd64.tar.gz"
    SHA256="676fb7f78d267b6ae73df719c0c7f2b565dde7147da935cfafbc1e1da558b6d5"
    ;;
  aarch64|arm64)
    ASSET="gost_${VERSION}_linux_arm64.tar.gz"
    SHA256="d03699e3f385d4ff5dad68046712adfcc7515325a064d2ab046e0bece30f8f8f"
    ;;
  *)
    echo "ERROR: unsupported architecture: $ARCH_RAW" >&2
    exit 2
    ;;
esac

for cmd in curl tar sha256sum install; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: missing command: $cmd" >&2
    exit 3
  }
done

TMP="$(mktemp -d /tmp/tp27-gost.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
URL="$BASE_URL/$ASSET"

echo "TP27_GOST_DOWNLOAD=$URL"
curl -fL \
  --retry 3 \
  --retry-delay 2 \
  --retry-all-errors \
  --connect-timeout 8 \
  --max-time 180 \
  "$URL" \
  -o "$TMP/gost.tar.gz"

echo "$SHA256  $TMP/gost.tar.gz" | sha256sum -c - >/dev/null

tar -xzf "$TMP/gost.tar.gz" -C "$TMP"
BIN="$(find "$TMP" -maxdepth 3 -type f -name gost -perm -u+x | head -1)"
[[ -n "$BIN" ]] || BIN="$(find "$TMP" -maxdepth 3 -type f -name gost | head -1)"
[[ -n "$BIN" ]] || { echo "ERROR: gost binary missing in archive" >&2; exit 4; }

install -d -m 755 "$(dirname "$DEST")"
install -m 755 "$BIN" "$DEST"

OUT="$($DEST -V 2>&1 || true)"
grep -q "$VERSION" <<<"$OUT" || {
  echo "ERROR: installed GOST version check failed: $OUT" >&2
  exit 5
}

echo "GOST_VERSION=$VERSION"
echo "GOST_BINARY=$DEST"
echo "GOST_INSTALL=OK"
