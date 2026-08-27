#!/usr/bin/env bash
set -euo pipefail

baseline_commit="ff3c34fbfed8310c60f47e45c9f424cba32129b3"
repo_input="${1:-$(git rev-parse --show-toplevel)}"
target_input="${2:?usage: ROLLBACK.sh [repo-root] <empty-target-directory>}"

if command -v cygpath >/dev/null 2>&1; then
  repo_root="$(cygpath -u "${repo_input}")"
  target_dir="$(cygpath -u "${target_input}")"
else
  repo_root="${repo_input}"
  target_dir="${target_input}"
fi

mkdir -p "${target_dir}"
if [ -n "$(find "${target_dir}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "target directory must be empty: ${target_dir}" >&2
  exit 2
fi

git -c core.autocrlf=false -C "${repo_root}" archive "${baseline_commit}" \
  | tar -xf - -C "${target_dir}"
restored_tree="$(git -C "${repo_root}" rev-parse "${baseline_commit}^{tree}")"
echo "restored_commit=${baseline_commit}"
echo "restored_tree=${restored_tree}"
echo "target=${target_dir}"
