# Lesson 0005: vendoring without a re-sync path strands every fix you will ever ship

**Status:** mechanised
**Enforced by:** `tests/test_install.py::test_reinstall_resyncs_framework_and_keeps_user_content` — a reinstall rewrites harness-owned files, leaves repo-owned ones, and restamps `.claude/.harness-version`.
**Date:** 2026-07-20
**Trigger:** you are copying your own code into someone else's repo so it keeps working without you.

## Expected

Copying `harness.py` into each scaffolded repo makes that repo self-contained — the stated goal, and
a correct one: a portfolio repo is read by people who do not have the plugin installed. Re-running
`/harness-init` would bring an updated copy across when needed.

## Happened

It would not. The installer skipped every file that already existed, `harness.py` included, so a
reinstall after a plugin update was a no-op on exactly the file the update was for. The only way
through was `--force`, which overwrote `CLAUDE.md`, `CONTEXT.md`, `.claude/harness.json` and every
cycle file along with it — the only content in the repo nobody can regenerate. And no version was
recorded anywhere, so there was no way to tell which build a given repo was carrying.

## Why it got past us

Non-destructive was treated as a property to maximise rather than a property to target. It is right
for the files the user writes and wrong for the files the harness owns, and one flag governed both.
Every test installed into an empty directory, where skip-if-exists and overwrite are the same
behaviour, so nothing ever exercised the second install.

## Next time

Vendoring is a distribution decision, and every distribution channel needs an update path or it is a
one-shot. Decide per file who owns it: the framework re-syncs, the user's content is never touched,
and the two sets are declared in one place. Stamp the version into a file the framework owns — a
marker in a file you refuse to overwrite is a marker that is right once and wrong forever after.

And test the *second* install, not the first. This is [[0004]] one layer down: the same "pushed,
green, reached nobody" with the stale copy sitting in the user's repo instead of the plugin cache.
