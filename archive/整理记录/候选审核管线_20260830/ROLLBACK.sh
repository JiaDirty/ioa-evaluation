#!/usr/bin/env sh
set -eu
target="${1:?usage: ROLLBACK.sh TARGET}"
printf '%s\n' 'candidate_review_pipeline=disabled' > "$target"
