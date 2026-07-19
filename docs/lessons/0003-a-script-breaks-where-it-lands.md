# Lesson 0003: a script that works where it was written can be broken where it lands

**Status:** active
**Date:** 2026-07-18
**Trigger:** you are copying a file into a directory other than the one it runs from today, especially if it resolves anything relative to `__file__`.

## Expected

`harness_init.py` runs fine from the plugin, so copying it into the scaffolded repo's
`.claude/scripts/` gives that repo a working scaffolder.

## Happened

`FileNotFoundError: .../.claude/templates/harness.json.tmpl` on first real use. The script resolves
its templates as `Path(__file__).parents[1] / "templates"`. From the plugin root that is
`<plugin>/templates/`, correct. From `<repo>/.claude/scripts/` it becomes `<repo>/.claude/templates/`,
which never exists.

## Why it got past us

It was only ever invoked from the plugin checkout. The first test against a copy passed for the wrong
reason: the target path did not exist, so the directory check exited before any template was read. A
test that stops early is not a test of the path beyond it.

## Next time

Anything relative to `__file__` changes meaning when the file is copied. For every file that gets
installed somewhere else, run it *from the destination* at least once. And when a test passes,
confirm it reached the code you meant to exercise.
