#!/usr/bin/env bash
# dapodcast-mamori/precheck.sh — the job (type=watchdog). Exit 0 = silent green.
# Uses a dedicated clone that never holds local work, so it hard-resets to
# origin/main each run. Commits touch only backups/production/ (no site file),
# so they need no deploy.
set -euo pipefail
REPO="${DAPODCAST_BACKUP_ROOT:-$HOME/projects/dapodcast-backup}"
export PATH="$HOME/.local/node/bin:$PATH" GIT_TERMINAL_PROMPT=0
cd "$REPO"
git fetch -q origin
git reset -q --hard origin/main
result="$(node tools/backup-production.mjs)"
if [ "$result" = unchanged ]; then echo "backup unchanged"; exit 0; fi
summary="$(node -e 'const s=require("./backups/production/review-state.json");console.log(s.feedback.length+" notes, review revision "+s.rev)')"
git add backups/production
git commit -q -m "Backup production review state: $summary" -m "Loop: dapodcast-mamori"
for attempt in 1 2 3; do
  if git push -q origin HEAD:main; then echo "backup committed: $summary"; exit 0; fi
  git fetch -q origin
  git rebase -q origin/main || { git rebase --abort; echo "fatal: rebase of backup commit failed"; exit 1; }
done
echo "fatal: push of backup commit failed after 3 attempts"
exit 1
