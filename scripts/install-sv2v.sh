#!/usr/bin/env bash
# Install a pinned sv2v (SystemVerilog -> Verilog) release binary.
#
# sv2v is a tool for the equivalence check only: `sqrtl check` converts the
# generated SystemVerilog with it before handing the result to Icarus.
# It is deliberately not a dependency of the sqrtl Python package.
#
# Usage: scripts/install-sv2v.sh [DEST_DIR]   (default: /usr/local/bin)
set -euo pipefail

SV2V_VERSION="${SV2V_VERSION:-v0.0.13}"
SV2V_SHA256="${SV2V_SHA256:-552799a1d76cd177b9b4cc63a3e77823a3d2a6eb4ec006569288abeff28e1ff8}"
DEST="${1:-/usr/local/bin}"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ASSET="sv2v-Linux.zip" ;;
  Darwin-*)     ASSET="sv2v-macOS.zip"; SV2V_SHA256="" ;;
  *) echo "install-sv2v: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/zachjs/sv2v/releases/download/${SV2V_VERSION}/${ASSET}"
echo "Downloading ${URL}"
curl -sSL --fail -o "$TMP/$ASSET" "$URL"
if [[ -n "$SV2V_SHA256" ]]; then
  echo "${SV2V_SHA256}  $TMP/$ASSET" | sha256sum -c -
fi
unzip -q -o "$TMP/$ASSET" -d "$TMP/unz"
BIN="$(find "$TMP/unz" -type f -name sv2v | head -n1)"
mkdir -p "$DEST"
install -m 0755 "$BIN" "$DEST/sv2v"
echo "Installed $("$DEST/sv2v" --version) to $DEST/sv2v"
