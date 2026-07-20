# Lesson 0013: whatever you refuse, the way out has to stay open

**Status:** mechanised
**Enforced by:** `tests/test_gate.py::test_a_failed_self_test_leaves_the_repair_path_open` — with the self-test failing, tests, docs and config must still be writable.
**Date:** 2026-07-20
**Trigger:** you are adding a refusal that fires on a condition the user has to fix from inside the thing you are refusing.

## Expected

Making the self-test's verdict binding was the obvious completion of it: `init.sh` proves the gate
still bites, a `SessionStart` hook runs it every session, the gate reads the verdict, and a harness
that has stopped enforcing stops being written against. Fail closed rather than fail quiet.

## Happened

The check went in one line too early — before the exemptions that keep tests, docs and config
writable, rather than after them. So a failed verdict refused *everything* under a guarded path,
including test files.

Which meant `init.sh`'s own probe, `gate allows tests/`, returned 2. The self-test could not pass.
The verdict could never flip back to true. **The repo was unrepairable by exactly the edits that
repair it** — and the deadlock was self-sustaining, because the thing that would clear it was the
thing being blocked.

It surfaced on the third run of a break-and-repair script: break the harness, watch it refuse,
repair it, watch it recover. Step three did not recover.

## Why it got past us

The check was reasoned about in isolation — "when the harness is broken, refuse production code" —
which is correct, and says nothing about ordering. The ordering only matters in the state *after*
the refusal fires, and reading the code forward from a healthy repo never visits that state.

The unit tests did not catch it either. They asserted the refusal happened, which it did.

## Next time

For any new refusal, walk the recovery: someone hits it, and now they must fix it. List what they
have to touch, and check that every one of those is still reachable. If the fix lives inside the
blast radius, the refusal is a brick.

The general shape: **a mechanism that fails closed needs an explicitly designed way out**, and the
way out has to be tested from the failed state, not reasoned about from the healthy one. A
break-and-repair script that asserts all three states — healthy, broken, repaired — is what found
this, and reading the diff never would have.
