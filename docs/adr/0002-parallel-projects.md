# ADR-0002: Independent projects build in parallel — the isolation was already there

- **Status:** accepted
- **Date:** 2026-07-22

## Context

`/harness-build` and `next_cycle.py` hand out one cycle at a time. `next_cycle.py` walks projects in
`build_order` and returns the first runnable cycle it finds; the orchestrator dispatches that one,
waits for it, then asks again. A repo with four independent projects builds them strictly one after
another, even though nothing couples them.

The earlier design discussion assumed parallelism needed git worktrees to keep concurrent work from
racing. That is true for two cycles of the *same* project — they share a git history, a state file
and a guarded tree — but it is the wrong frame for projects. Projects are already isolated by
construction:

- `link_projects.sh` clones each project as its **own repo and remote**, and says so outright: "this
  only reproduces the working layout, it does not couple histories." Each lives under
  `projects/<name>/` with its own `.git`.
- State is per project: `cmd_red`, `cmd_green`, `cmd_cycle` and `record_touch` all write
  `.claude/state/<project>.json`. The gate for A reads `state/A.json`, the gate for B reads
  `state/B.json`.
- `depends_on` is within a project — it names cycle ids in the same cycle file. There is no
  cross-project dependency anywhere in the harness; `build_order` orders projects for *dispatch*,
  and nothing enforces it as a prerequisite.

So two implementers working on two different projects touch different directories, different git
repos and different state files. There is nothing between them to race. The blocker to parallelism
was never isolation — there was nothing to isolate. It was that the selector surfaces one cycle and
the orchestrator dispatches one.

## Decision

`next_cycle.py --batch` prints the **runnable frontier**: the lowest runnable cycle of *every*
project that has one, one `BUILD` line each, plus a `BLOCKED` line for any project whose remaining
cycles all wait on unmet dependencies. With no flag its behaviour is unchanged — one line, the way
`/harness-continue` and auto-resume already read it.

A parallel orchestration command dispatches one `tdd-implementer` per `BUILD` line in the batch,
concurrently. Each implementer works inside its own `projects/<name>/`, opens and closes its own
project's gate, and commits to its own repo — exactly as it does today, with no awareness that
another implementer is working a different project at the same time.

Within a project nothing changes: the batch contains at most one cycle per project, so a project's
cycles stay strictly ordered, `depends_on` still holds, and the gate's per-project RED ledger is
untouched. Cross-project is the only axis that goes parallel, and it needs no worktree, no merge and
no reconcile, because the histories were never joined.

The board is the one shared write. `PROGRESS.md` and `HANDOFF.md` render from all projects at once,
so only the orchestrator renders them, once, after the batch joins — never an implementer. This is
already how `/harness-build` is written (the implementer writes code; the orchestrator writes the
board), and it becomes a rule rather than a habit here.

## Consequences

A repo of N independent projects builds in roughly wall-clock/N, with each project keeping the same
per-project ledger, gate and history a reviewer reads standalone. Nothing about a single project's
guarantees weakens; they simply stop waiting in line.

The failure modes that made parallelism look hard do not arise on this axis. There is no shared
mutable state to guard, so no lock, no worktree and no post-merge reconcile. The one genuinely
shared artifact — the board — is written by exactly one actor, which is the pre-existing design.

Cycle 0 stays in the main thread, per project, as `/harness-build` already requires: scaffolding has
no failing test to write, and it is where a project's guarded paths and runner first appear. The
batch may list a fresh project's cycle 0; the orchestrator scaffolds it in the main thread rather
than dispatching it, then the project joins the parallel frontier from cycle 1 on.

## Limitations

This does not deliver intra-project parallelism. Two cycles of one project still run one at a time,
because they share a history and a ledger — parallelising them is the worktree-and-reconcile problem
of the earlier discussion, a separate axis left for later. What ships here is only the axis that was
already free.

It assumes projects are genuinely independent, which today they always are, because the harness has
no way to say otherwise. If two projects must in fact be ordered — B's contract depends on A's —
`build_order` will not enforce it under parallel dispatch, and there is no project-level `depends_on`
to fall back on. That mechanism is future work; until it exists, parallel mode treats every project
as independent because every project is.

The speedup is only realised if the runtime actually runs the dispatched implementers concurrently.
The selector and the isolation are correct regardless — dispatched serially, the batch simply builds
the same projects in the same order — but the wall-clock win depends on concurrent execution the
harness requests and does not control.

The board-render rule is load-bearing and unenforced: an implementer that calls `status --write` or
`handoff` mid-cycle reintroduces the one race this design avoids. It is stated in the command and in
the implementer's brief, and it is exactly the kind of prose constraint this harness distrusts — a
candidate for a later mechanical check, noted so it is not forgotten.

## Test-first plan

The selector is a pure function of the cycle files and state, so it is driven entirely by failing
tests in `test_cycle_deps.py` (which already owns the dispatcher's tests), against real throwaway
roots with more than one project:

1. **Two fresh projects both appear.** Two cycle files, no state. `--batch` lists a `BUILD` for each
   project's cycle 0. Plain `next_cycle` still prints one line — the existing tests lock that.
2. **The frontier is one cycle per project.** Project A three cycles deep with cycle 0 done, project
   B fresh. `--batch` lists A's lowest runnable cycle and B's cycle 0 — two lines, not A's whole
   remaining backlog.
3. **A finished project drops out.** A fully done, B mid-way. `--batch` lists only B.
4. **A blocked project is reported, not dispatched.** A deadlocked on unmet dependencies appears as
   `BLOCKED`, never `BUILD` — the same guarantee the single-line dispatcher already gives, held per
   project across the batch.
5. **Everything done is DONE.** All projects fully closed → `--batch` prints `DONE`, so the
   orchestrator's loop terminates.

Only the selector is mechanically testable; the concurrent dispatch itself lives in the command
prose and is verified by reading, not by a unit test — the same status every agent-facing command
here has.

Shipped files change (`scripts/next_cycle.py`, `commands/`), so the version bumps and
`test_version_bump.py` enforces it.
