#!/usr/bin/env python3
"""Pick the next action for continuous operation.

Prints, on one line, the next thing to do across all projects in build order:
  BUILD <project> <cycle-id> <title>      a cycle to run
  DONE                                     every cycle of every project is done+evidenced

Used by the `/continue` command and by the auto-resume wakeup so a fresh session — or a
session resuming after a usage-limit reset — knows exactly where to pick up without reading
chat history.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Cycle titles carry Thai and an em dash, and harness.py writes the state as UTF-8. Windows gives a
# piped stdout the locale codec (cp874 here), which cannot encode either. POSIX stdout is already
# UTF-8, so this is a no-op there. Same guard as harness.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
CYCLE_DIR = ROOT / ".claude" / "cycles"
STATE_DIR = ROOT / ".claude" / "state"


def build_order() -> list[str]:
    def rank(p: Path) -> tuple[int, str]:
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get("build_order", 99), p.stem)
        except (json.JSONDecodeError, OSError):
            return (99, p.stem)

    return [p.stem for p in sorted(CYCLE_DIR.glob("*.json"), key=rank)]


def state_for(project: str) -> dict | None:
    p = STATE_DIR / f"{project}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> None:
    for project in build_order():
        st = state_for(project)
        seed = json.loads((CYCLE_DIR / f"{project}.json").read_text(encoding="utf-8"))["cycles"]

        if st is None:
            # nothing built yet — the first action is cycle 0
            c = seed[0]
            print(f"BUILD {project} {c['id']} {c['title']}")
            return

        # a cycle marked done but lacking evidence is not really done
        by_id = {c["id"]: c for c in st["cycles"]}
        for c in seed:
            cur = by_id.get(c["id"], {"state": "queued", "evidence": ""})
            done = cur["state"] == "done" and cur.get("evidence")
            if not done:
                print(f"BUILD {project} {c['id']} {c['title']}")
                return

    print("DONE")


if __name__ == "__main__":
    main()
