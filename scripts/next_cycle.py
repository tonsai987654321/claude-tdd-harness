#!/usr/bin/env python3
"""Pick the next action for continuous operation.

Prints, on one line, the next thing to do across all projects in build order:
  BUILD <project> <cycle-id> <title>      a cycle to run
  DONE                                     every cycle of every project is done+evidenced

Used by the `/harness-continue` command and by the auto-resume wakeup so a fresh session — or a
session resuming after a usage-limit reset — knows exactly where to pick up without reading
chat history.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Both streams, always. Windows gives this process the console codepage; an em dash in a message
# or a Thai cycle title then raises UnicodeEncodeError and the command dies reporting something.
# POSIX is already UTF-8, so this is a no-op there. See docs/lessons/0009.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
CYCLE_DIR = ROOT / ".claude" / "cycles"
STATE_DIR = ROOT / ".claude" / "state"


def build_order() -> list[str]:
    def rank(p: Path) -> tuple[int, str]:
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get("build_order", 99), p.stem)
        except (json.JSONDecodeError, OSError):
            return (99, p.stem)

    return [p.stem for p in sorted(CYCLE_DIR.glob("*.json"), key=rank) if not p.name.startswith(".")]


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

        def closed(cycle_id: object) -> bool:
            cur = by_id.get(cycle_id, {"state": "queued", "evidence": ""})
            return cur["state"] == "done" and bool(cur.get("evidence"))

        # Declaration order is not dependency order: cycle 1 may depend on cycle 5. `cycle ... done`
        # refuses an unmet dependency, but by then the work is written — the refusal costs a whole
        # cycle to deliver. Skipping it here spends nothing (lesson 0013: where a refusal lands
        # decides whether it is repairable).
        blocked = []
        for c in seed:
            if closed(c["id"]):
                continue
            waiting = [d for d in (c.get("depends_on") or []) if not closed(d)]
            if waiting:
                blocked.append((c, waiting))
                continue
            print(f"BUILD {project} {c['id']} {c['title']}")
            return

        # Every remaining cycle is waiting on another. Falling through to "DONE" would report a
        # deadlocked project as a finished one.
        if blocked:
            for c, waiting in blocked:
                deps = ", ".join(str(d) for d in waiting)
                print(f"BLOCKED {project} {c['id']} {c['title']} — waiting on cycle(s) {deps}")
            return

    print("DONE")


if __name__ == "__main__":
    main()
