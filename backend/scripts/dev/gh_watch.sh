#!/bin/sh
# Watcher of one stream: polls GitHub and exits (printing what changed) as soon as anything that
# needs the stream's attention changes — issues labelled to:<stream>, PRs of the stream's branches
# (head, reviews, comments, CI), the last CI run of the stream branch. Run it in the background,
# handle the change, run it again (the parallel-claude-sync skill, §3).
#   scripts/dev/gh_watch.sh C          poll every 120 s
#   scripts/dev/gh_watch.sh C 300      poll every 300 s
set -eu
stream=${1:?A|B|C}
interval=${2:-120}
lower=$(echo "$stream" | tr 'ABC' 'abc')
branch_prefix="phase-4-$lower"

snapshot() {
  gh issue list --label "to:$stream" --state open --limit 100 \
    --json number,updatedAt,title --jq '.[] | "issue #\(.number) \(.updatedAt) \(.title)"'
  gh pr list --state open --limit 100 --json number,headRefName,headRefOid,updatedAt,reviewDecision \
    --jq ".[] | select(.headRefName | startswith(\"$branch_prefix\")) |
      \"pr #\(.number) \(.headRefName) \(.headRefOid[0:7]) \(.updatedAt) \(.reviewDecision)\""
  gh run list --branch "$branch_prefix" --limit 1 --json headSha,status,conclusion \
    --jq '.[] | "ci \(.headSha[0:7]) \(.status) \(.conclusion)"' 2>/dev/null || true
}

before=$(snapshot | sort)
while :; do
  sleep "$interval"
  now=$(snapshot | sort) || continue
  if [ "$now" != "$before" ]; then
    echo "CHANGED $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' "$now" | grep -vxF "$before" | sed 's/^/> /' || true
    exit 0
  fi
done
