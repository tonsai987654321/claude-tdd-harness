# tdd-harness

A Claude Code plugin that installs a **mechanical RED gate** into a repo: a `PreToolUse` hook that blocks every write to production code until a test has been run and watched to fail.

The rule it enforces is one line — *no production code without a failing test on record* — and the point is that it is enforced by the filesystem rather than by instructions. An instruction in `CLAUDE.md` is a strong prior. A hook that exits 2 is a constraint.

## What it installs

| | |
|---|---|
| `.claude/scripts/harness.py` | the gate hook, `red`/`green`/`quality`/`suite`, cycle tracking, coverage, token accounting, handoff |
| `.claude/harness.json` | the only project-specific config: owner, stack, guarded paths, runner and quality-gate definitions |
| `.claude/cycles/<p>.json` | the ordered TDD cycles per project, with each project's runner and coverage gate |
| `init.sh` | one command that proves the gate still blocks, then runs every scaffolded suite |
| `.claude/.harness-version` | which build of the plugin last re-synced this repo |
| `CLAUDE.md`, `CONTEXT.md` | the constitution and the domain glossary |
| `docs/PLAYBOOK.md`, `docs/adr/`, `docs/lessons/` | how to run it, why it is shaped this way, what went wrong before |
| agents | `tdd-implementer`, `cycle-reviewer`, `project-auditor`, `reconcile-auditor` |
| commands | `/harness-init`, `/harness-build`, `/harness-status`, `/harness-continue` |

The installed repo is **self-contained**. Scripts are copied in, not referenced from the plugin, so the gate keeps working in a fresh clone on a machine that has never heard of this plugin — which is the situation any reviewer of the repo is in.

## Install the plugin

```
/plugin marketplace add tonsai987654321/claude-tdd-harness
/plugin install tdd-harness@tonsai-plugins
```

The repo is both the marketplace and the plugin, so one `marketplace add` is enough. `tonsai-plugins` is the marketplace name from `.claude-plugin/marketplace.json`, not the repo name.

Verify what you installed with `/plugin list`, and remove it with `claude plugin uninstall tdd-harness@tonsai-plugins`.

## Use it

In the repo you want the harness installed into:

```
/harness-init
```

It asks for the owner, the projects, each project's runner and coverage gate, and the stack, then scaffolds and verifies. Or drive the scaffolder directly, without the plugin:

```bash
python3 scripts/harness_init.py --target /path/to/repo --owner alice \
  --project billing-api:pytest:90 \
  --project web:vitest:80 \
  --purpose "Three services and a console behind one operator login."
```

## Upgrading an installed repo

Run `/harness-init` again. The installer splits every file it writes into two sets and treats them differently, which is what makes a second run safe *and* useful:

| | |
|---|---|
| **harness-owned** — `.claude/scripts/`, `.claude/harness-tests/`, `init.sh`, `docs/PLAYBOOK.md`, the shipped ADRs | rewritten every run, so a plugin fix actually reaches the repo |
| **repo-owned** — `CLAUDE.md`, `CONTEXT.md`, `.claude/harness.json`, `.claude/cycles/`, `docs/lessons/` | never rewritten. `--reset <path>` overwrites one, named exactly |

`.claude/settings.json` and `.gitignore` are **merged**, not replaced. `.claude/.harness-version` records which build last re-synced the repo — compare it with `/plugin list` to see whether a repo is carrying an old gate.

A re-sync never rewrites `.claude/harness.json`, so a repo installed on an older version keeps a config that predates whatever keys have since been added. Shipped runners inherit the keys they do not mention, and the installer names every one it defaulted — see [lesson 0008](docs/lessons/0008-a-schema-that-grows-in-a-file-you-refuse-to-rewrite.md).

The blanket `--force` is gone. It was the only way to update a vendored `harness.py`, and it took the constitution, the glossary and the cycle list with it — see [lesson 0005](docs/lessons/0005-vendored-code-needs-a-re-sync-path.md).

## How the gate works

```
Write projects/billing-api/app/tariff.py
  → PreToolUse hook → harness.py gate
  → path matches `guarded`, project gate is SHUT
  → exit 2, write refused

harness.py red billing-api tests/test_tariff.py
  → runs the test, requires it to FAIL
  → exit 0 → refused ("a test that passes before the code exists proves nothing")
  → exit 127 → refused ("infrastructure failure, not a test failure")
  → honest failure → gate OPEN

Write projects/billing-api/app/tariff.py   → allowed
harness.py green billing-api               → suite passes, gate SHUT, coverage recorded
```

The gate is per-project: opening billing-api's gate does not unlock any other project.

Tests, `__init__.py`, config and CI files are never blocked. Neither is anything outside `guarded`.

## Configuration

Everything project-specific lives in `.claude/harness.json`:

