#!/usr/bin/env python3
"""Pick the next action for continuous operation.

Prints the next thing to do across all projects in build order:
  BUILD <project> <cycle-id> <title>      a cycle to run
  BLOCKED <project> <id> <title> — ...    a cycle waiting on unmet dependencies
  DONE                                    every cycle of every project is done+evidenced

Default: one line — the single next action, for `/harness-continue` and the auto-resume wakeup, so
a fresh session (or one resuming after a usage-limit reset) knows where to pick up without reading
chat history.

`--batch`: the runnable frontier — the lowest runnable cycle of *every* project at once, one BUILD
line each, for parallel dispatch. Projects are independent (own repo, own state, own history; see
ADR-0002), so more than one can be handed out safely. A project's own cycles stay ordered, so the
frontier is at most one cycle per project.
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


def action_for(project: str) -> list[str] | None:
    """The lines this project contributes: a single BUILD, one-or-more BLOCKED, or None if done.

    None means every cycle is closed — the project drops out of the frontier entirely.
    """
    st = state_for(project)
    seed = json.loads((CYCLE_DIR / f"{project}.json").read_text(encoding="utf-8"))["cycles"]

    if st is None:
        # nothing built yet — the first action is cycle 0
        c = seed[0]
        return [f"BUILD {project} {c['id']} {c['title']}"]

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
        # The first runnable cycle is this project's whole contribution to the frontier: its own
        # cycles are ordered, so at most one may start.
        return [f"BUILD {project} {c['id']} {c['title']}"]

    # Every remaining cycle is waiting on another. Reporting nothing would let the caller read a
    # deadlocked project as a finished one.
    if blocked:
        return [
            f"BLOCKED {project} {c['id']} {c['title']} — waiting on cycle(s) "
            + ", ".join(str(d) for d in waiting)
            for c, waiting in blocked
        ]

    return None  # every cycle closed — this project is done


def main() -> None:
    batch = "--batch" in sys.argv[1:]

    printed = False
    for project in build_order():
        lines = action_for(project)
        if lines is None:
            continue  # a finished project is not part of the frontier
        for line in lines:
            print(line)
        printed = True
        if not batch:
            return  # single mode: the first project with an action, and stop

    if not printed:
        print("DONE")


if __name__ == "__main__":
    main()
