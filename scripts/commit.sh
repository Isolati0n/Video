#!/usr/bin/env bash
# usage: scripts/commit.sh "<message>" <path>...   -> commits, pushes with rebase retries, prints the sha
set -euo pipefail
msg="$1"; shift
branch="${DEFAULT_BRANCH:?}"
git config user.name "yt-digest[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for p in "$@"; do git add -A -- "$p" 2>/dev/null || true; done
if git diff --cached --quiet; then echo "nothing to commit" >&2; git rev-parse HEAD; exit 0; fi
git commit -q -m "$msg"
for i in 1 2 3 4 5 6; do
  if git push -q origin "HEAD:$branch"; then git rev-parse HEAD; exit 0; fi
  sleep $((i * 3))
  git pull -q --rebase origin "$branch"
done
echo "push failed" >&2; exit 1
