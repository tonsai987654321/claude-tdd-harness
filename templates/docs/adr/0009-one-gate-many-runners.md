# ADR-0009 — One gate, many runners

Status: accepted

## Context

The harness's one rule is "no production code without a failing test on record," enforced by a
`PreToolUse` hook that blocks writes to a project's shipped code until `harness.py red` has watched
a test fail ([ADR-0003](0003-mechanical-red-gate.md)).

That gate started Python/pytest-shaped: the guard matched `projects/<p>/app/`, and `red`/`green`
shelled out to `pytest`. The moment a repo contains a project in another language — a TypeScript
SPA, a Go service — the gate either stops applying to it or has to be duplicated.

Leaving one project ungated is worse than it looks. A repo whose stated identity is "test quality
is the product" that exempts its newest and most visible deliverable has not made an exception; it
has withdrawn the claim.

## Decision

Teach the one gate to run any runner, rather than building a parallel gate per language.

1. **Per-project runner, declared not inferred.** Each project's cycle file
   (`.claude/cycles/<p>.json`) carries a `"runner"` field naming a key in `.claude/harness.json`.
   `red`/`green` dispatch on it. **No project's runner is guessed from the files present** — a
   heuristic that picks the wrong runner fails in a way that reads like a broken test.
2. **One guarded set, config-driven.** `guarded` in `.claude/harness.json` lists the paths that
   count as production code across all projects (`app/`, `src/`, migrations, …). Widening it
   widens the gate and the `init.sh` self-test together, because the self-test generates its
   probes from the same list.
3. **Per-runner RED detection.** Runners disagree about exit codes. Each runner definition states
   which codes count as an honest failure, and how "no tests collected" shows up — an exit code
   for some, a message for others. A clean pass, or a run that never happened, is refused
   identically whichever runner produced it.
4. **Per-runner coverage scraping.** Each runner definition carries the regex that finds its
   coverage total, and whether the output needs ANSI escapes stripped first.
5. **Per-project coverage gate**, in the cycle file, not global. Different layers earn different
   bars honestly: a pure logic layer should sit well above a UI layer full of view glue.

## Consequences

- The gate stops being "the Python gate" and becomes "the gate," which is the honest framing —
  the rule was never about Python.
- `harness.py` must locate and invoke toolchains it does not own. It resolves executables through
  `shutil.which` so that a platform-specific shim (`npx.cmd` on Windows) does not read as an
  infrastructure failure and silently leave the gate shut.
- A typo in a runner name is fatal rather than falling back to a default. A silent fallback would
  run the wrong test command and report the result as if it meant something.

## Limitations

This gate proves **ordering**, not test quality. A test that asserts nothing still fails before the
module exists and still passes after — the gate cannot tell the difference, and neither can the
commit history it produces.

Mock-backed component tests are the sharpest version of this: they can be written to pass
trivially. The mitigation is not mechanical. The `cycle-reviewer` must check that a test actually
constrains behaviour — asserts on rendered output, on the URL requested, on the payload sent — and
not merely that it is green. **Neither the gate nor the reviewer is sufficient alone**, and a repo
that trusts only the gate has automated the appearance of the discipline rather than the discipline.

## Alternatives rejected

- **Leave non-Python projects ungated** — fastest, and breaks the repo's central claim on its most
  visible piece.
- **A separate gate per language** — duplicates the mechanism, and two gates drift. One gate with
  a runner table is strictly simpler and keeps the refusal rules in one place.
