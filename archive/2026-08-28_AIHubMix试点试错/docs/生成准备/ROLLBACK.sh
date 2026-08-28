#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$ROOT/docs/生成准备"
if [ -d "$TARGET" ]; then
  rm -rf -- "$TARGET"
  echo "Removed draft directory: $TARGET"
else
  echo "Nothing to roll back: $TARGET"
fi