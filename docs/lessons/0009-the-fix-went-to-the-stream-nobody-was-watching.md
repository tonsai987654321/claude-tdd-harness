# Lesson 0009: the encoding fix went to stdout; the contract runs on stderr

**Status:** mechanised
**Enforced by:** `tests/test_harness_encoding.py::test_stderr_is_utf8_so_the_gate_cannot_die_reporting_a_block` — the gate must exit 2 and print a readable reason for a path outside the machine's codepage.
**Date:** 2026-07-20
**Trigger:** you are fixing an encoding, locale or buffering problem on one stream, or one file, or one call site.

## Expected

`sys.stdout.reconfigure(encoding="utf-8")` at the top of both scripts, with a comment explaining
that a Windows console inherits cp874 and the dashboard's glyphs would kill the command. A whole
test file, `test_harness_encoding.py`, exists to hold the line. The encoding problem was solved.

## Happened

It was solved for the stream that prints reports and unsolved for the stream that carries the
contract. `deny()` writes the block reason to **stderr** and exits 2 — that *is* the PreToolUse
protocol — and stderr was never reconfigured. A guarded path containing a character outside the
machine's codepage raises `UnicodeEncodeError` inside `deny`, the process dies with a traceback,
and the exit code is no longer 2. **The write it was refusing goes through.**

The same gap swallowed every error message in the installer: an em-dash in "unknown runner —
see docs/PLAYBOOK.md" was written as cp874 and came back as `None` to anything reading the stream
as UTF-8.

It surfaced by accident. A test asserted on `proc.stderr`, got `TypeError: argument of type
'NoneType' is not iterable`, and the first instinct was that the test was wrong.

## Why it got past us

The fix was made where the bug had been *seen* — a crash printing the dashboard — rather than
everywhere the cause applied. `sys.stdout` was the subject of the incident, so `sys.stdout` was
the subject of the fix, and the comment above it described the incident so persuasively that it
read as a complete account.

And the failure mode is invisible in the good case: a repo whose paths are all ASCII never trips
it, so the gate looks healthy right up until someone names a file in their own language.

## Next time

When you fix a class of bug, enumerate the instances of the class before writing the fix. Streams:
stdout *and* stderr. Reads *and* writes. Every subprocess with `text=True`, not the one that
crashed.

And weight the instances by consequence, not by how they were found. The dashboard crashing is
loud and harmless. The gate dying while refusing a write is silent and fails open — it deserved
the attention first, and got it last.