```json
{
  "owner": "alice",
  "projects_dir": "projects",
  "requires": ["python3", "uv"],
  "guarded": ["app/", "src/", "alembic/versions/", "alembic/env.py"],
  "runners": {
    "pytest": {
      "cmd": ["uv", "run", "pytest"],
      "red_args": ["-q"],
      "green_args": ["--cov", "--cov-report=term"],
      "red_exit_codes": [1, 2],
      "no_tests_exit": 5,
      "coverage_re": "^TOTAL\\s+.*?(\\d+)%",
      "coverage_multiline": true
    }
  }
}
```

A trailing `/` in `guarded` means "this directory and everything under it"; anything else is an exact filename. Naming `guarded` or `runners` **replaces** the default rather than merging into it — half-overriding the guarded set is how you end up with a gate that protects less than the config appears to say.

`init.sh` generates its self-test probes from `guarded`, so widening the gate widens the check that proves it works.

**Nothing outside this file names a tool.** `stack` is where the toolchain is declared and the agents read it from there; `quality` is the linter, formatter and type checker, run by `harness.py quality <project>`, with `{writable}` expanding to `writable_hint`. `init.sh`, the implementer, the reviewer and the auditor all go through the harness, so a project on poetry, pyright, golangci-lint or cargo changes this file and nothing else. It did not used to be true — four places each named a stack and only one of them was the one the implementer obeyed, which is [lesson 0007](docs/lessons/0007-an-abstraction-that-stops-halfway-hides-where-it-stopped.md).

## Reference documents

Lessons and ADRs are reference material, and reference material only earns its keep when the agent who needs one actually reads it. Both are read through an index rather than loaded:

```
harness.py lessons     # one line per live lesson: number, claim, and the trigger that says when it bites
harness.py adrs        # one line per accepted ADR; superseded ones are history, not guidance
```

The full text of an entry is opened only when its trigger matches the work in hand, so the archive costs context in proportion to what is relevant, not to how long the project has run.

That leaves the index itself as the thing that grows, and the answer to that is retirement rather than shorter prose. When a lesson's failure mode has been made mechanically impossible — a gate probe, a test, a lint rule — it is marked `**Status:** mechanised` with the check named in `**Enforced by:**`, and it drops out of the index. **The check is the lesson now.** The prose stays as the reason the check exists, for whoever finds that check inconvenient later.

`harness.py lessons` prints the split; most of this repo's own lessons are already retired that way.

## What this does not prove

The gate proves **ordering**: a test existed and failed before the code did. It does not prove the test is any good. An `ImportError` counts as RED, and a test that asserts nothing passes the gate exactly like a test that asserts everything.

That gap is why the harness ships a reviewer agent and an evidence rule, and why `harness.py cycle <p> <id> done` refuses without evidence — what ran, what it printed, and the two commit SHAs. A completion nobody can check is indistinguishable from a lie. It refuses below the project's `coverage_gate` too, and `init.sh` fails on any `done` cycle that predates that check.

Neither mechanism is sufficient alone. A repo that trusts only the gate has automated the appearance of the discipline.

`HARNESS_GATE_BYPASS=1` exists so that a gate nobody can override does not become a hook somebody deletes. It prints to stderr and lands in the transcript. Using it to save time is the single way to make the whole thing worthless.

## Development

```bash
uv run --with pytest python -m pytest tests/ -q
```

The suite drives `harness.py` itself — the gate's block/allow decisions, the handoff's refusal to declare done over its own blockers, UTF-8 pinning on every read and write, and the config layer's guarantee that the defaults still compile to the patterns the hardcoded constants used to hold — plus `test_install.py`, which installs *twice* and asserts that the second run re-syncs the harness and leaves your content alone.

`harness.py` resolves the repo root from `CLAUDE_PROJECT_DIR`, falling back to two levels above itself (it normally lives at `.claude/scripts/`). To run a command against this checkout: `CLAUDE_PROJECT_DIR=$PWD python3 scripts/harness.py lessons`.

## Releasing

Claude Code caches an installed plugin under the **version declared in `plugin.json`**, not under the commit. Pushing a fix without moving the version ships it to nobody — `marketplace update` reports success and re-fetches nothing.

So for any change to behaviour:

1. Bump `version` in **both** `.claude-plugin/plugin.json` and the entry in `.claude-plugin/marketplace.json`. `claude plugin validate .` fails if they disagree.
2. Commit and push.
3. `claude plugin tag .` — creates `tdd-harness--v<version>`, revalidating that the manifests agree. Push it with `git push origin --tags`.
4. **Verify the installed copy, not the repo.** Run `claude plugin update tdd-harness@tonsai-plugins`, then grep `~/.claude/plugins/cache/tonsai-plugins/tdd-harness/<version>/` for a string that exists only in the new build. If it is not there, it did not ship.

Docs-only changes do not need a bump — they never reach a running session either way.

Step 4 is not paranoia. It is what caught three fixes that had been pushed, reported green, and reached nobody. See [docs/lessons/](docs/lessons/).

## License

MIT — see [LICENSE](LICENSE).
