---
description: Render the harness dashboard — cycle states, gate, coverage, per-subagent token burn.
allowed-tools: Bash(python3:*)
---

!`python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" status --write`

Present the dashboard above to the user as-is. If a cycle is `blocked`, say why in one line beneath the table.
