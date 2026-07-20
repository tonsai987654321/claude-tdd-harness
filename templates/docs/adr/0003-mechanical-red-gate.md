# ADR-0003: The RED gate is a hook, not a rule in a prompt

- **Status:** accepted
- **Date:** 2026-07-10

## Context

All three briefs say the same thing in bold: **never write production code without a failing test first.**

An instruction in `CLAUDE.md` is a strong prior, not a constraint. Over a long session an agent under pressure to make a test pass will write the implementation and then the test, and will describe the result as test-driven, because from the inside those two orderings feel identical. Nothing in the artefact distinguishes them afterwards. That is precisely why the discipline is worth anything: it cannot be reconstructed after the fact.

We wanted a constraint that does not depend on the agent's self-report, and that applies equally to subagents, which are the ones actually writing code here.

## Decision

A `PreToolUse` hook on `Write|Edit|MultiEdit|NotebookEdit` runs `harness.py gate`, which denies any write to `projects/*/app/**`, `projects/*/src/**`, `projects/*/alembic/versions/**` or `projects/*/alembic/env.py` unless the project's gate state is `OPEN`.

The boundary is "does it ship, and does it encode behaviour". `alembic/env.py` is named explicitly rather than gating `alembic/` wholesale: it is what `alembic upgrade head` executes on every container boot, so a bug there takes out every table rather than one endpoint — it broke this repo twice before the gate covered it. `alembic.ini` beside it ships too, but no test can drive a config file, so it stays writable.

The gate opens exactly one way: `harness.py red <project> <test>` runs pytest and requires a non-zero exit. Pytest exit 1 (assertion failed) and exit 2 (collection error, typically `ImportError` because the module does not exist yet) both count as RED. Exit 0 is refused — *"the test passed; a test that passes before the code exists proves nothing."* Exit 5, no tests collected, is refused too.

`harness.py green` runs the suite; on success it shuts the gate. The next cycle therefore begins locked, and the only key is another failing test.

Tests, `__init__.py`, `pyproject.toml`, CI config and compose files are never blocked. They are not production code.

## Consequences

The ordering constraint is enforced by the file system, so it holds for the orchestrator, for every subagent, and for a human in a hurry.

Hooks fire inside subagents, which is what makes the subagent-driven design (ADR-0004) safe. The subagent's prompt states the rule; the harness makes the rule true.

The gate is per-project. Three projects can be in three different states.

The escape hatch, `HARNESS_GATE_BYPASS=1`, exists because a gate with no override becomes a gate people route around by disabling the hook. It prints to stderr and lands in the transcript. Using it to save time is the one way to make this whole apparatus worthless.

A cost worth naming: `ImportError` counts as RED, so an agent can satisfy the gate with a test that imports a module that does not exist and asserts nothing. The gate proves ordering, not test quality. Test quality is the reviewer's job (ADR-0004) — the two mechanisms cover each other, and neither alone is sufficient.

## Alternatives

**Trust the prompt, verify at commit.** Verifies coverage and lint, which we do anyway. Cannot verify ordering — by commit time the evidence is gone.

**Require two commits per cycle and inspect the history.** We do this as well, as corroboration. Alone it is weak: `git commit --amend` and a reordered `git add` reproduce the shape without the substance.
