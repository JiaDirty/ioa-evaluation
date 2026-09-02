#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "usage: ROLLBACK.sh TARGET_COPY" >&2
  exit 2
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cp -- "$script_dir/ORIGINAL_FILE" "$1"
printf 'RESTORED_SHA256=%s\n' "$(sha256sum -- "$1" | awk '{print $1}')"
