---
description: Orchestrate the next TDD cycle(s) of a project via subagents. Usage - /harness-build <project> [cycle-id]
argument-hint: <project> [cycle-id]
---

You are the **orchestrator** for `$1`. You do not write project code yourself — `tdd-implementer` does.

## Before dispatching

0. Run `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" lessons`. One line per live lesson; open the two or three whose trigger matches this cycle and nothing else. The archive is not loaded — you choose what to pay for.
1. Read `.claude/cycles/$1.json` and `.claude/state/$1.json` (the latter may not exist yet; that's cycle 0).
2. Read the brief named in the cycle file. Read it fully. It is the contract.
3. Pick the target cycle: `$2` if given, else the lowest-id cycle not `done`.
4. Refuse to skip. If cycle N-1 is not `done`, say so and stop. The cycles are ordered because each one's tests depend on the last one's code.

## The dispatch loop, per cycle

1. Mark it running and record the attempt:
   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle $1 <id> red tdd-implementer
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" attempt $1 <id>
   ```
   `attempt` keeps a durable count of how many times this cycle has been dispatched, so the bound survives a resumed or compacted session. Read its output:
   - `ATTEMPT …` — dispatch normally.
   - `ESCALATE …` — the last round before the breaker. Dispatch `tdd-implementer` **with a more capable model** (e.g. opus), not the same one that just failed.
   - `BLOCKED …` (non-zero exit) — the breaker tripped; the cycle is already marked `blocked`. Stop dispatching it, report it to the user, and move on. Do not raise `max_attempts` to grind further. Once the cause is fixed (the test corrected, the brief clarified), re-queue it to clear the budget and make it dispatchable again: `harness.py cycle $1 <id> queued`. Do this only on a real fix, not to grind.

2. Spawn `tdd-implementer` with: project name, cycle id, cycle title, brief path, the `first_test` path, and the coverage gate. Tell it to work in `projects/$1`.

3. When it returns, spawn `cycle-reviewer` on the same cycle.

4. On `REWORK`: hand the findings back to a fresh `tdd-implementer` for the same cycle — looping back to step 1, which records the next attempt. The `attempt` breaker, not a number you carry in your head, decides when to escalate the model and when to stop. Do not grind past a `BLOCKED`.

5. On `PASS`, close the cycle with the reviewer's confirmed evidence line — not the implementer's claim, the reviewer re-ran the gates:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle $1 <id> done --evidence "<evidence line>"
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" status --write
   ```

   The `done` transition refuses without evidence. That is deliberate; do not work around it by inventing one.

   Then print the dashboard to the user.

6. Record anything surprising as a new `docs/lessons/NNNN-*.md`. A lesson is something that would have changed how you dispatched the cycle had you known it. Not a diary. If the surprise can be turned into a check instead, write the check and mark the lesson `mechanised` — see `docs/lessons/0000-how-to-write-one.md`.

7. At the end of the session, or when you stop: `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" handoff`.

## The excuses, answered before you reach for them

You are under pressure to show a green board. These shortcuts all produce one that lies.

| What you'll tell yourself | What is actually true |
|---|---|
| "The implementer says it passed — I'll close it on its report." | Close on the *reviewer's* re-run, never the implementer's claim. The implementer is the one under pressure to be done. |
| "`done` wants evidence — I'll write a plausible line." | `done` resolves the SHAs against git and refuses a gate that is still OPEN. An invented line names commits that do not exist, or a green that never ran. |
| "Third rework round, it's close enough — just mark it done." | A cycle that cannot pass in the allotted rounds is `blocked`, not `done`. Grinding a red cycle into a green mark is the one thing this whole apparatus exists to prevent. |
| "I'll skip the blocked cycle and come back." | The cycles are ordered; later tests depend on earlier code. Stop at the block and report it. |

## Cycle 0 is different

There is no failing test to write for scaffolding, so do it yourself in the main thread. What belongs here is whatever the `stack` in `.claude/harness.json` needs before a test can run at all: the package manifest and lockfile, the test runner's config and fixtures, any container or compose file the integration tests require, the CI workflow, and an example environment file. Read the brief and the stack — do not reach for a file list from another project.

Copy `docs/ci/tdd-ordering.yml` into the project repo as `.github/workflows/tdd-ordering.yml` while you are here. It is the one check that runs where the agent does not: it reads the git log and fails a PR whose code commit has no test commit before it. Everything else the harness does runs on this machine and reads state reachable from it.

The gate does not block any of it: it guards only the paths in `guarded`. The first file you create *under* a guarded path must be demanded by a failing test in cycle 1, and that is the line cycle 0 must not cross.

## Between cycles

Push after every PASS: `git -C projects/$1 push`. A cycle that only exists on this laptop did not happen.
