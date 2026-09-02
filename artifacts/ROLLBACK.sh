#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: ROLLBACK.sh <target-file> <baseline-file>" >&2
  exit 2
fi

target=$1
baseline=$2
if [[ ! -f "$baseline" ]]; then
  echo "baseline file does not exist: $baseline" >&2
  exit 3
fi
mkdir -p "$(dirname "$target")"
cp -- "$baseline" "$target"
echo "RESTORED $target FROM $baseline"
