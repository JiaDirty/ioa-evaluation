#!/usr/bin/env bash
set -euo pipefail

TARGET_COPY="${1:?usage: ROLLBACK.sh TARGET_COPY}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! -d "$TARGET_COPY/.git" ]]; then
  echo "TARGET_COPY must be a git working tree: $TARGET_COPY" >&2
  exit 2
fi

git -C "$TARGET_COPY" apply --reverse --check "$REPO_ROOT/artifacts/DIFF_FILE"
git -C "$TARGET_COPY" apply --reverse "$REPO_ROOT/artifacts/DIFF_FILE"
echo "ROLLBACK_OK: $TARGET_COPY"
