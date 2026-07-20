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

1. Mark it running:
   `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle $1 <id> red tdd-implementer`

2. Spawn `tdd-implementer` with: project name, cycle id, cycle title, brief path, the `first_test` path, and the coverage gate. Tell it to work in `projects/$1`.

3. When it returns, spawn `cycle-reviewer` on the same cycle.

4. On `REWORK`: hand the findings back to a fresh `tdd-implementer` for the same cycle. Two rework rounds without a PASS means mark the cycle `blocked` and stop — do not grind.

5. On `PASS`, close the cycle with the reviewer's confirmed evidence line — not the implementer's claim, the reviewer re-ran the gates:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle $1 <id> done --evidence "<evidence line>"
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" status --write
   ```

   The `done` transition refuses without evidence. That is deliberate; do not work around it by inventing one.

   Then print the dashboard to the user.

6. Record anything surprising as a new `docs/lessons/NNNN-*.md`. A lesson is something that would have changed how you dispatched the cycle had you known it. Not a diary. If the surprise can be turned into a check instead, write the check and mark the lesson `mechanised` — see `docs/lessons/0000-how-to-write-one.md`.

7. At the end of the session, or when you stop: `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" handoff`.

## Cycle 0 is different

There is no failing test to write for scaffolding, so do it yourself in the main thread. What belongs here is whatever the `stack` in `.claude/harness.json` needs before a test can run at all: the package manifest and lockfile, the test runner's config and fixtures, any container or compose file the integration tests require, the CI workflow, and an example environment file. Read the brief and the stack — do not reach for a file list from another project.

Copy `docs/ci/tdd-ordering.yml` into the project repo as `.github/workflows/tdd-ordering.yml` while you are here. It is the one check that runs where the agent does not: it reads the git log and fails a PR whose code commit has no test commit before it. Everything else the harness does runs on this machine and reads state reachable from it.

The gate does not block any of it: it guards only the paths in `guarded`. The first file you create *under* a guarded path must be demanded by a failing test in cycle 1, and that is the line cycle 0 must not cross.

## Between cycles

Push after every PASS: `git -C projects/$1 push`. A cycle that only exists on this laptop did not happen.
