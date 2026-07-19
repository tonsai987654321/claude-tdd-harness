# Lesson 0002: a conditional around a verification step deletes the verification

**Status:** mechanised
**Enforced by:** `tests/test_install.py::test_harness_suite_is_installed`, plus the unconditional check in `init.sh` — the suite is always installed, so a missing one is a failure rather than a skip.
**Date:** 2026-07-18
**Trigger:** you are adding an `if` around a check so it can skip when a precondition is absent.

## Expected

Wrapping the harness self-suite in `if [ -d "$ROOT/tests" ]` was defensive: run the tests when they
are there, skip harmlessly when they are not.

## Happened

`harness_init.py` never copied `tests/`, so the condition was false in *every* scaffolded repo.
`init.sh` printed a clean run while silently skipping the only check that proves the copy of
`harness.py` those repos depend on actually works. The worse case never fired but was live: a target
repo with its own `tests/` would have satisfied the condition, run the user's suite as "the harness
suite", and reported their failing tests as a broken gate.

## Why it got past us

The guard was added to make a check safe rather than to make it correct. Nobody asked what the false
branch meant, and a skipped check and a passing check look identical in the output.

## Next time

A verification step that can silently skip is decorative. Either its precondition is guaranteed
(install it, do not hope for it), or the missing precondition is itself a failure. Never point a
self-test at a path the user also owns; `.claude/harness-tests/` cannot collide, `tests/` can.
