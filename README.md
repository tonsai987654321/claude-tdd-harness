# tdd-harness

A Claude Code plugin that installs a **mechanical RED gate** into a repo: a `PreToolUse` hook that blocks every write to production code until a test has been run and watched to fail.

The rule it enforces is one line — *no production code without a failing test on record* — and the point is that it is enforced by the filesystem rather than by instructions. An instruction in `CLAUDE.md` is a strong prior. A hook that exits 2 is a constraint.

## What it installs

| | |
|---|---|
| `.claude/scripts/harness.py` | the gate hook, `red`/`green`, cycle tracking, coverage, token accounting, handoff |
| `.claude/harness.json` | the only project-specific config: owner, guarded paths, runner definitions |
| `.claude/cycles/<p>.json` | the ordered TDD cycles per project, with each project's runner and coverage gate |
| `init.sh` | one command that proves the gate still blocks, then runs every scaffolded suite |
| `CLAUDE.md`, `CONTEXT.md` | the constitution and the domain glossary |
| `docs/PLAYBOOK.md`, `docs/adr/`, `docs/LESSONS.md` | how to run it, why it is shaped this way, what went wrong before |
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

It asks for the owner, the projects, and each project's runner and coverage gate, then scaffolds and verifies. Or drive the scaffolder directly, without the plugin:

```bash
python3 scripts/harness_init.py --target /path/to/repo --owner alice \
  --project billing-api:pytest:90 \
  --project web:vitest:80 \
  --purpose "Three services and a console behind one operator login."
```

Non-destructive: existing files are reported and skipped. `.claude/settings.json` and `.gitignore` are **merged**, not replaced.

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

## What this does not prove

The gate proves **ordering**: a test existed and failed before the code did. It does not prove the test is any good. An `ImportError` counts as RED, and a test that asserts nothing passes the gate exactly like a test that asserts everything.

That gap is why the harness ships a reviewer agent and an evidence rule, and why `harness.py cycle <p> <id> done` refuses without evidence — what ran, what it printed, and the two commit SHAs. A completion nobody can check is indistinguishable from a lie.

Neither mechanism is sufficient alone. A repo that trusts only the gate has automated the appearance of the discipline.

`HARNESS_GATE_BYPASS=1` exists so that a gate nobody can override does not become a hook somebody deletes. It prints to stderr and lands in the transcript. Using it to save time is the single way to make the whole thing worthless.

## Development

```bash
uv run --with pytest python -m pytest tests/ -q
```

The suite drives `harness.py` itself — the gate's block/allow decisions, the handoff's refusal to declare done over its own blockers, UTF-8 pinning on every read and write, and the config layer's guarantee that the defaults still compile to the patterns the hardcoded constants used to hold.

## Releasing

Claude Code caches an installed plugin under the **version declared in `plugin.json`**, not under the commit. Pushing a fix without moving the version ships it to nobody — `marketplace update` reports success and re-fetches nothing.

So for any change to behaviour:

1. Bump `version` in **both** `.claude-plugin/plugin.json` and the entry in `.claude-plugin/marketplace.json`. `claude plugin validate .` fails if they disagree.
2. Commit and push.
3. `claude plugin tag .` — creates `tdd-harness--v<version>`, revalidating that the manifests agree. Push it with `git push origin --tags`.
4. **Verify the installed copy, not the repo.** Run `claude plugin update tdd-harness@tonsai-plugins`, then grep `~/.claude/plugins/cache/tonsai-plugins/tdd-harness/<version>/` for a string that exists only in the new build. If it is not there, it did not ship.

Docs-only changes do not need a bump — they never reach a running session either way.

Step 4 is not paranoia. It is what caught three fixes that had been pushed, reported green, and reached nobody. See [docs/LESSONS.md](docs/LESSONS.md).

## License

MIT — see [LICENSE](LICENSE).
