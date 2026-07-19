# Lesson 0008: a required key cannot be added to a file you have promised never to rewrite

**Status:** mechanised
**Enforced by:** `tests/test_quality.py::test_a_config_predating_quality_still_works_for_a_shipped_runner` — a config written before the key existed still runs, and the installer names every key it is defaulting.
**Date:** 2026-07-20
**Trigger:** you are adding a key that your code will require, to a config file the user owns.

## Expected

0.2.0 split the scaffolded repo into harness-owned files, re-synced on every install, and repo-owned
files, never rewritten. 0.3.0 added a `quality` block to each runner and made a missing one a hard
error, on the reasoning from [[0002]]: a repo running no quality gates must not look like a repo
that passed them all. Both decisions were right on their own.

## Happened

Together they bricked the upgrade. `.claude/harness.json` is repo-owned, so a re-sync leaves it at
its 0.2.0 shape — a full `runners` block with no `quality` key. The same re-sync *does* replace
`harness.py` and `init.sh`, because those are harness-owned. So an upgraded repo got new code
demanding a key that the config it was promised to preserve could not contain, and `init.sh` exited
1 on every project. Not a warning: the whole verification entrypoint, red.

Compounding it, `harness_config()` merges shallowly by design — naming `runners` replaces the
default outright — so the new `quality` in `DEFAULT_CONFIG` was shadowed rather than filled in.

## Why it got past us

Sixty-one tests passed. Every one of them either installed into an empty directory, where the
config comes from the current template and has the key, or ran `quality` against a config the test
wrote itself. Nothing constructed *the config an upgrade actually produces*. This is [[0005]]
returning one level down: there the stale artifact was the vendored code, here it is the schema
the vendored code expects. Owning the update path for the files is not the same as owning it for
their contents.

## Next time

Adding a required key to a user-owned file is a migration, not an edit, and it needs one of three
answers decided up front: fill the gap from a default, migrate the file, or refuse to start with a
message that says exactly what to add. Silence is not on the list.

Here the answer is the first: a runner sharing a name with a shipped one inherits the keys it did
not mention, so nothing breaks and keys the user did name still win. The hard error survives for
runners the plugin does not ship, where there is nothing to inherit and guessing would be worse.

And when a rule like *"naming a key replaces it outright"* meets a rule like *"a missing key is
fatal"*, check what happens to the people who wrote their config before the second rule existed.
They cannot have complied with it.
