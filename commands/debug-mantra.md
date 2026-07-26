---
description: Find the root cause of a bug, test failure, or surprising behaviour before proposing any fix. Four phases, root cause first.
argument-hint: [what broke]
---

You are debugging: **$ARGUMENTS**

A bug is a gap between what you believe the code does and what it does. Closing that gap is the work; a fix applied before the gap is understood just moves the bug. Under pressure — a red suite, a blocked cycle, a reviewer waiting — guessing feels faster and is not: a wrong fix costs the guess, the re-run, and the confidence you spend defending it.

## The Iron Law

```
NO FIX BEFORE THE ROOT CAUSE IS PROVEN.
```

You have not found the root cause until you can point at the line where the wrong value is born and explain why. "It's probably the …" is a hypothesis, not a cause. Complete each phase before the next.

## Phase 1 — Read the failure exactly

Do not paraphrase the error; read it.

- The full message, the full stack trace, the exit code. The runner prints *why* it failed — `harness.py red`/`green`/`suite` pass the runner's own output straight through. Read to the bottom; the real cause is often under a wall of framework frames.
- Which test, which assertion, which line. Expected vs actual — both concrete values, not "it's wrong".
- Reproduce it deterministically. Run the one test in isolation. If it fails only sometimes, that *is* the finding — order dependence, shared state, a clock, a real I/O call in what should be pure logic (the brief says business logic is pure; an impure function is a prime suspect).

## Phase 2 — Locate the boundary

A cycle crosses layers: test → business logic → adapter → dependency, or gate → state → git. The bug lives at one boundary. Find which by making each boundary *show* what passes through it, rather than reasoning about which one you trust.

- Log, or print, the value entering and leaving each layer. Run once. The first place expected and actual diverge is the layer that owns the bug — stop bisecting there.
- For the harness itself: dump the state file, the gate state, the exact `git log` the check reads. The ledger reads only `git log`; if a verdict surprises you, read the same log it read.

## Phase 3 — Prove the cause

- Trace the wrong value backward from where it surfaced to where it originates. The fix belongs at the origin, not where the symptom showed.
- State the cause in one sentence: "X is wrong because Y, at `file:line`." If you cannot, you are still in Phase 2.
- Confirm it: change that one thing in your understanding and predict what the failure output should become. Do not fix yet — predict.

## Phase 4 — Fix under the discipline

- If the bug is missing or wrong behaviour in project code, it is a new cycle: write the failing test that reproduces it (RED), watch it fail for the reason you proved, then fix (GREEN). A bug you can fix with no test is a bug you cannot prove you fixed.
- Change the one thing the cause named. Resist the adjacent "while I'm here" edits — they are untested and they blur what fixed it.
- Verify against the original symptom, not a proxy: rerun the exact command from Phase 1 and read that it now passes. Green on a different test is not evidence.

## The excuses, answered

| What you'll tell yourself | What is actually true |
|---|---|
| "I've seen this before, I know the fix." | Then proving the cause costs one minute. Skipping it has cost hours, every time it was not the same bug. |
| "No time to investigate, just try the fix." | A guessed fix that fails costs more than the investigation. Systematic is the fast path under pressure, not the slow one. |
| "The test is wrong, I'll change the test." | Sometimes true — but only after Phase 3 proves the code is right. Changing the test first is how a real bug gets a green mark. |
| "I'll fix it and the nearby thing too." | One cause, one change. Bundled edits hide which one worked and add untested surface. |
