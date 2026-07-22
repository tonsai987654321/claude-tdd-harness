---
description: Build the runnable cycle of every project at once, in parallel. Usage - /harness-build-parallel
argument-hint:
---

You are the **orchestrator** for every project at once. You do not write project code yourself —
`tdd-implementer` does. This is `/harness-build` widened from one project to the whole frontier:
projects are independent (own repo, own state, own history — see `docs/adr/0002-parallel-projects.md`),
so their cycles run concurrently with nothing to race.

## Before dispatching

0. Run `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" lessons`. Open the two or three
   whose trigger matches the cycles about to run, and nothing else.

1. Read the frontier:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/next_cycle.py" --batch
   ```

   Each `BUILD <project> <id> <title>` is one cycle to dispatch — at most one per project, because a
   project's own cycles are ordered. `BLOCKED` lines are projects waiting on unmet dependencies;
   leave them, they are not stalling anything. `DONE` means every project is finished — stop.

## The batch, dispatched at once

2. **Cycle 0 is scaffolding, and stays in the main thread.** For any `BUILD <project> 0 ...`, do it
   yourself here — the package manifest, the runner config, fixtures, compose files, the CI
   workflow, an example env file, whatever the project's `stack` needs before a test can run. Copy
   `docs/ci/tdd-ordering.yml` into the project as `.github/workflows/tdd-ordering.yml`. The gate
   guards only `guarded` paths; the first file under one must be demanded by a failing test in cycle
   1, and that is the line cycle 0 must not cross. A scaffolded project rejoins the frontier at cycle
   1 on the next `--batch`.

3. For every remaining `BUILD <project> <id> <title>`, mark it running and dispatch — **all of them
   in one step, so they run concurrently**:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle <project> <id> red tdd-implementer
   ```

   Then spawn one `tdd-implementer` per cycle, each with: project name, cycle id, cycle title, brief
   path, the `first_test` path, the coverage gate, and `work in projects/<project>`. Each implementer
   opens and closes its own project's gate and commits to its own repo; none is aware of the others.

   **The implementers must not render the board.** No `status --write`, no `handoff` inside a cycle —
   that is the one shared write, and two implementers doing it at once is the only race this design
   has. You render it, once, in step 6.

## Joining the batch

4. When an implementer returns, spawn `cycle-reviewer` on that cycle. Reviews are independent per
   project, so run them as each implementer finishes.

5. Per cycle, on the reviewer's verdict:

   - **REWORK**: hand the findings to a fresh `tdd-implementer` for the same cycle. Two rework rounds
     without a PASS → mark it `blocked` and leave it; do not grind.
   - **PASS**: close it with the reviewer's confirmed evidence — the reviewer re-ran the gates, not
     the implementer's claim:

     ```bash
     python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" cycle <project> <id> done --evidence "<evidence line>"
     ```

     `done` refuses without evidence, and refuses a cycle whose `depends_on` are not themselves done.
     Do not invent evidence to get past it.

6. Once the whole batch has settled, render the board **once**, and push each project that advanced:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" status --write
   git -C projects/<project> push   # for each project that closed a cycle
   ```

   Then print the dashboard. Record anything surprising as a new `docs/lessons/NNNN-*.md` — a lesson
   is something that would have changed how you dispatched, not a diary.

## The loop

7. Ask `--batch` again. A new frontier appears: projects that closed a cycle now offer their next
   one, scaffolded projects offer cycle 1, and blocked ones may have unblocked. Repeat from step 2
   until it prints `DONE`.

8. When you stop, or at the end of the session:
   `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" handoff`.

## What this does not do

Two cycles of the *same* project never run in parallel — the frontier is one cycle per project, and
a project's cycles share a history and a ledger. That is a different problem (worktrees, then a merge
and a reconcile) and it is deliberately not here. If two projects must in fact be ordered,
`build_order` will not enforce it under parallel dispatch; there is no project-level `depends_on`
yet. Until there is, every project is treated as independent, because every project is.
