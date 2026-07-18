#!/usr/bin/env bash
# External resume for the build — survives a hard usage-limit kill.
#
# In-session schedulers (ScheduleWakeup, CronCreate) die WITH the session, so a
# usage-limit kill of the orchestrator leaves nothing alive to resume. This script
# runs from launchd (outside any Claude session) on a fixed tick. Each tick:
#   - if all cycles are done -> exit quietly (self-disables the useful work)
#   - if a resume is already in flight -> skip (lock)
#   - else launch a headless `claude -p "/continue"` in the worktree
#     If quota is still exhausted, that headless run exits fast with a limit
#     error and we simply retry next tick — cheap polling until the window resets.
#
# Opt-in: it does nothing until you load the launchd plist (see auto_resume.plist).
set -euo pipefail

WT="/Users/ton/Desktop/git_collection/electric-portfolio/.claude/worktrees/harness-bootstrap"
LOCK="$WT/.claude/state/.resume.lock"
LOG="$WT/.claude/state/auto_resume.log"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"

cd "$WT" || { echo "$(date '+%F %T') worktree missing" >>"$LOG"; exit 0; }

# Already resuming? Bail — never double-dispatch cycles.
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "$(date '+%F %T') skip: resume already running (pid $(cat "$LOCK"))" >>"$LOG"
  exit 0
fi

# Work left?
NEXT="$(python3 .claude/scripts/next_cycle.py 2>/dev/null || echo ERR)"
if [ "$NEXT" = "DONE" ]; then
  echo "$(date '+%F %T') all cycles done — nothing to resume" >>"$LOG"
  exit 0
fi
if [ "$NEXT" = "ERR" ]; then
  echo "$(date '+%F %T') next_cycle.py errored — not resuming blindly" >>"$LOG"
  exit 0
fi

echo $$ >"$LOCK"
trap 'rm -f "$LOCK"' EXIT
echo "$(date '+%F %T') resuming: $NEXT" >>"$LOG"

# Headless resume. --continue reuses the latest session in this dir if present.
# If the usage window is still closed, this exits quickly; the lock is released
# and the next launchd tick retries.
"$CLAUDE_BIN" -p "/continue" >>"$LOG" 2>&1 \
  || echo "$(date '+%F %T') claude exited non-zero (likely quota still out) — will retry next tick" >>"$LOG"

echo "$(date '+%F %T') resume attempt finished" >>"$LOG"
