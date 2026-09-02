#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:?target path required}"
ORIGINAL="${2:?original path required}"
cp -- "$ORIGINAL" "$TARGET"
if command -v sha256sum >/dev/null 2>&1; then
  echo "RESTORED_SHA256=$(sha256sum -- "$TARGET" | awk '{print $1}')"
else
  echo "RESTORED=$(python - "$TARGET" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
  )"
fi
