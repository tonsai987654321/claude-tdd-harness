# Lesson 0000: how to write one

**Status:** guide
**Trigger:** you are about to record a lesson.

One file per lesson, `NNNN-kebab-slug.md`, newest number highest. `harness.py lessons` prints the
index; nothing reads this directory wholesale, which is what lets it grow.

## The bar

**It must change what someone does next time.** Not a diary, not a changelog.

Write the **surprise** — what you expected versus what actually happened. That is the part that
transfers. A lesson that only records the fix is a changelog entry and helps nobody.

## The shape

```markdown
# Lesson NNNN: <the surprise, stated as a claim>

**Status:** active
**Date:** YYYY-MM-DD
**Trigger:** one line — the situation in which someone would need this

## Expected
## Happened
## Why it got past us
## Next time
```

`Trigger` is the only line the index shows, so it is the line that decides whether anyone ever
opens the file. Write it as the situation, not as the conclusion: *"reinstalling a plugin over an
existing scaffold"* beats *"be careful with caches"*.

## Retirement — the part people skip

When a lesson's failure mode has been made **mechanically impossible** — a gate probe, a test, a
lint rule, a CI check — it stops being a lesson. Mark it:

```markdown
**Status:** mechanised
**Enforced by:** tests/test_gate.py::test_gate_blocks_app_while_shut
```

It drops out of the index and stops competing for attention, because the check is now the lesson.
The file stays; the reasoning is why the check exists and the next person to find that check
inconvenient needs to be able to read it.

So every lesson carries one question worth asking out loud: **can this be made mechanical?** If it
can, the lesson was a stopgap, and leaving it as prose means choosing to be reminded forever
instead of being protected once.
