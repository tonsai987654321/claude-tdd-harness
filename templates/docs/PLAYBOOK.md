# Playbook

How to run the harness, resume it after a break, and recover when it misbehaves.

Throughout, `<project>` is a name from `.claude/cycles/` — the harness has no project names baked in.

## Prerequisites

Run the harness's own verification. It checks the toolchain, self-tests that the TDD gate still blocks, scans for unproven completions, and runs every scaffolded project's suite:

```bash
./init.sh
```

It fails fast. If it fails, repair that before adding scope — a harness on a broken baseline just makes the breakage restartable.

## Start a project

From the harness root, in Claude Code:

```
/harness-build <project>
```

The orchestrator reads `.claude/cycles/<project>.json`, picks the lowest cycle that is not `done`, and dispatches it.

Cycle 0 is scaffolding and the orchestrator does it in the main thread — there is no failing test to write for a `pyproject.toml` or a `package.json`. Cycles 1 and up go to `tdd-implementer`, then to `cycle-reviewer`.

## Watch it

```
/harness-status
```

Cycle states, gate state, coverage, and what each subagent cost. Also written to `PROGRESS.md`, so it survives a context reset. Never hand-edit that file; it is regenerated.

## Resume after a break

State lives in `.claude/state/<project>.json`, not in the conversation. Open a fresh session and:

```bash
./init.sh                                      # is the repo healthy?
python3 .claude/scripts/harness.py handoff     # what is the next action?
```

`HANDOFF.md` names the single next action, the blockers, and where each project stands. Then `/harness-build <project>` picks up at the first cycle that is not `done`.

Before you stop, leave the trail for the next session:

```bash
python3 .claude/scripts/harness.py status --write
python3 .claude/scripts/harness.py handoff
```

## Closing a cycle

A cycle is `done` only with evidence — what ran, what it printed, the two commit SHAs:

```bash
python3 .claude/scripts/harness.py cycle <project> 1 done \
  --evidence "pytest 18 passed, cov 94%; ruff clean; mypy --strict clean; a1b2c3d [RED] -> e4f5g6h [GREEN]"
```

It **refuses** without one, and `init.sh` fails on any `done` cycle missing it. A completion nobody can check is indistinguishable from a lie. The evidence comes from the reviewer, which re-runs the gates itself — never from the implementer's own report.

## When the gate blocks you

```
BLOCKED by TDD gate: projects/<project>/app/services/billing.py
```

This is the harness working. Write the test, then:

```bash
python3 .claude/scripts/harness.py red <project> tests/unit/test_billing.py
```

The gate opens only if the runner actually fails. If it reports `NOT RED: the test passed`, the test does not test anything yet.

Do **not** reach for `HARNESS_GATE_BYPASS=1`. It exists so that a gate nobody can override does not become a hook somebody deletes. It prints to stderr, it lands in the transcript, and every use of it makes the git history's `[RED] → [GREEN]` shape a claim rather than a fact.

## When a cycle is blocked

Two rework rounds without a `PASS` marks the cycle `blocked` and stops the run. That is a signal about the brief, not the agents — two independent agents disagreeing twice usually means the cycle's requirement is ambiguous. Read the brief section, decide, and if the decision was non-obvious, record it: an ADR if it was hard to reverse, a line in `CONTEXT.md` if it was a word.

Then reset the cycle and re-run:

```bash
python3 .claude/scripts/harness.py cycle <project> 5 queued
```

## Finishing a project

The Definition of Done is in the project's brief. All of it, not most of it. For a Python service that usually reads:

- [ ] `uv run pytest --cov` — at or above the project's `coverage_gate` in its cycle file
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy --strict app/`
- [ ] `docker compose up` and the service's docs page loads
- [ ] migrations apply from an empty database
- [ ] CI green: install → lint → type-check → test → coverage gate
- [ ] README: what it is, an architecture diagram, worked examples, the coverage table

If the repo is going to be read by someone you are trying to impress, publish it only once it is green:

```bash
gh repo edit <owner>/<project> --visibility public --accept-visibility-change-consequences
```

A private repo is invisible to a reviewer. A public repo with a red badge is worse than invisible. Ship it green or leave it private.

## Adding a project

Drop the brief in `brief/`, write `.claude/cycles/<name>.json` with its cycle ordering and its `runner`, create the repo, and clone it into `projects/` (or run `.claude/scripts/link_projects.sh`). Nothing in `harness.py` knows the project names — it discovers them from the cycle files.

## Adding a runner

`.claude/harness.json` defines each runner: the command, the arguments for the RED and GREEN phases, which exit codes count as an honest failure, and how to scrape coverage out of the output. Add a key there and name it in a project's cycle file.

The exit codes are the part that matters. A runner that exits non-zero because it could not start is **not** a failing test, and accepting it would open the gate on an infrastructure problem — which is precisely the hole the gate exists to close.
