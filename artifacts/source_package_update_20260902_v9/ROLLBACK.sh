#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:?target file is required}"
ORIGINAL="${2:?original file is required}"
cp -- "$ORIGINAL" "$TARGET"
HASH="$(sha256sum -- "$TARGET" | awk '{print $1}')"
printf 'RESTORED_SHA256=%s\n' "$HASH"

