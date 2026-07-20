# Lesson 0012: a mechanism that can rewrite its own verdict is not a mechanism

**Status:** mechanised
**Enforced by:** `tests/test_gate.py::test_an_open_gate_does_not_open_the_harness_itself` — the gate refuses its own state, script and wiring whatever the gate says, and `init.sh` probes that from the config.
**Date:** 2026-07-20
**Trigger:** you have built something that constrains an agent, and have not yet asked what stops the agent editing the constraint.

## Expected

The gate refuses writes to production code until a test is on record. That refusal is a hook
exiting 2 — a constraint, not a prior, which is the sentence the whole README is built on.

## Happened

The gate guarded `app/` and nothing else. A review probed six paths and found five open:

```
.claude/state/api.json       ALLOWED   ← the gate's own verdict
.claude/scripts/harness.py   ALLOWED   ← the gate itself
.claude/settings.json        ALLOWED   ← the wiring that makes it fire
projects/api/app/x.py        BLOCKED
```

Three lines of exploit, all through the ordinary Write tool:

1. write to `app/x.py` → refused, exit 2
2. write `{"gate": {"state": "OPEN"}}` into `.claude/state/api.json` → allowed
3. write to `app/x.py` again → allowed. No test was ever run.

And `.claude/state/` is gitignored, so none of it leaves a trace — not in the repo, not in review.
The harness had one loud door, `HARNESS_GATE_BYPASS`, deliberately designed to announce itself in
the transcript, and three silent ones that were cheaper to use.

Worse, it was not a config oversight anybody could fix. Every `guarded` pattern is compiled as
`(?:^|/){projects_dir}/(?P<project>[^/]+)/{entry}`, so a harness-root path could not be expressed
in that key however the file was written. The hole was in the shape of the mechanism.

## Why it got past us

The threat model was drift, not shortcuts: someone who forgets the discipline, not someone who
finds it inconvenient at 2am with a deadline. Against forgetting, guarding `app/` is enough. Against
an agent taking the cheapest path to a green board — which is what agents do — the cheapest path
went around the gate rather than through it.

And the mechanism was reviewed by reading it, which shows what it checks. The hole was in what it
does not check, and that is only visible by probing paths nobody thought to name. It took an
explicit "what guards the guard?" to find, in a codebase that had already been reviewed six times.

## Next time

For anything that enforces, list its own inputs — state, code, configuration, wiring — and ask of
each: can the constrained party write this, and would anyone see? Whatever answers *yes, and no* is
the real interface, not the one you designed.

The fix is not to make it unopenable. It is to make every door loud: `protected` refuses
regardless of gate state, and `HARNESS_GATE_BYPASS` still opens it while printing `PROTECTED` into
the transcript. **One loud door, and no quiet ones.**

The line between what is protected and what stays editable is auditability, not importance. Gate
state is gitignored, so an edit there is invisible — shut. Cycle files and `harness.json` are
committed, so lowering a `coverage_gate` to dodge it shows up in a diff someone reads — and the
documented install steps require editing them. Blocking what is already visible buys nothing and
breaks the workflow.
