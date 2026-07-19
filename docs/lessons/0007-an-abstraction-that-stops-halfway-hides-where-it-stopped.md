# Lesson 0007: an abstraction that stops halfway hides where it stopped

**Status:** mechanised
**Enforced by:** `tests/test_quality.py` — every check runs through `harness.py quality`/`suite` against a runner the tests invent, so a command that only works for the shipped defaults fails.
**Date:** 2026-07-20
**Trigger:** you have made one layer of a system configurable and are about to call the system configurable.

## Expected

`.claude/harness.json` made the harness stack-agnostic. The gate reads its guarded paths from
config, the runners are declared there, and ADR-0009 records the decision as *one gate, many
runners*. A repo on a different stack changes the config and nothing else.

## Happened

Only the gate was configurable. Everything downstream still named one toolchain, in prose, four
separate times:

- `init.sh` sniffed for `pyproject.toml` / `package.json` and ran `uv run ruff`,
  `uv run mypy --strict app/`, `npm run typecheck`
- `tdd-implementer` listed the same commands, and carried `Python 3.12, FastAPI, SQLAlchemy 2.0
  async, Pydantic v2, Alembic, pytest` under the heading *"Rules you do not get to reinterpret"*
- `cycle-reviewer` listed them again
- `project-auditor` listed them a fourth time

Meanwhile `CLAUDE.md` rendered a `{{STACK}}` placeholder that the installer filled with
`TODO: name the stack here` — and `grep -rn STACK agents/` returns nothing. Nobody read it. A user
who wrote *"Go 1.23, chi, sqlc"* into the constitution would have got an agent that built FastAPI
and was right to, because the instruction it actually received said so. A project on poetry got a
green `init.sh` that had run no checks at all: the `pyproject.toml` branch was not taken, the
`package.json` branch was not taken, and the loop printed `skip (not scaffolded yet)`.

## Why it got past us

The config file was treated as evidence of genericity rather than as one instance of it. Once
`runners` existed, "is this stack-specific?" felt answered, and the four prose copies were never
counted as configuration — they were prompts and shell, so they looked like a different kind of
thing. They are not. Anything that names a tool is configuration, whatever file it lives in.

And the failure was invisible from the inside: every check passed on the machine of the one person
whose stack matched.

## Next time

Count the copies. If a fact appears in more than one place, exactly one of them is the source and
the others are drift waiting to happen — and the one that runs is not always the one that reads
like the declaration. Here the constitution *looked* authoritative and the agent prompt *was*.

Then test against something the defaults do not cover. A suite that only exercises `pytest` and
`vitest` cannot tell a generic harness from a hardcoded one; the tests that replaced these four
copies use a runner called `madeup`, and that is the only reason they mean anything.
