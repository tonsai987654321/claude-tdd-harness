#!/usr/bin/env bash
# Live view of the running harness subagents. Run in your own terminal:
#   ./.claude/scripts/watch_subagents.sh
# Tails the newest subagent transcript and prints each step (tool call, test
# output, message text) as the agent produces it. Ctrl-C to stop.
#
# The orchestrator (main chat) only sees a subagent's *final* report; this file
# is the subagent's full running transcript, so this is the one place to watch
# it think in real time.
set -euo pipefail

# Claude Code names a project's session dir after its absolute path with every "/"
# replaced by "-". Derive it rather than hardcoding, so this works in any checkout.
# Run this from the harness root, or set SESS_DIR yourself.
SESS_DIR="${SESS_DIR:-$HOME/.claude/projects/$(pwd | sed 's|/|-|g')}"

# Newest session folder, then its subagents dir.
sub_dir() {
  local newest
  newest=$(find "$SESS_DIR" -maxdepth 2 -type d -name subagents 2>/dev/null \
           | xargs -I{} stat -f '%m %N' {} 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  printf '%s' "$newest"
}

echo "watching for subagent transcripts… (Ctrl-C to stop)"
last=""
while true; do
  d=$(sub_dir)
  if [ -n "$d" ]; then
    newest=$(find "$d" -name 'agent-*.jsonl' 2>/dev/null \
             | xargs -I{} stat -f '%m %N' {} 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -n "$newest" ] && [ "$newest" != "$last" ]; then
      echo; echo "════ following $(basename "$newest") ════"
      last="$newest"
      # Follow the file; pretty-print role + tool/text from each JSONL line.
      tail -n +1 -f "$newest" 2>/dev/null | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    msg = o.get("message", o)
    role = msg.get("role", o.get("type", "?"))
    content = msg.get("content", "")
    if isinstance(content, str):
        if content.strip():
            print(f"[{role}] {content.strip()[:800]}")
        continue
    for block in content if isinstance(content, list) else []:
        t = block.get("type")
        if t == "text" and block.get("text","").strip():
            print(f"[{role}] {block[\"text\"].strip()[:800]}")
        elif t == "tool_use":
            name = block.get("name","?")
            inp = block.get("input",{})
            arg = inp.get("command") or inp.get("file_path") or inp.get("description") or ""
            print(f"  → {name}: {str(arg)[:200]}")
        elif t == "tool_result":
            c = block.get("content","")
            if isinstance(c, list):
                c = " ".join(b.get("text","") for b in c if isinstance(b,dict))
            c = str(c).strip().replace("\n"," ")
            if c:
                print(f"  ⤷ {c[:200]}")
    sys.stdout.flush()
' &
      TAIL_PID=$!
    fi
  fi
  sleep 2
  # If a newer transcript appeared, kill the old follower and re-loop.
  if [ -n "${TAIL_PID:-}" ]; then
    cur=$(find "$(sub_dir)" -name 'agent-*.jsonl' 2>/dev/null \
          | xargs -I{} stat -f '%m %N' {} 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    [ "$cur" != "$last" ] && kill "$TAIL_PID" 2>/dev/null || true
  fi
done
