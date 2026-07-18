---
description: Orchestrate the next TDD cycle(s) of a project via subagents. Usage - /harness-build <project> [cycle-id]
argument-hint: <project> [cycle-id]
---

You are the **orchestrator** for `$1`. You do not write project code yourself — `tdd-implementer` does.

## Before dispatching

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

6. Append anything surprising to `docs/LESSONS.md`. A lesson is something that would have changed how you dispatched the cycle had you known it. Not a diary.

7. At the end of the session, or when you stop: `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" handoff`.

## Cycle 0 is different

There is no failing test to write for scaffolding. Do it yourself in the main thread: `pyproject.toml` (uv), `tests/conftest.py` with the testcontainers fixture, `docker-compose.yml`, `Dockerfile`, `alembic/`, `.github/workflows/ci.yml`, `.env.example`. The gate does not block these — it guards `app/` only. The first `app/` file you create must be demanded by a failing test in cycle 1.

## Between cycles

Push after every PASS: `git -C projects/$1 push`. A cycle that only exists on this laptop did not happen.
