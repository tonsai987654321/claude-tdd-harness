#!/usr/bin/env bash
# In-session auto-continue flag, read by the statusline's `auto-cont` segment.
#
#   autocont.sh on [ttl_seconds]   # arm: write expiry = now + ttl (default 3600)
#   autocont.sh off                # disarm: remove the flag
#   autocont.sh status             # exit 0 if armed & unexpired, else 1
#
# The flag holds an absolute expiry epoch, refreshed each /continue turn. If the
# session dies mid-loop the flag simply expires (self-healing) rather than showing
# a stale TRUE forever. The launchd durable job is a separate signal the statusline
# ORs in; this covers only the transient in-session loop.
set -euo pipefail
FLAG="$HOME/.claude/state/auto_continue.flag"
mkdir -p "$(dirname "$FLAG")" 2>/dev/null || true

now() { date +%s; }

case "${1:-status}" in
  on)
    ttl="${2:-3600}"
    echo $(( $(now) + ttl )) > "$FLAG"
    echo "auto-cont armed (in-session), expires in ${ttl}s"
    ;;
  off)
    rm -f "$FLAG"
    echo "auto-cont disarmed (in-session)"
    ;;
  status)
    [ -f "$FLAG" ] || exit 1
    exp="$(cat "$FLAG" 2>/dev/null || echo 0)"
    case "$exp" in (*[!0-9]*|"") exit 1 ;; esac
    [ "$exp" -gt "$(now)" ] && exit 0 || exit 1
    ;;
  *)
    echo "usage: autocont.sh {on [ttl]|off|status}" >&2; exit 2 ;;
esac
