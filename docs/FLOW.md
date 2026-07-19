# How the harness works

Five diagrams, each traced from the code rather than from the design. Where the order of two
checks matters, the order here is the order in the source.

- [1. The gate](#1-the-gate--pretooluse) — the one mechanism everything else exists to support
- [2. One TDD cycle](#2-one-tdd-cycle) — how the gate opens and shuts
- [3. Closing a cycle](#3-closing-a-cycle) — the two refusals
- [4. Install and re-sync](#4-install-and-re-sync) — who owns which file
- [5. `init.sh`](#5-initsh) — the verification order, which is load-bearing

---

## 1. The gate — `PreToolUse`

Wired into `.claude/settings.json` as `python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" gate`,
matching `Write|Edit|MultiEdit|NotebookEdit`. **Exit 2 refuses the write; exit 0 allows it.**

Two orderings in here are deliberate and easy to get backwards:

- **The root is resolved from the file's path, not from `CLAUDE_PROJECT_DIR`.** A write inside a
  git worktree is judged by that worktree's config and state, which can legitimately differ from
  the main checkout's mid-cycle.
- **`HARNESS_GATE_BYPASS` is checked *after* the gate state.** When the gate is already OPEN the
  bypass never fires, so it cannot appear in a transcript except where it actually overrode a
  refusal.

```mermaid
flowchart TD
    A["Write / Edit / MultiEdit / NotebookEdit"] --> B{"stdin parses,<br/>and carries a file_path?"}
    B -- no --> Z0["exit 0 — nothing to judge"]
    B -- yes --> C["walk up from the file<br/>looking for .claude/scripts/harness.py"]
    C --> D{"found a harness root?"}
    D -- no --> Z1["exit 0 — outside any harness"]
    D -- yes --> E["read THAT root's harness.json<br/>guarded, exempt_names, exempt_patterns"]
    E --> F{"path matches a<br/>guarded pattern?"}
    F -- no --> Z2["exit 0 — not production code"]
    F -- yes --> G{"filename in<br/>exempt_names?"}
    G -- "yes — __init__.py" --> Z3["exit 0 — structural, no test can drive it"]
    G -- no --> H{"matches an<br/>exempt_pattern?"}
    H -- "yes — *.test.ts, __tests__/, vite.config.ts …" --> Z4["exit 0 — tests and config"]
    H -- no --> I["project = the captured group<br/>read .claude/state/PROJECT.json"]
    I --> J{"gate state?"}
    J -- OPEN --> Z5["exit 0 — a failing test is on record"]
    J -- SHUT --> K{"HARNESS_GATE_BYPASS=1?"}
    K -- yes --> Z6["exit 0<br/>+ a line on stderr, into the transcript"]
    K -- no --> X["exit 2 — REFUSED<br/>'no failing test is on record'"]

    style X fill:#7f1d1d,stroke:#ef4444,color:#fff
    style Z6 fill:#78350f,stroke:#f59e0b,color:#fff
```

---

## 2. One TDD cycle

`red` is the only thing that opens the gate, and it refuses four different ways — three of which
are a run that did not happen. That is the point: an infrastructure failure is not a failing test,
and accepting it would open the gate on a broken toolchain.

```mermaid
flowchart TD
    S(["cycle starts — gate SHUT"]) --> W1["write the failing test<br/>tests/ is never gated"]
    W1 --> R["harness.py red PROJECT tests/…"]
    R --> RR["run: cmd + red_args"]
    RR --> C1{"output contains<br/>no_tests_marker?"}
    C1 -- yes --> N1["NOT RED — no tests found"]
    C1 -- no --> C2{"exit code 0?"}
    C2 -- yes --> N2["NOT RED — 'a test that passes<br/>before the code exists proves nothing'"]
    C2 -- no --> C3{"exit == no_tests_exit?"}
    C3 -- "yes — e.g. pytest 5" --> N3["NOT RED — collected nothing"]
    C3 -- no --> C4{"exit in red_exit_codes?"}
    C4 -- "no — e.g. 127" --> N4["NOT RED — infrastructure failure,<br/>not a test failure"]
    C4 -- yes --> OPEN(["gate OPEN — production code writable"])

    N1 --> W1
    N2 --> W1
    N3 --> W1
    N4 --> W1

    OPEN --> CODE["write the minimum code<br/>the test demands"]
    CODE --> G["harness.py green PROJECT"]
    G --> GR["run: cmd + green_args"]
    GR --> C5{"exit 0?"}
    C5 -- no --> STILL["still RED — gate stays OPEN"]
    STILL --> CODE
    C5 -- yes --> SCRAPE["scrape coverage via coverage_re"]
    SCRAPE --> SHUT(["gate SHUT · coverage recorded"])
    SHUT --> Q["harness.py quality PROJECT<br/>linter, formatter, type checker<br/>from the runner's quality list"]
    Q --> REV["cycle-reviewer re-runs the gates itself"]
    REV --> D{"PASS?"}
    D -- REWORK --> CODE
    D -- yes --> DONE["harness.py cycle … done --evidence"]

    style OPEN fill:#14532d,stroke:#22c55e,color:#fff
    style SHUT fill:#1e3a5f,stroke:#3b82f6,color:#fff
```

> Every cycle lands as two commits — `[RED]` for the failing test, `[GREEN]` for the code. The git
> log is the evidence, and the ordering it shows is the thing the gate actually proves.

---

## 3. Closing a cycle

`done` is where the claim is made, so it is where both refusals live. Neither is in `green`:
coverage climbing toward the gate is the normal shape of a cycle, and refusing there would fight
the work rather than the claim.

```mermaid
flowchart TD
    A["harness.py cycle PROJECT ID done"] --> B{"--evidence given?"}
    B -- no --> R1["REFUSED — 'a done nobody can check<br/>is indistinguishable from a lie'"]
    B -- yes --> C{"recorded coverage<br/>below coverage_gate?"}
    C -- "yes — e.g. 71% vs 90%" --> R2["REFUSED — cover it, or change<br/>the gate deliberately"]
    C -- "no, or never measured" --> D["state = done<br/>evidence + verified_at recorded"]

    style R1 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style R2 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style D fill:#14532d,stroke:#22c55e,color:#fff
```

Unmeasured coverage does not block: cycle 0 is scaffolding and runs no suite, and the evidence
rule already stands between an unmeasured cycle and a silent close.

---

## 4. Install and re-sync

`/harness-init` is also the upgrade path. Every file it writes belongs to exactly one of two sets,
and the split is the whole design — a vendored copy the installer refuses to replace strands every
fix the plugin will ever ship, and an installer that replaces everything takes the constitution and
the cycle list with it.

```mermaid
flowchart TD
    I["/harness-init"] --> V{"file already exists?"}
    V -- no --> NEW["write it"]
    V -- yes --> OWN{"who owns it?"}

    OWN -- "HARNESS<br/>.claude/scripts/ · .claude/harness-tests/<br/>init.sh · docs/PLAYBOOK.md · shipped ADRs" --> RS["overwrite — every run<br/>reported as ~~ re-synced"]
    OWN -- "REPO<br/>CLAUDE.md · CONTEXT.md · harness.json<br/>.claude/cycles/ · docs/lessons/" --> KEEP["leave it — reported as ==<br/>--reset PATH overwrites one, named"]

    NEW --> STAMP
    RS --> STAMP
    KEEP --> STAMP["stamp .claude/.harness-version<br/>harness-owned, so it cannot go stale"]

    STAMP --> MERGE["settings.json — MERGE<br/>add or re-point the harness hooks,<br/>never touch anyone else's"]
    MERGE --> GI[".gitignore — APPEND or TOP UP<br/>entries added by a later version"]
    GI --> NOTE{"config predates<br/>the current schema?"}
    NOTE -- yes --> WARN["!! name every key being defaulted"]
    NOTE -- no --> END(["report, then ./init.sh"])
    WARN --> END

    style RS fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style KEEP fill:#14532d,stroke:#22c55e,color:#fff
```

A runner that shares a name with a shipped one inherits the keys it did not mention, so a config
written before `quality` existed still runs. Keys the config does name always win.

---

## 5. `init.sh`

The order is load-bearing. The gate self-test proves the one thing the harness exists for, and it
needs neither Docker nor `gh` — so the project's tooling is checked *after* it, next to the suites
that need it. Putting the project's prerequisites first meant a machine with no Docker daemon
aborted before the gate was ever verified.

```mermaid
flowchart TD
    A["./init.sh"] --> B["Harness prerequisites<br/>python3, uv — hardcoded, not from config"]
    B --> C["read the gate command out of<br/>.claude/settings.json"]
    C --> C1{"a PreToolUse<br/>gate hook exists?"}
    C1 -- no --> F1["FAIL — 'nothing is guarding this repo'"]
    C1 -- yes --> D["probe THAT command:<br/>every guarded path must be refused,<br/>tests/ · docs · config must be allowed,<br/>and a path inside a worktree must be refused"]
    D --> D1{"all probes as expected?"}
    D1 -- no --> F2["FAIL — the gate has stopped biting"]
    D1 -- yes --> E["dashboard renders"]
    E --> G["run the vendored harness suite<br/>unconditionally — a missing one is a broken install"]
    G --> H["Unproven completions<br/>done without evidence → FAIL<br/>done under coverage_gate → FAIL"]
    H --> I["Project tooling<br/>requires from harness.json:<br/>docker daemon, gh auth, node …"]
    I --> J["Project suites — one per cycle file<br/>harness.py quality + harness.py suite"]
    J --> K(["verified"])

    style F1 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style F2 fill:#7f1d1d,stroke:#ef4444,color:#fff
    style D fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style K fill:#14532d,stroke:#22c55e,color:#fff
```

`suite`, not `green`: `green` shuts the gate on success, so running the health check in the middle
of a RED cycle would revoke the write permission that cycle legitimately holds.

---

## What drives what

Nothing outside `.claude/harness.json` names a tool. That is the property that lets a project on
poetry, pyright, golangci-lint or cargo change one file and nothing else.

```mermaid
flowchart LR
    CFG[".claude/harness.json"]
    CYC[".claude/cycles/PROJECT.json"]

    CFG --> G1["gate — guarded, exempt_*"]
    CFG --> G2["red / green / suite — runners[].cmd, args, exit codes"]
    CFG --> G3["quality — runners[].quality, {writable}"]
    CFG --> G4["init.sh probes — generated from guarded"]
    CFG --> G5["init.sh project tooling — requires"]
    CFG --> G6["the agents' stack — stack"]
    CFG --> G7["link_projects.sh — owner, projects_dir"]

    CYC --> C1["which runner this project uses"]
    CYC --> C2["coverage_gate — enforced at done"]
    CYC --> C3["the ordered cycle list"]

    style CFG fill:#1e3a5f,stroke:#3b82f6,color:#fff
    style CYC fill:#1e3a5f,stroke:#3b82f6,color:#fff
```
