# Playbook

How to run the harness, resume it after a break, and recover when it misbehaves.

Throughout, `<project>` is a name from `.claude/cycles/` — the harness has no project names baked in.

For the shape of the whole system rather than the commands to drive it, read [`FLOW.md`](FLOW.md):
seven diagrams covering the gate's decision path, a cycle, the four refusals that close one, install
and re-sync, `init.sh`, the config, and which checks an agent can step around.

## Prerequisites

Run the harness's own verification. It checks the toolchain, self-tests that the TDD gate still blocks, scans for unproven completions, and runs every scaffolded project's suite:

```bash
./init.sh
```

It fails fast. If it fails, repair that before adding scope — a harness on a broken baseline just makes the breakage restartable.

**The harness needs one tool: `uv`.** It runs the harness's own suite in an isolated Python 3.12, and it supplies Python where the machine has none — so a repo with no Python in it still needs it, and a machine with no `python3` still works. If it is missing, `./init.sh` offers to install it and prints the command; run non-interactively it prints the command and stops, because a verification script may not change your machine unasked.

Everything else — Docker, `gh`, Node — belongs to the projects, not to the harness. `init.sh` checks whatever `requires` in `.claude/harness.json` names, after the gate self-test, and never installs any of it: those need a daemon or a login, and which ones you want is your decision.

## Start a project

From the harness root, in Claude Code:

```
/harness-build <project>
```

The orchestrator reads `.claude/cycles/<project>.json`, picks the lowest cycle that is not `done`, and dispatches it.

Cycle 0 is scaffolding and the orchestrator does it in the main thread — there is no failing test to write for a package manifest or a CI workflow. Cycles 1 and up go to `tdd-implementer`, then to `cycle-reviewer`.

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
  --evidence "<runner> 18 passed, cov 94%; quality gates clean; a1b2c3d [RED] -> e4f5g6h [GREEN]"
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

The Definition of Done is in the project's brief. All of it, not most of it. The harness-level part is the same whatever the stack:

- [ ] `harness.py suite <project>` — coverage at or above the `coverage_gate` in its cycle file (enforced: `cycle ... done` refuses below it)
- [ ] `harness.py quality <project>` — every check the runner declares, clean
- [ ] the service starts from a cold checkout by the documented command
- [ ] any schema or migration state applies from empty
- [ ] CI green: install → lint → type-check → test → coverage gate
- [ ] README: what it is, an architecture diagram, worked examples, the coverage table

The brief adds whatever else the stack demands. What it may not do is drop one of the above.

If the repo is going to be read by someone you are trying to impress, publish it only once it is green:

```bash
gh repo edit <owner>/<project> --visibility public --accept-visibility-change-consequences
```

A private repo is invisible to a reviewer. A public repo with a red badge is worse than invisible. Ship it green or leave it private.

## Adding a project

Drop the brief in `brief/`, write `.claude/cycles/<name>.json` with its cycle ordering and its `runner`, create the repo, and clone it into `projects/` (or run `.claude/scripts/link_projects.sh`). Nothing in `harness.py` knows the project names — it discovers them from the cycle files.

## Adding a runner

`.claude/harness.json` defines each runner: the command, the arguments for the RED and GREEN phases, which exit codes count as an honest failure, how to scrape coverage out of the output, and the `quality` commands. Add a key there and name it in a project's cycle file. Nothing else changes — `init.sh`, the implementer, the reviewer and the auditor all run the project's checks through `harness.py quality` and `harness.py suite`, so they follow the config rather than carrying a toolchain of their own.

```json
"gotest": {
  "cmd": ["go", "test", "./..."],
  "red_args": [],
  "green_args": ["-cover"],
  "red_exit_codes": [1],
  "coverage_re": "coverage: ([\\d.]+)% of statements",
  "writable_hint": "internal/",
  "quality": [
    ["gofmt", "-l", "."],
    ["go", "vet", "./..."]
  ]
}
```

`quality` is a list of argv lists, run in the project directory, in order, stopping at the first non-zero exit. `{writable}` in any argument expands to `writable_hint` — that is how `mypy --strict {writable}` stays correct for a project that keeps its source somewhere other than `app/`. A runner with no `quality` key is a hard error rather than a skip: a repo running no quality gates must not look like a repo that passed them all.

The exit codes are the part that matters. A runner that exits non-zero because it could not start is **not** a failing test, and accepting it would open the gate on an infrastructure problem — which is precisely the hole the gate exists to close.
