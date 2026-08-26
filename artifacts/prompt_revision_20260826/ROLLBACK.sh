#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
original="${repo_root}/docs/当前方案/八项测评场景扩增生成Prompt.md"
default_target="${repo_root}/docs/当前方案/八项测评场景扩增生成Prompt_v2.md"
target="${1:-${default_target}}"

cp -- "${original}" "${target}"
echo "ROLLBACK_OK target=${target}"
