# Lesson 0004: verifying the repo is not verifying the artifact

**Status:** active
**Date:** 2026-07-19
**Trigger:** you have pushed a fix to something that gets cached or vendored — a plugin, a template, a copied script — and are about to call it delivered.

## Expected

Push the fixes, run `claude plugin marketplace update`, and the installed plugin now carries them.
The marketplace command reported `✔ Successfully updated marketplace`, which reads like confirmation.

## Happened

The cache still held the previous build. Claude Code keys an installed plugin on the **version
declared in `plugin.json`**, not on the commit. The version had not moved, so nothing was re-fetched.
An inspection of the cached files showed all three bugs still present: `harness_init.py` still in
`SCRIPTS`, no runner validation in `harness.py`, `init.sh.tmpl` still pointing at `$ROOT/tests`.

## Why it got past us

Every check ran against the git repo, which was genuinely correct. Nothing ran against
`~/.claude/plugins/cache/…`, which is the only copy a user's session actually loads. "Pushed and
green" was treated as "delivered", and the two are different artifacts.

## Next time

After publishing anything that gets cached or vendored, grep the *installed* copy for a string that
only exists in the new build. If it is not there, it did not ship. And bump the version on every
behaviour change: the version is the distribution mechanism, not decoration. (Docs-only changes are
exempt — they never reach a running session either way.)

See also [[0005]] — the same failure one layer further down, where the vendored copy is in the
user's repo rather than the plugin cache.
