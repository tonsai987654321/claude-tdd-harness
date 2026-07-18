# Lessons

What we learned the expensive way. The bar: **it must change what someone does next time.**
Not a diary, not a changelog.

Write the **surprise** — what you expected versus what actually happened. That is the part that
transfers. A lesson that only records the fix is a changelog entry and helps nobody.

Newest first.

---

## Verifying the repo is not verifying the artifact

**Expected** — push the fixes, run `claude plugin marketplace update`, and the installed plugin now
carries them. The marketplace command reported `✔ Successfully updated marketplace`, which reads
like confirmation.

**Happened** — the cache still held the previous build. Claude Code keys an installed plugin on the
**version declared in `plugin.json`**, not on the commit. The version had not moved, so nothing was
re-fetched. An inspection of the cached files showed all three bugs still present: `harness_init.py`
still in `SCRIPTS`, no runner validation in `harness.py`, `init.sh.tmpl` still pointing at
`$ROOT/tests`.

**Why it got past us** — every check ran against the git repo, which was genuinely correct. Nothing
ran against `~/.claude/plugins/cache/…`, which is the only copy a user's session actually loads.
"Pushed and green" was treated as "delivered", and the two are different artifacts.

**Next time** — after publishing anything that gets cached or vendored, grep the *installed* copy
for a string that only exists in the new build. If it is not there, it did not ship. And bump the
version on every behaviour change: the version is the distribution mechanism, not decoration.
(Docs-only changes are exempt — they never reach a running session either way.)

---

## A script that works where it was written can be broken where it lands

**Expected** — `harness_init.py` runs fine from the plugin, so copying it into the scaffolded repo's
`.claude/scripts/` gives that repo a working scaffolder.

**Happened** — `FileNotFoundError: .../.claude/templates/harness.json.tmpl` on first real use. The
script resolves its templates as `Path(__file__).parents[1] / "templates"`. From the plugin root
that is `<plugin>/templates/`, correct. From `<repo>/.claude/scripts/` it becomes
`<repo>/.claude/templates/`, which never exists.

**Why it got past us** — it was only ever invoked from the plugin checkout. The first test against a
copy passed for the wrong reason: the target path did not exist, so the directory check exited
before any template was read. A test that stops early is not a test of the path beyond it.

**Next time** — anything relative to `__file__` changes meaning when the file is copied. For every
file that gets installed somewhere else, run it *from the destination* at least once. And when a
test passes, confirm it reached the code you meant to exercise.

---

## A conditional around a verification step deletes the verification

**Expected** — wrapping the harness self-suite in `if [ -d "$ROOT/tests" ]` was defensive: run the
tests when they are there, skip harmlessly when they are not.

**Happened** — `harness_init.py` never copied `tests/`, so the condition was false in *every*
scaffolded repo. `init.sh` printed a clean run while silently skipping the only check that proves
the copy of `harness.py` those repos depend on actually works. The worse case never fired but was
live: a target repo with its own `tests/` would have satisfied the condition, run the user's suite
as "the harness suite", and reported their failing tests as a broken gate.

**Why it got past us** — the guard was added to make a check safe rather than to make it correct.
Nobody asked what the false branch meant, and a skipped check and a passing check look identical in
the output.

**Next time** — a verification step that can silently skip is decorative. Either its precondition is
guaranteed (install it, do not hope for it), or the missing precondition is itself a failure. Never
point a self-test at a path the user also owns; `.claude/harness-tests/` cannot collide, `tests/`
can.

---

## "Verified end to end" described the path we designed, not the surface we shipped

**Expected** — the end-to-end run (scaffold → `init.sh` → RED → GREEN → evidence) covered the
plugin, so it was ready to publish.

**Happened** — a later review traced four paths that run had never touched: `link_projects.sh`
(never executed once), the copied scaffolder, a malformed runner definition, and the installed
cache. Three of the four held a real bug. The end-to-end had covered the happy path its author
already had in mind.

**Why it got past us** — the e2e was written alongside the feature, so it inherited the same blind
spots. It proved the design worked, not that the surface was sound.

**Next time** — before publishing, list the entry points a *stranger* will hit — including hand-edited
config, scripts nobody ran, and the copy in the cache — and check them off explicitly. Coverage of
the flow you built is not coverage of the surface you exposed.
