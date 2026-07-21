# ADR-0001: One repo can hold many projects — the ordering check scopes by path, not by `.git`

- **Status:** proposed
- **Date:** 2026-07-22

## Context

The harness lays projects out as `projects/<name>/`, but it forces each of those directories to
be its own git repository. That is not a stated design goal — it falls out of one check.

`history` is the ordering check meant to run in CI, where the agent writes the code but not the
verdict (ADR-0003, ADR-0004). It reads only `git log`, which the harness never writes, and it
counts rather than asserts: walking oldest to newest, a commit touching tests banks a RED and a
commit touching guarded code spends one; code that arrives with nothing banked is the violation.

Two lines make it single-project only (`harness.py`):

```python
# reads the WHOLE repo log — no pathspec
["git", "-C", d, "log", "--reverse", "--no-merges", "--format=%H%x1f%s", "--name-only"]
# prepends ONE project's prefix to every file in every commit
code = any(any(p.search(prefix + f) for p in patterns) for f in files if not exempt(cfg, f))
```

The ledger walks the entire log and `prefix` names exactly one project. So the check is only
correct when the log contains one project's commits and nothing else. Put two projects in one
repository and it breaks in both directions:

- **Cross-project mispairing.** A RED committed for project A banks a credit that the next code
  commit for project B spends. B's untested code passes because A paid for it; A's real violation
  can be hidden the same way. The count is meaningless once the log interleaves.
- **Cross-project misclassification.** A single commit touching `projects/A/src/foo.py` and
  `projects/B/test/bar.py` has one hardcoded `prefix` prepended to both filenames. Files are
  attributed to the wrong project before they are even classified.

So a team wanting one repository — shared tooling, one PR that touches two services, one CI run —
has to shard it into N git repos to satisfy a check about *ordering within a project*. The
requirement is an accident of implementation, not a property anyone chose.

A worktree-based parallelism scheme does not fix this. Worktrees share one object store and merge
back to one branch; the merged log still interleaves projects. Concurrency and monorepo are
different axes, and only the check's single-project assumption is in the way of the second.

## Decision

`history` and `gate` attribute every file to a project by its path, and check each project against
only its own commits and its own files.

`history` filters the log with a pathspec — `git log ... -- <projects_dir>/<name>/` — so the walk
sees only commits that touched this project, and within each commit `settle()` counts only files
under that project's prefix. Each project's RED/GREEN ledger is computed from its own history
alone, whether that history lives in a dedicated repo or interleaved in a shared one.

The existing dual mode is preserved. `--repo` still points the check at a checkout whose files are
`src/...` (CI cloning one service); a monorepo presents the same files as `projects/A/src/...`. The
synthetic `prefix` was invented precisely to unify those two shapes, and it continues to — the
check detects whether a file already carries the project prefix and does not double it.

`gate` already resolves the owning harness by walking up from the written file to the nearest
`.claude/scripts/harness.py` rather than trusting `CLAUDE_PROJECT_DIR` (`harness_root_for`), which
is what makes it worktree-correct today. It gains the symmetric step for monorepos: attribute a
guarded write to a project by its `projects/<name>/` path prefix, and consult that project's gate
state. State is already keyed per project; only the path-to-project mapping is new.

The `.git`-per-project requirement is dropped. In a monorepo the git root is the repository and a
project is a subdirectory of it; in a single-project repo the two coincide, exactly as before.

## Consequences

One repository can hold many projects with shared tooling, a single lockfile, one CI pipeline, and
PRs that cross project boundaries — while each project keeps an independent RED-before-GREEN ledger
that a reviewer can still verify from `git log` alone.

The per-project subrepo layout keeps working unchanged. A single-project repo is the degenerate
case of the path-scoped check — one prefix, one project — so nothing about the common path shifts.
The four real projects the suite exercises today (`meter-billing-api` and the rest) are the
regression proof: their verdicts must not move.

CI runs the ordering check once per project (`tdd-ordering.yml` gains a loop over the projects it
finds), which is more invocations but each reads a smaller, path-scoped slice of the log.

A cost worth naming: path attribution is only as good as the layout. A project whose guarded files
live outside its `projects/<name>/` prefix — a shared library imported by two services, say — is
invisible to both projects' ledgers and is checked by neither. The monorepo makes that arrangement
easy to reach for, where the subrepo layout made it physically awkward. The `guarded` patterns must
stay rooted at the project prefix, and a shared package that ships behaviour needs its own project
entry rather than a home in the gaps between them.

## Limitations

This does not prove ordering across projects, and there is no such thing to prove — the ledger is
per-project by definition, and a dependency between two projects' *code* is not something `git log`
can order. It proves, per project, that each shipped commit was preceded by a test commit within
that project.

It does not resolve merge conflicts on shared files, and a monorepo produces more of them: two
projects advancing in the same PR touch one lockfile, one CI config, one root `pyproject.toml`.
That is ordinary git friction, not a gate concern, but the monorepo is what surfaces it at volume.

It does not make the parallel-cycle scheme of the earlier discussion exist. That remains a separate
piece of work on a separate axis; this ADR only removes the reason a monorepo could not be checked
at all.

## Test-first plan

`history` is the one check that must not be wrong: every other mechanism here lets something wrong
look right, and this one can make something *right* look wrong, in the check built to be the
boundary the agent cannot cross (this is the exact surface of defect #10, `test_history_exempt.py`).
So the change is driven entirely by failing tests, and the first commit adds them RED.

New suite `test_history_monorepo.py`, each test building a real throwaway git repo:

1. **Interleaved projects do not cross-credit.** One repo, commits alternating between project A and
   project B, each project internally RED-before-GREEN. `history A` and `history B` both pass.
   Against today's code this fails: B's code spends A's banked RED.
2. **Interleaved projects catch a real violation.** Same repo, but B commits code with no B test
   before it while A has a spare banked RED. `history B` must fail. Today it wrongly passes.
3. **One commit spanning two projects is split by path.** A single commit touches
   `projects/A/src/x.py` and `projects/B/test/y.py`. It banks a RED for B and spends a credit for A,
   not the reverse, and not both against one prefix.
4. **A shared-file commit is in no project's ledger.** A commit touching only the root lockfile or
   CI config is counted by neither `history A` nor `history B` — scaffolding is not in the ledger
   (ADR-0003), and that must hold at the repo root too.

Regression, unchanged behaviour that must survive:

5. **The `--repo` / single-project path is byte-for-byte unchanged.** The existing history tests
   (`test_history_exempt.py`, and the four real-project verdicts) pass without edit. If any real
   verdict moves, the change is wrong, not the project.

Only once all five are RED for the right reason does `history` change. `gate` attribution is a
second cycle with its own failing tests — a guarded write under `projects/A/` is judged against
A's state, and a write under `projects/B/` against B's, in one repo with one `.claude/`.

Shipped files change (`scripts/`, `templates/`), so the version bumps and `test_version_bump.py`
enforces it (ADR is docs — this ADR alone ships nothing).
