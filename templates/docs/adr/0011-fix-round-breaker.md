# ADR-0011: The fix-round bound is durable state, not a number in the orchestrator's head

- **Status:** accepted
- **Date:** 2026-07-26

## Context

When a cycle fails review, the orchestrator retries it with a fresh implementer. The bound on that
retrying lived only in the build command's prose — "two rework rounds without a PASS means mark the
cycle blocked and stop." That is a prior, not a constraint, and it has the failure mode every prose
rule in this harness has had: it does not survive. A compaction drops it; a session resumed by the
auto-resume tick (which exists precisely to continue across a usage-limit kill) never read it. An
unattended loop can then rework one stuck cycle forever, spending the whole run on it.

The orchestrator also has no notion of *escalating* a stuck cycle — the same model that just failed
three times gets a fourth identical try.

## Decision

The attempt count is durable per-cycle state. `harness.py attempt <project> <cycle>` increments it,
saves it to `.claude/state/`, and reports where the cycle stands:

- below the bound → `ATTEMPT n/m`, dispatch normally;
- at the last round (`m-1`) → `ESCALATE`, retry with a more capable model;
- at the bound (`m`) → the cycle is marked `blocked` and the command exits non-zero, so an
  orchestrating loop cannot read the result as a go-ahead to dispatch again.

`next_cycle` reads the same count from the same state, so a cycle that has exhausted its rounds
surfaces as `BLOCKED` to a resuming session instead of being handed out to grind. The bound is
`max_attempts` in `.claude/harness.json` (default 5, floored at 2), read identically by both.

The split is deliberate and follows ADR-0003. What can be mechanical is: the count, its durability,
the non-zero exit that stops the loop, and the `BLOCKED` surfaced by `next_cycle`. What cannot be —
choosing the subagent's model — stays prose in the build command, but prose backed by a number the
harness maintains rather than one the orchestrator has to remember.

## Consequences

An unattended loop stops after a bounded number of failed rounds on one cycle and reports it for a
human, rather than grinding. A hard cycle gets one stronger attempt before it blocks. The bound is
honoured across compaction and resumed sessions because it is state, not memory.

`blocked` set by the breaker is the same state a human or the orchestrator can set, and the board
already renders it (`[!]`). Raising `max_attempts` to push past a block is possible and visible in a
committed config diff — the same "loud, not forbidden" stance as the gate's bypass.

The block is not a one-way trap. Re-queuing the cycle (`cycle <project> <id> queued`) clears the
attempt budget and makes it dispatchable again, so a cycle blocked while its cause was unfixed
resumes once the cause is fixed — without hand-editing gitignored state. Reset lives on `queued`
alone, never on the `red` the orchestrator sets every dispatch, or the count would zero each round
and the breaker could never trip. `attempt` also refuses a cycle already `done`, so a stray call
cannot overwrite a finished cycle's state with `blocked`.

## Limitations

The count measures dispatches, not the quality of each attempt: five shallow tries and five serious
ones both trip the breaker at five. It bounds waste; it does not judge effort. And like every count
the harness keeps, it is only as good as the orchestrator calling `attempt` at each dispatch — the
build command does, but a hand-run cycle that skips it is uncounted, the same best-effort limit
`record_touch` carries.
