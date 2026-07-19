# Lesson 0011: a threshold everything documents and nothing checks is not a gate

**Status:** mechanised
**Enforced by:** `tests/test_quality.py::test_a_cycle_under_the_coverage_gate_cannot_be_closed` — `cycle ... done` refuses below the gate, and `init.sh` fails on state written before that check existed.
**Date:** 2026-07-20
**Trigger:** you are about to add a threshold, limit or budget to a config file.

## Expected

`coverage_gate` was a gate. `/harness-init` asks for it per project. The installer validates it to
0–100 and refuses an unreachable one. It is written into every cycle file. `docs/PLAYBOOK.md` lists
it in the definition of done. `reconcile-auditor` is told to read it there "rather than assuming a
number" and to mark a shortfall as REWORK. Six places take it seriously.

## Happened

`grep -rn coverage_gate` returns three hits: the installer writing it, a PLAYBOOK checkbox, and a
sentence in an agent prompt. `harness.py green` scrapes coverage and records it in state, and
nothing ever compares the two numbers. A cycle could be closed at 40% against a gate of 90 and
every mechanism in the harness would agree it was done.

This is the plugin's own thesis, violated in its own config. From the README: *an instruction in
`CLAUDE.md` is a strong prior; a hook that exits 2 is a constraint.* `coverage_gate` had six
documents and no exit 2.

## Why it got past us

Validating the value felt like enforcing it. The installer rejects `coverage:200` with a message
about gates that can never be met — which reads as proof that something, somewhere, meets them.
Effort spent on a value is not the same as a check on it, and the effort is the part that is
visible when you go looking.

The agent instruction hid it too. `reconcile-auditor` really does check, so the property held most
of the time, and "an agent is told to verify it" is exactly the arrangement this project exists to
replace with a mechanism.

## Next time

For every threshold in config, name the line of code that refuses when it is not met. If there
isn't one, it is documentation, and it should either become a check or stop calling itself a gate.

Put the check where the claim is made, not where the number is produced. Coverage climbing toward
the gate is the normal shape of a cycle, so refusing in `green` would fight the work; refusing at
`done` — beside the evidence rule, for the same reason — refuses only the assertion that the work
is finished.
