# ADR-0010: `green` proves the test still needs the code, by reverting it

- **Status:** accepted
- **Date:** 2026-07-26

## Context

ADR-0003 built the RED gate to prove one thing: no production code without a failing test on
record. It proves *ordering* — the test failed before the code was written — and it says plainly
what it does not prove (its own closing paragraph): "an agent can satisfy the gate with a test that
imports a module that does not exist and asserts nothing. The gate proves ordering, not test
quality."

There is a second, sharper hole in the same seam. The test file is never gated — it must stay
writable, it is how the gate is opened. So between `red` and `green` an agent can:

1. open the gate with a genuinely failing test T1,
2. write production code under the now-open gate,
3. rewrite the test to T2 — something that passes trivially and does not exercise the new code,
4. run `green`, which reruns the suite, sees T2 pass, and shuts the gate.

The cycle is now "green" over code that nothing constrains. `red`'s proof has been spent on a test
that no longer exists. Nothing downstream can see it: the two commits have the right shape, the
suite passes, `history` counts a RED before a GREEN. This is the exact failure mode the harness
exists to prevent, reached through the one file the gate cannot hold.

`obra/superpowers` describes the countermeasure ("revert the fix, the test MUST fail, restore") —
but as prose in a skill, a prior an agent can rationalise past, which ADR-0003 already argues is not
a constraint. The harness's job is to make it mechanical.

## Decision

`green`, after the suite passes and before it shuts the gate, reverts the guarded files this cycle
touched to their pre-cycle content, reruns the test that opened the gate, and requires a real
failure. A test that still passes with the code gone proves nothing, so the gate stays OPEN — the
same refusal `red` makes for a test that passes before the code exists, moved to the other end of
the cycle.

The pieces were already on record and are reused, not invented:

- **What to revert:** `gate.touched`, the guarded files written while the gate was open, already
  recorded by the gate hook (per ADR-0001 the gate is per-project, so this list is A's alone).
- **What to rerun:** `gate.test`, the test path `red` opened on.
- **What to revert *to*:** a new `gate.base_sha`, the commit HEAD pointed at when `red` ran — the
  test is committed there (it is the `[RED]` commit) but the code is not. Captured at `red` time,
  not read as HEAD at `green` time, so the check still works if a cycle commits its code before
  running `green`.

Restore is unconditional (a `finally`): files removed for the check are recreated, files created
for it are removed, the rest are rewritten to their working content. A quality check that leaves the
tree half-reverted would be worse than no check. The revert reads the committed blob into memory and
writes the working content back — never a destructive `git checkout` of the working tree — so an
uncommitted edit cannot be lost.

The escape hatch is `HARNESS_QUALITY_BYPASS=1`, printed to stderr and landing in the transcript,
mirroring `HARNESS_GATE_BYPASS` (ADR-0003). It exists for the genuinely undriveable behavioural
test, and using it says so out loud.

The check fails *open* when it cannot run: no test on record, nothing touched (record_touch is
best-effort and may drop an entry — it must never cost a legitimate green), no baseline, or no git
to revert against. It adds proof where it can; it does not manufacture a refusal from missing data.

## Consequences

The window between `red` and `green` is closed for the reachable case: a cycle can no longer be
certified green by a test that was edited to stop needing its code. The proof `red` starts is
re-checked against the *final* test and the *final* code, which is where it was leaking.

`green` now runs the opening test a second time. For a normal cycle that is one extra fast run; the
suite already passed, so the runner is known to work.

## Limitations

This is whole-change revert, not statement-level mutation (that framework is explicitly out of
scope). When the touched file is imported by the test, removing it breaks the import and the test
fails — so a test that imports the module but asserts nothing still looks real. It does **not** prove
the test is a good test; it proves the test *needs this cycle's code to pass*. Vacuous-assert
detection remains what ADR-0003 said it was: the reviewer's job (ADR-0004), and the two mechanisms
still cover each other rather than either being sufficient alone.

It reverts the files the gate recorded. A cycle that writes behaviour into a file the gate does not
guard (the cost ADR-0001 already names for the monorepo layout) is outside both the gate and this
check.
