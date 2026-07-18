#!/usr/bin/env python3
"""Live usage brake, fed by the statusline snapshot.

The 5-hour / 7-day rate-limit figures exist only inside the statusline command's
stdin, which no tool can read directly. The statusline tees them to
`~/.claude/state/usage.json` on every render; this reads that snapshot and decides
whether to keep working or pause until the window resets.

    usage_guard.py            -> human line + exit 0 (go) / 10 (brake) / 2 (stale/unknown)
    usage_guard.py --eta      -> seconds until 5h reset + buffer (for ScheduleWakeup)
    usage_guard.py --json     -> the raw decision as JSON

Brake when five_hour.used_percentage >= THRESHOLD (default 95; override with
USAGE_BRAKE_PCT). The snapshot is only as fresh as the last statusline render, so a
snapshot older than STALE_S is treated as unknown — never brake on stale data, and
never brake blindly if the file is missing (exit 2 = caller proceeds with caution).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SNAPSHOT = Path(os.path.expanduser("~/.claude/state/usage.json"))
THRESHOLD = float(os.environ.get("USAGE_BRAKE_PCT", "95"))
STALE_S = float(os.environ.get("USAGE_STALE_S", "180"))
BUFFER_S = 300  # resume 5 min past the reset


def load() -> dict | None:
    try:
        data = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def decide() -> dict:
    data = load()
    if data is None:
        return {"status": "unknown", "reason": "no snapshot (statusline not rendered yet)"}
    age = time.time() - float(data.get("captured_at", 0))
    if age > STALE_S:
        return {"status": "unknown", "reason": f"snapshot stale ({int(age)}s old)"}
    five = data.get("five_hour") or {}
    pct = five.get("used_percentage")
    resets_at = five.get("resets_at")
    if pct is None:
        return {"status": "unknown", "reason": "no five_hour figure in snapshot"}
    pct = float(pct)
    out = {"used_percentage": pct, "resets_at": resets_at, "threshold": THRESHOLD}
    if pct >= THRESHOLD:
        out["status"] = "brake"
        out["reason"] = f"5h usage {pct:.0f}% >= {THRESHOLD:.0f}%"
    else:
        out["status"] = "go"
        out["reason"] = f"5h usage {pct:.0f}% < {THRESHOLD:.0f}%"
    return out


def eta_seconds(d: dict) -> int:
    resets_at = d.get("resets_at")
    if not resets_at:
        return BUFFER_S
    return max(60, int(float(resets_at) - time.time()) + BUFFER_S)


def main() -> None:
    d = decide()
    if "--json" in sys.argv:
        print(json.dumps(d))
    elif "--eta" in sys.argv:
        print(eta_seconds(d))
        return
    else:
        print(d["reason"])
    # Exit codes: 0 go, 10 brake, 2 unknown.
    sys.exit({"go": 0, "brake": 10, "unknown": 2}[d["status"]])


if __name__ == "__main__":
    main()
