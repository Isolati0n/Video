#!/usr/bin/env bash
# Regenerate INDEX.md from the current tree and push it. Because INDEX.md is derived, a rebase
# conflict is resolved by regenerating, never by merging.   usage: scripts/commit_index.sh
set -euo pipefail
branch="${DEFAULT_BRANCH:?}"
git config user.name "yt-digest[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
for i in 1 2 3 4 5 6; do
  git pull -q --rebase origin "$branch" || { git rebase --abort 2>/dev/null || true; git pull -q --no-rebase -s ours origin "$branch" || true; }
  python -m ytdigest.index_repo
  git add INDEX.md
  if git diff --cached --quiet; then echo "INDEX.md unchanged"; exit 0; fi
  git commit -q -m "Update INDEX.md"
  if git push -q origin "HEAD:$branch"; then echo "INDEX.md updated"; exit 0; fi
  git reset -q --hard "origin/$branch" 2>/dev/null || git fetch -q origin "$branch" && git reset -q --hard "origin/$branch"
  sleep $((i * 3))
done
echo "could not push INDEX.md" >&2
exit 1
