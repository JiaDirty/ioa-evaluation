#!/usr/bin/env bash
set -euo pipefail

TARGET_COPY="${1:?usage: ROLLBACK.sh TARGET_COPY}"
TARGET_COPY="${TARGET_COPY//\\//}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GIT_BIN="git"
if command -v git.exe >/dev/null 2>&1; then
  GIT_BIN="git.exe"
fi
PATCH_PATH="$REPO_ROOT/artifacts/DIFF_FILE"
if [[ "$GIT_BIN" == "git.exe" ]] && command -v wslpath >/dev/null 2>&1; then
  [[ "$TARGET_COPY" == /mnt/* ]] && TARGET_COPY="$(wslpath -w "$TARGET_COPY")"
  PATCH_PATH="$(wslpath -w "$PATCH_PATH")"
fi

if ! "$GIT_BIN" -C "$TARGET_COPY" rev-parse --git-dir >/dev/null 2>&1; then
  echo "TARGET_COPY must be a git working tree: $TARGET_COPY" >&2
  exit 2
fi

"$GIT_BIN" -C "$TARGET_COPY" apply --reverse --check "$PATCH_PATH"
"$GIT_BIN" -C "$TARGET_COPY" apply --reverse "$PATCH_PATH"
echo "ROLLBACK_OK: $TARGET_COPY"
