# Lesson 0006: a portability fix that writes a machine-specific path is not a portability fix

**Status:** mechanised
**Enforced by:** `tests/test_install.py::test_gate_hook_command_is_portable` — the gate hook command must name a PATH-resolved `python3` and contain no absolute path.
**Date:** 2026-07-20
**Trigger:** you are about to resolve a tool, path or interpreter at install time and write the result into a file the target repo will commit.

## Expected

The gate hook hardcoded `python3`, which is not guaranteed to exist on Windows. Resolving it at
install time — `shutil.which("python3") or sys.executable` — looked like the obvious hardening: the
hook would name an interpreter we had just confirmed was real.

## Happened

It confirmed the interpreter was real *here*. On the author's own machine `shutil.which("python3")`
returns `None` (the working `python3` is a Git Bash shim with no Windows extension, invisible to
Windows Python's PATH resolution), so every scaffold pinned
`C:/Users/…/Python313/python.exe` into `.claude/settings.json` — which is committed, not
gitignored. Every clone of a repo scaffolded that way would have carried a gate hook naming an
interpreter that exists on exactly one computer.

That is worse than the problem it replaced. A `python3` that is missing fails for some people; an
absolute path fails for everyone except its author, and it fails in the direction the whole plugin
exists to prevent — a `PreToolUse` hook that cannot run is a gate that may simply be off, with no
sign in the output.

## Why it got past us

The test written alongside it asserted the hook "names an interpreter that exists" but only checked
the token was non-empty. An absolute path satisfies it; a bare name satisfies it. It passed on every
machine while verifying the property on none of them, and its *name* was the reassurance nobody
looked past.

The deeper miss: no one asked where the file was going. `.claude/settings.json` is committed by
design, because a fresh clone with no hooks has no gate — so anything install-time resolution puts
in it is a decision made for every future reader of the repo, not for the machine doing the install.

## Next time

Before resolving anything at install time, ask **who else will read this file**. If it is committed
or vendored, prefer the portable name and make the failure loud rather than baking in a local
answer; `init.sh` probing the wired command is the backstop that lets the portable choice be safe.

And when a test's name asserts a property, check that its body does too. A test called
`..._that_exists` which never tests existence is worse than no test, because it is counted as one.
