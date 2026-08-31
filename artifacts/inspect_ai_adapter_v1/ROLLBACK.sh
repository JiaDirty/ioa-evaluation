#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: ROLLBACK.sh <target-file> <original-file>" >&2
  exit 2
fi

target=$1
original=$2
if [[ ! -f "$original" ]]; then
  echo "original file does not exist: $original" >&2
  exit 3
fi

mkdir -p "$(dirname "$target")"
cp -- "$original" "$target"
expected=$(sha256sum "$original" | awk '{print $1}')
actual=$(sha256sum "$target" | awk '{print $1}')
if [[ "$actual" != "$expected" ]]; then
  echo "rollback hash mismatch: expected=$expected actual=$actual" >&2
  exit 4
fi

echo "RESTORED $target FROM $original"
echo "SHA256 $actual"
