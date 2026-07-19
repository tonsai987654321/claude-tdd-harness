---
description: Install the TDD harness into this repo — mechanical RED gate, cycle tracking, evidence-gated completion. Usage - /harness-init [owner] [project:runner:coverage ...]
---

You are installing the TDD harness into the current repo. The harness enforces one rule mechanically: **no production code without a failing test on record.**

## Before you write anything

Work out these four things. Ask the user only for what you genuinely cannot determine.

1. **Owner** — the git host account that owns the project repos (`link_projects.sh` clones `<owner>/<project>`). If `gh auth status` names an account and the user has not said otherwise, use it and say so.
2. **Projects** — the names of the projects this harness will build, each with a runner and a coverage gate. If the user has not named them, ask; do not invent projects.
3. **Runner per project** — `pytest` or `vitest`. The installer rejects anything else rather than scaffolding a cycle file that is fatal at the first `red`; a new runner needs an entry in `.claude/harness.json` first, see the "Adding a runner" section of `docs/PLAYBOOK.md`.
4. **Purpose** — one paragraph on what these projects are and who reads them. It becomes the opening of `CLAUDE.md` and it is what stops a later session inventing scope.

If the repo already has a `.claude/settings.json`, say so before running. The installer merges into it rather than replacing it, but the user should know their config is being touched.

## Install

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_init.py" \
  --target . \
  --owner <owner> \
  --project <name>:<runner>:<coverage> \
  --project <name>:<runner>:<coverage> \
  --purpose "<one paragraph>"
```

Run it again on a repo that already has the harness and it **re-syncs**: harness-owned files (`.claude/scripts/`, `.claude/harness-tests/`, `init.sh`, `docs/PLAYBOOK.md`) are rewritten from the plugin so a fix actually lands, and repo-owned files (`CLAUDE.md`, `CONTEXT.md`, `.claude/harness.json`, the cycle files, `docs/lessons/`) are never touched. `--reset <path>` overwrites one repo-owned file, named exactly; there is no blanket overwrite, because the one that used to exist took the user's constitution and cycle list with it.

Read the report it prints. `++` is new, `~~` was re-synced from the plugin, `==` is yours and was left alone — if one of those is `CLAUDE.md`, the repo already had a constitution and the user needs to decide whether to merge the harness sections in by hand.

## Prove it works before you claim it does

```bash
./init.sh
```

This is the whole point. `init.sh` generates a gate probe for every guarded path in the config and asserts the hook blocks each one, plus asserts it *allows* tests and config. **Do not report a successful install without pasting what this printed.** A gate that has quietly stopped blocking looks exactly like a gate that works, right up until it matters.

`requires` in `.claude/harness.json` is derived from the runners you chose, and it lists only what the *project suites* need. It cannot block the gate self-test: that runs first, on `python3` and `uv` alone. If `init.sh` fails there, the gate itself is broken and nothing else matters yet.

If it reports no PreToolUse gate hook, stop. The probe reads the command out of `.claude/settings.json` and runs *that*, so this failure means the editor would write to production code unguarded — not that the check is fussy.

## Then hand over

The installer leaves stubs, not a finished harness. Tell the user plainly what is still theirs to do:

1. **Write the briefs** under `brief/`. The harness builds what the specs say and nothing more; an empty brief is how a build turns into invention.
2. **Lift each brief's cycle list** into `.claude/cycles/<project>.json`. The two-cycle stub is a placeholder, not a plan.
3. **Fill in `CONTEXT.md`** before any code uses domain words. A term that is not in there does not exist in the codebase.
4. **Set the stack** in `CLAUDE.md` where it says TODO.

Do not do steps 1–4 unprompted. They encode decisions the user has not made yet, and a plausible-looking brief you invented is worse than an empty one.
