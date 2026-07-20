#!/usr/bin/env python3
"""TDD harness: mechanical RED gate, cycle tracking, per-subagent token accounting.

Domain-agnostic. Everything project-specific — which repos exist, which test runner each one
uses, which paths count as production code — is read from `.claude/harness.json`. See
`harness_config()` for the schema and the defaults applied when the file is absent.

Subcommands
    gate      PreToolUse hook. Blocks edits to production code while the RED gate is shut.
    red       Run a test, require it to fail, open the gate for one project.
    green     Run the full suite; on pass, shut the gate and record coverage.
    quality   Run the project's linter/formatter/type-checker, as declared by its runner.
    suite     Run the project's tests read-only: no gate change, no coverage recorded.
    history   Check a project's git log for test-before-code ordering. Built to run in CI.
    cycle     Move a TDD cycle between states. `done` requires --evidence.
    status    Render the dashboard to stdout and to PROGRESS.md.
    stats     Attribute token usage to each subagent from the session transcript.
    handoff   Write HANDOFF.md: next action, blockers, where each project stands.
    lessons   One line per live lesson. Read this, then open only the ones that apply.
    adrs      One line per accepted ADR. Superseded ones are history, not guidance.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# stdout AND stderr. stderr is not an afterthought here: it is the PreToolUse contract — `deny()`
# writes the block reason there and exits 2 — and it carries every error message this script has.
# A Windows console inherits a legacy codepage (cp874 here); a path or a message with one character
# outside it raises UnicodeEncodeError, the process dies with a code that is not 2, and a write
# that should have been refused is not. The gate failing OPEN over a filename is the worst outcome
# this file has. POSIX is already UTF-8, so this is a no-op there.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE_DIR = ROOT / ".claude" / "state"
CYCLE_DIR = ROOT / ".claude" / "cycles"
CONFIG_PATH = ROOT / ".claude" / "harness.json"

# Applied when `.claude/harness.json` is absent or omits a key. These reproduce the behaviour the
# harness shipped with before it was made configurable, so an un-configured repo still gets a
# working gate for a Python/`app/` or a JS/`src/` layout.
DEFAULT_CONFIG: dict = {
    "projects_dir": "projects",
    # What the projects are built with. Declared once, here, because four different places used
    # to each name a stack in prose and only one of them was the one the implementer obeyed.
    "stack": "TODO: declare the stack in .claude/harness.json",
    # Production code. Touching it requires a failing test on record. A trailing "/" means "this
    # directory and everything under it"; anything else is an exact filename.
    #
    # `alembic/env.py` is named exactly rather than gating `alembic/`, because it is what
    # `alembic upgrade head` executes on every container boot — it ships, and it encodes
    # behaviour. `alembic.ini` beside it is config and cannot be driven by a test.
    "guarded": ["app/", "src/", "alembic/versions/", "alembic/env.py"],
    # The harness's own machinery, relative to the harness root. Refused ALWAYS — an open gate
    # does not open these, because they are what an open gate is made of.
    #
    # The line between this and the config a user edits freely is auditability, not importance.
    # `.claude/state/` is gitignored, so flipping it to OPEN by hand leaves no trace anywhere; the
    # gate hook and the wiring in settings.json are how the refusal happens at all, and a quiet
    # edit to either turns every later green run into a claim about nothing. Those are invisible
    # or self-referential, so they are shut.
    #
    # `.claude/cycles/` and `.claude/harness.json` are deliberately NOT here. Lifting a brief's
    # cycle list into the cycle file is step two of installing the harness, and both files are
    # committed — lowering a coverage_gate to dodge it is a visible act in a reviewable diff.
    # Blocking what is auditable would buy nothing and break the documented workflow.
    "protected": [".claude/state/", ".claude/scripts/", ".claude/settings.json"],
    # Structural files that cannot be driven by a test. Creating them is not "production code".
    "exempt_names": ["__init__.py"],
    # Test/setup/config/type-decl files. Never production code; never gated, regardless of path —
    # a write to src/components/Foo.test.tsx is not held to the RED-first rule.
    "exempt_patterns": [
        r"\.(test|spec)\.[tj]sx?$",
        r"(?:^|/)__tests__/",
        r"(?:^|/)(?:vite|vitest|tailwind|postcss|eslint)\.config\.[tj]s$",
        r"(?:^|/)setupTests\.[tj]sx?$",
        r"\.d\.ts$",
    ],
    "runners": {
        "pytest": {
            "cmd": ["uv", "run", "pytest"],
            "red_args": ["-q"],
            "green_args": ["--cov", "--cov-report=term"],
            # 1 = assertions failed, 2 = collection error (e.g. ImportError). Both are honest RED.
            "red_exit_codes": [1, 2],
            "no_tests_exit": 5,
            "coverage_re": r"^TOTAL\s+.*?(\d+)%",
            "coverage_multiline": True,
            "writable_hint": "app/",
            # The quality gates, as argv lists run in the project directory. `{writable}` expands
            # to writable_hint. These live in the config for the same reason the test command
            # does: a repo on poetry, pylint or pyright is not a different harness, it is a
            # different four lines. Naming them in an agent prompt made them unreachable.
            "quality": [
                ["uv", "run", "ruff", "check", "."],
                ["uv", "run", "ruff", "format", "--check", "."],
                ["uv", "run", "mypy", "--strict", "{writable}"],
            ],
        },
        "vitest": {
            "cmd": ["npx", "vitest", "run"],
            "red_args": [],
            "green_args": ["--coverage"],
            # vitest exits 1 on a genuine test failure (failed assertion, or a test file that will
            # not import/compile). Any other code — 127 = npx/vitest missing, 2 = usage error — is
            # the run never happening, which is not an honest RED.
            "red_exit_codes": [1],
            # vitest has no dedicated exit code for "nothing collected", so match the message.
            "no_tests_marker": "no test files",
            # The text reporter colours the summary, so `All files` arrives behind ANSI escapes.
            "coverage_re": r"All files\s*\|\s*([\d.]+)",
            "strip_ansi": True,
            "writable_hint": "src/",
            "quality": [
                ["npm", "run", "typecheck"],
                ["npm", "run", "lint"],
            ],
        },
    },
}


def harness_config() -> dict:
    """`.claude/harness.json`, shallow-merged over DEFAULT_CONFIG.

    Shallow on purpose: naming `guarded` or `runners` in the file replaces that key outright
    rather than merging into it. Half-overriding the guarded set is how you end up with a gate
    that silently protects less than the config appears to say.
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass  # No config, or an unreadable one: the defaults still make a working gate.
    return cfg


def guarded_patterns(cfg: dict | None = None) -> tuple[re.Pattern, ...]:
    """Compile the guarded-path globs into project-capturing regexes.

    Matched anywhere in the path, never anchored at the root. A git worktree sits at
    `.claude/worktrees/<name>/projects/...`, which is *inside* the harness root — an anchored
    `^projects/` silently matches nothing there, and the gate stops guarding anything at all.
    """
    cfg = cfg or harness_config()
    base = re.escape(cfg["projects_dir"].strip("/"))
    out = []
    for entry in cfg["guarded"]:
        body = re.escape(entry.strip("/"))
        suffix = "/" if entry.endswith("/") else "$"
        out.append(re.compile(rf"(?:^|/){base}/(?P<project>[^/]+)/{body}{suffix}"))
    return tuple(out)


def runner_spec(project: str) -> dict:
    """The runner definition for one project, by the name declared in its cycle file.

    A runner the config does not define is fatal rather than silently falling back to pytest:
    a typo'd runner name must not quietly gate a JS project with a Python test command.
    """
    cfg = harness_config()
    name = runner_for(project)
    try:
        spec = cfg["runners"][name]
    except KeyError:
        sys.exit(f"harness: project '{project}' declares runner '{name}', which .claude/harness.json does not define.")

    # A runner that shares a name with a shipped one inherits the keys it did not mention. This is
    # the one place the "naming a key replaces it outright" rule is relaxed, and the reason is a
    # schema that grows: `quality` was added in 0.3.0, but `.claude/harness.json` is repo-owned and
    # is deliberately never rewritten by a re-sync. Without this, upgrading a repo installed on an
    # earlier version delivered a new harness.py that demanded a key the repo's config could not
    # have had, and `init.sh` failed on every project. Keys the user did name still win outright —
    # this can only fill a gap, never soften a decision they made.
    default = DEFAULT_CONFIG["runners"].get(name)
    if default:
        spec = {**default, **spec}

    # Fail on the config, not with a traceback three frames into cmd_red. A half-written runner
    # is a likely thing to hand-edit into harness.json, and the message has to say which key.
    missing = [k for k in ("cmd", "red_exit_codes") if k not in spec]
    if missing:
        sys.exit(f"harness: runner '{name}' in .claude/harness.json is missing required key(s): {', '.join(missing)}.")
    return spec


# ---------------------------------------------------------------- state


def state_path(project: str) -> Path:
    return STATE_DIR / f"{project}.json"


def _queued_row(c: dict) -> dict:
    return {"id": c["id"], "title": c["title"], "state": "queued", "agent": "-", "tokens": 0, "evidence": ""}


def load_state(project: str) -> dict:
    """Seed from the cycle file, then reconcile against it on *every* load.

    Seeding only on first write was the shape that made the board lie without anyone touching it:
    once the state file existed it was returned verbatim, so a cycle appended to the cycle file
    afterwards had no row, could not be marked anything, and rendered nowhere -- while the board
    read `n/n` with every row accounted for. Nothing looked wrong, which is the expensive kind of
    wrong.

    So this is a left join, not a replace. Rows already in the state win on every field, because
    they carry what happened; the cycle file only decides which ids exist and supplies titles for
    ids the state has never seen. An id the state holds and the cycle file no longer defines is
    flagged `orphan` rather than dropped -- it may carry evidence of work that really happened, and
    deleting a record to make two files agree is the same lie in the other direction.
    """
    seed = CYCLE_DIR / f"{project}.json"
    declared = []
    if seed.exists():
        try:
            declared = json.loads(seed.read_text(encoding="utf-8"))["cycles"]
        except (json.JSONDecodeError, KeyError, OSError):
            declared = []

    p = state_path(project)
    if not p.exists():
        return {
            "project": project,
            "gate": {"state": "SHUT"},
            "coverage": None,
            "cycles": [_queued_row(c) for c in declared],
        }

    state = json.loads(p.read_text(encoding="utf-8"))
    if not seed.exists():
        # No declaration to reconcile against. Absence of the cycle file is not evidence that every
        # cycle was withdrawn, so flag nothing.
        return state

    by_id = {str(c["id"]): c for c in state.get("cycles") or []}
    declared_ids = {str(c["id"]) for c in declared}
    reconciled = []
    for c in declared:
        row = by_id.get(str(c["id"]))
        if row is None:
            reconciled.append(_queued_row(c))
            continue
        row.pop("orphan", None)  # re-declared: it is a normal cycle again
        row.setdefault("title", c["title"])
        reconciled.append(row)

    # Keep orphans, in their original relative order, after the declared ones.
    for row in state.get("cycles") or []:
        if str(row["id"]) not in declared_ids:
            row["orphan"] = True
            reconciled.append(row)

    state["cycles"] = reconciled
    return state


def save_state(project: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(project).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def known_projects() -> list[str]:
    """In build order (ADR-0007), not alphabetical. The dashboard and the handoff both read as a plan."""

    def rank(p: Path) -> tuple[int, str]:
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get("build_order", 99), p.stem)
        except (json.JSONDecodeError, OSError):
            return (99, p.stem)

    return [p.stem for p in sorted(CYCLE_DIR.glob("*.json"), key=rank) if not p.name.startswith(".")]


def project_dir(project: str) -> Path:
    # `projects_dir` is configurable everywhere else — the gate patterns, init.sh, link_projects.sh.
    # Hardcoding it here meant a repo that set it to anything else got a gate that guarded the
    # configured directory and a runner that looked in a directory that did not exist.
    return ROOT / harness_config()["projects_dir"] / project


def project_config(project: str) -> dict:
    p = CYCLE_DIR / f"{project}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def runner_for(project: str) -> str:
    return project_config(project).get("runner", "pytest")


# ---------------------------------------------------------------- gate (PreToolUse hook)


def record_touch(project: str, rel: str) -> None:
    """Note that a guarded file was written while this project's gate was open.

    The gate is per-project, not per-file: one failing test opens every guarded path until green.
    Narrowing that to the files a test covers would need language-specific import analysis, cannot
    see the code that does not exist yet at RED time, and would block honest multi-file cycles and
    every refactor — so the answer here is to make the breadth *visible* instead of refusing it.
    A cycle that opened on one test and touched fourteen files is a fact a reviewer can act on.

    Bookkeeping only, and never allowed to cost a write: if the state file is busy or malformed the
    write still goes through, because a gate that fails on its own note-taking is worse than a gate
    with a gap in its notes. Two subagents writing at once can lose an entry for the same reason.
    """
    try:
        state = load_state(project)
        gate = state.get("gate", {})
        if gate.get("state") != "OPEN":
            return
        touched = gate.setdefault("touched", [])
        if rel not in touched:
            touched.append(rel)
            save_state(project, state)
    except Exception:
        pass


def deny(msg: str) -> None:
    """Exit 2 with stderr: the PreToolUse contract for blocking a tool call."""
    print(msg, file=sys.stderr)
    sys.exit(2)


def harness_root_for(path: Path) -> Path | None:
    """The harness that owns this file, found by walking up to `.claude/scripts/harness.py`.

    Derived from the path, not from CLAUDE_PROJECT_DIR, so that a write inside a worktree is
    judged against that worktree's state rather than the main checkout's.
    """
    for parent in path.parents:
        if (parent / ".claude" / "scripts" / "harness.py").is_file():
            return parent
    return None


def cmd_gate() -> None:
    global STATE_DIR, CYCLE_DIR, CONFIG_PATH

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # Nothing to judge; never wedge the session on a malformed payload.

    raw = payload.get("tool_input", {}).get("file_path")
    if not raw:
        sys.exit(0)

    target = Path(raw).resolve()
    rel = target.as_posix()

    root = harness_root_for(target)
    if root is None:
        sys.exit(0)  # Outside any harness. Not ours to police.

    # Rebind to the harness that owns this file before reading any config. A write inside a
    # worktree must be judged by that worktree's guarded set and its state, not the main
    # checkout's — the two can legitimately differ mid-cycle.
    STATE_DIR = root / ".claude" / "state"
    CYCLE_DIR = root / ".claude" / "cycles"
    CONFIG_PATH = root / ".claude" / "harness.json"
    cfg = harness_config()

    # The harness's own machinery, checked before anything else and answerable to no gate state.
    # Every `guarded` pattern is forced to start with `<projects_dir>/<project>/`, so nothing at
    # the harness root could ever be expressed there however the config was written — which left
    # `.claude/state/` writable, and writing `{"gate": {"state": "OPEN"}}` into it opened the gate
    # with no test ever run and no trace in git, because that directory is gitignored.
    #
    # HARNESS_GATE_BYPASS still overrides, deliberately: the point is not that this is unopenable,
    # it is that opening it says so out loud instead of looking like nothing happened.
    inside = target.relative_to(root).as_posix()
    if any(inside.startswith(p) if p.endswith("/") else inside == p for p in cfg["protected"]):
        if os.environ.get("HARNESS_GATE_BYPASS") == "1":
            print(f"harness: PROTECTED path written under bypass: {inside}", file=sys.stderr)
            sys.exit(0)
        deny(
            f"BLOCKED: {inside} is part of the harness itself.\n"
            "  Gate state, the gate script and the hook wiring are refused whatever the gate says —\n"
            "  a mechanism that can rewrite its own verdict is not a mechanism.\n"
            "  Cycle files and harness.json are yours to edit; they are committed and reviewable."
        )

    match = next((m for g in guarded_patterns(cfg) if (m := g.search(rel))), None)
    if not match:
        sys.exit(0)

    if target.name in set(cfg["exempt_names"]):
        sys.exit(0)
    if any(re.search(p, rel) for p in cfg["exempt_patterns"]):
        sys.exit(0)

    # A self-test whose result nothing consults is a report. `init.sh` writes its verdict here and
    # a SessionStart hook runs it, so a harness that has stopped enforcing stops being written
    # against — fail closed, not fail quiet.
    #
    # AFTER the exemptions, and that placement is the whole difference between a brake and a brick.
    # Checked before them, a failed verdict also refused tests and config — so the self-test's own
    # "gate allows tests/" probe returned 2, the verdict could never flip back to true, and the
    # repo was unrepairable by the exact edits repairing it requires. Whatever is refused here, the
    # way out has to stay open.
    #
    # Only a recorded FAILURE refuses. A missing verdict means never verified, not verified-bad,
    # and refusing there would brick a fresh clone before its first init.sh. And the honest limit:
    # this catches the gate BREAKING, not someone determined to evade it — the verdict is a file,
    # and while `protected` keeps the Write tool off it, nothing here gates `rm`.
    verdict = STATE_DIR / ".selftest.json"
    try:
        if verdict.is_file() and json.loads(verdict.read_text(encoding="utf-8")).get("gate_verified") is False:
            deny(
                f"BLOCKED: {inside}\n"
                "  The last ./init.sh could not verify this harness, so it is refusing production\n"
                "  code until someone looks. Run ./init.sh and fix what it reports.\n"
                "  Tests and config stay writable — that is how you repair it."
            )
    except (OSError, json.JSONDecodeError):
        pass  # An unreadable verdict is not a failed one; never wedge a session on bookkeeping.

    project = match.group("project")
    rel = target.relative_to(root).as_posix()
    gate = load_state(project)["gate"]

    if gate.get("state") == "OPEN":
        record_touch(project, rel)
        sys.exit(0)

    if os.environ.get("HARNESS_GATE_BYPASS") == "1":
        print(f"harness: gate bypassed for {rel} (HARNESS_GATE_BYPASS=1)", file=sys.stderr)
        sys.exit(0)

    deny(
        f"BLOCKED by TDD gate: {rel}\n"
        f"  Gate for '{project}' is SHUT. No failing test is on record.\n"
        f"  Write the test first, then open the gate:\n"
        f"      python3 .claude/scripts/harness.py red {project} <test-path>\n"
        f"  Tests, __init__.py, config and CI files are never blocked."
    )


# ---------------------------------------------------------------- red / green


# `text=True` decodes a child's output with the *locale* codec. On a Windows console that is a
# legacy codepage (cp874 here), and both runners emit box-drawing and tick glyphs it cannot decode:
# the decode raises, stdout and stderr come back None, and the caller dies on `None + None` before
# it ever sees whether the test failed. Name the encoding instead — POSIX is already UTF-8, so this
# changes nothing there — and never let an undecodable byte in a test report take the harness down.
DECODE = {"encoding": "utf-8", "errors": "replace"}


def run_suite(project: str, spec: dict, args: list[str]) -> tuple[int, str]:
    """Invoke a runner in the project directory and echo everything it printed."""
    cmd = list(spec["cmd"])
    # On Windows an npm shim is `npx.cmd`, and subprocess without a shell will not find a bare
    # "npx" — it raises FileNotFoundError, which cmd_red reads as an infrastructure failure and
    # the gate never opens. shutil.which resolves the real executable on every platform (it
    # honours PATHEXT on Windows, returns the plain path on POSIX), so this stays correct on macOS.
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    proc = subprocess.run([*cmd, *args], cwd=project_dir(project), capture_output=True, **DECODE)
    out = proc.stdout + proc.stderr
    print(out)
    return proc.returncode, out


TEST_PATH = re.compile(r"(?:^|/)tests?/|[._](?:test|spec)\.[a-z]+$|(?:^|/)__tests__/")


def cmd_history(project: str, repo: Path | None = None) -> None:
    """Check the git history for the ordering the gate exists to produce: test first, then code.

    Everything else in this harness runs on the machine the agent runs on and reads state the
    agent can reach, which makes it evidence about a cooperative agent rather than a boundary.
    This is the one check meant to run somewhere else — in CI, where the agent writes the code but
    not the verdict — and it reads only `git log`, which the harness never writes.

    The rule is the harness's own model, counted rather than asserted. Walking oldest to newest, a
    commit touching tests banks a RED and a commit touching guarded code spends one; code that
    arrives with nothing banked is the violation. Commits touching neither — scaffolding, docs, CI
    — are not in the ledger at all.

    A commit carrying tests and code together banks and immediately spends, which is deliberate.
    It is the one shape this cannot judge, because the ordering it exists to prove happened inside
    a single commit where git cannot see it. PLAYBOOK asks for two commits per cycle for exactly
    that reason, and a repo that squashes gets a weaker check rather than a false accusation.
    """
    # `--repo` points at a checkout directly, which is how this runs in CI: the project repo is
    # its own root there, not a directory under the harness. The guarded patterns are matched
    # against a synthesised `projects/<name>/` prefix either way, so the same config decides what
    # counts as production code in both places.
    d = repo or project_dir(project)
    if not (d / ".git").is_dir():
        sys.exit(f"harness: no git history at {d} — nothing to check.")

    cfg = harness_config()
    patterns = guarded_patterns(cfg)
    prefix = f"{cfg['projects_dir']}/{project}/"

    proc = subprocess.run(
        ["git", "-C", str(d), "log", "--reverse", "--no-merges", "--format=%H%x1f%s", "--name-only"],
        capture_output=True, **DECODE,
    )
    log = proc.stdout.strip()
    if not log:
        sys.exit(f"harness: {project} has no commits.")

    banked, violations, counted = 0, [], 0
    sha = subject = ""
    files: list[str] = []

    def settle() -> None:
        nonlocal banked, counted
        if not sha:
            return
        code = any(any(p.search(prefix + f) for p in patterns) for f in files)
        tests = any(TEST_PATH.search(f) for f in files)
        if not (code or tests):
            return
        counted += 1
        if tests:
            banked += 1
        if code:
            if banked:
                banked -= 1
            else:
                violations.append(f"{sha[:8]} {subject}")

    for line in log.splitlines():
        if "\x1f" in line:
            settle()
            sha, subject = line.split("\x1f", 1)
            files = []
        elif line.strip():
            files.append(line.strip())
    settle()

    if violations:
        print(f"harness: {project} — production code committed with no test commit before it:\n")
        for v in violations:
            print(f"  {v}")
        print(
            f"\n  {len(violations)} of {counted} relevant commits. The gate proves this ordering "
            "locally;\n  this proves it survived into the history, where it can be reviewed."
        )
        sys.exit(1)
    print(f"harness: {project} — {counted} commits, every code commit preceded by a test commit.")


def cmd_quality(project: str) -> None:
    """Run the project's quality gates, in order, stopping at the first failure.

    These used to be prose. `init.sh` named `uv run ruff` and `uv run mypy --strict app/`, the
    implementer agent named them again, the reviewer agent named them a third time, and the
    auditor a fourth — four copies of one fact, none of them reachable by a repo that lints with
    anything else. Naming them here means a project on poetry, pylint or pyright changes its
    config and nothing else, exactly as `red` and `green` already worked.
    """
    spec = runner_spec(project)
    commands = spec.get("quality")
    if not commands:
        # Not a skip. A repo whose quality gates cannot be found is a repo running none of them,
        # and it looks identical in the output to one that passed them all.
        sys.exit(
            f"harness: runner '{runner_for(project)}' in .claude/harness.json defines no \"quality\" "
            "commands, so the quality gates are not being run at all. Add them — see the "
            "'Adding a runner' section of docs/PLAYBOOK.md — or state plainly that this project "
            "has none."
        )

    guarded = spec.get("writable_hint", ".")
    for raw in commands:
        cmd = [str(part).replace("{writable}", guarded) for part in raw]
        printable = " ".join(cmd)
        print(f"--- {printable}")
        resolved = list(cmd)
        # Same reason as run_suite: a bare `npm` is `npm.cmd` on Windows and subprocess without a
        # shell will not find it.
        resolved[0] = shutil.which(resolved[0]) or resolved[0]
        proc = subprocess.run(resolved, cwd=project_dir(project), capture_output=True, **DECODE)
        print(proc.stdout + proc.stderr)
        if proc.returncode != 0:
            sys.exit(f"harness: quality gate failed for {project}: {printable} (exit {proc.returncode})")
    n = len(commands)
    print(f"harness: quality gates pass for {project} ({n} check{'' if n == 1 else 's'}).")


def cmd_suite(project: str) -> None:
    """Run the project's tests and report, without touching gate state or coverage.

    `green` is the wrong command for a health check: it shuts the gate on success. Calling it
    from `init.sh` would mean that running the one verification entrypoint in the middle of a RED
    cycle silently closed the gate the cycle had legitimately opened, and the implementer's next
    write would be refused for no reason it could see.
    """
    spec = runner_spec(project)
    code, _ = run_suite(project, spec, spec.get("green_args", []))
    if code == 0:
        return
    # Before the first cycle there is genuinely nothing to collect, and that is the correct state
    # for a freshly scaffolded project rather than a failure.
    if code == spec.get("no_tests_exit"):
        print(f"harness: no tests in {project} yet.")
        return
    sys.exit(f"harness: suite failed for {project} (exit {code}).")


def scrape_coverage(spec: dict, out: str) -> int | None:
    pattern = spec.get("coverage_re")
    if not pattern:
        return None
    if spec.get("strip_ansi"):
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    flags = re.MULTILINE if spec.get("coverage_multiline") else 0
    if m := re.search(pattern, out, flags):
        return int(round(float(m.group(1))))
    return None


def cmd_red(project: str, test_args: list[str]) -> None:
    if not test_args:
        sys.exit("usage: harness.py red <project> <test-path> [runner args]")

    spec = runner_spec(project)
    runner = runner_for(project)
    code, out = run_suite(project, spec, [*test_args, *spec.get("red_args", [])])

    marker = spec.get("no_tests_marker")
    if marker and marker.lower() in out.lower():
        sys.exit(f"NOT RED: {runner} found no tests. Write the test first.")
    if code == 0:
        sys.exit("NOT RED: the test passed. A test that passes before the code exists proves nothing.")
    if code == spec.get("no_tests_exit"):
        sys.exit(f"NOT RED: {runner} collected no tests. Write the test first.")
    if code not in set(spec["red_exit_codes"]):
        # The run never happened. A missing runner or a usage error is not a failing test, and
        # accepting it here would open the gate on an infrastructure problem.
        sys.exit(f"NOT RED: {runner} exited {code} (infrastructure failure, not a test failure).")

    state = load_state(project)
    state["gate"] = {"state": "OPEN", "test": test_args, "opened_at": time.time(), "exit_code": code}
    save_state(project, state)
    hint = spec.get("writable_hint", "production code")
    print(f"\nRED confirmed (exit {code}). Gate OPEN for '{project}' — {hint} is writable until green.")


def cmd_green(project: str) -> None:
    spec = runner_spec(project)
    code, out = run_suite(project, spec, list(spec.get("green_args", [])))
    if code != 0:
        sys.exit(f"\nStill RED (exit {code}). Gate stays OPEN. Keep going.")

    coverage = scrape_coverage(spec, out)
    state = load_state(project)
    # Carry the test that opened this gate past the close. `done` asks git whether it was ever
    # committed, and without this the only record of which test justified the cycle is erased by
    # the very command that ends it.
    opened_with = state.get("gate", {}).get("test") or state.get("last_red_test")
    touched = state.get("gate", {}).get("touched") or []
    state["gate"] = {"state": "SHUT", "closed_at": time.time()}
    if opened_with:
        state["last_red_test"] = opened_with
    if touched:
        state["last_touched"] = touched
    state["coverage"] = coverage
    save_state(project, state)
    print(f"\nGREEN. Gate SHUT for '{project}'. Coverage: {coverage if coverage is not None else '?'}%")


# ---------------------------------------------------------------- cycles


# Hex-looking words are the cost of this check: a token of 7+ hex characters is a SHA by shape
# alone, and English has a few ("defaced", "decade5"). Requiring at least one letter drops the far
# more common false positive — a bare 7-digit number, which in an evidence string is a token count
# or a duration, never a commit. Anything caught wrongly is repaired by rewording the evidence,
# which is cheap; anything let through wrongly is a `done` nobody can check, which is not.
SHA_SHAPED = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


def _refuse_unresolvable_shas(project: str, cycle_id: str, evidence: str) -> None:
    shas = list(dict.fromkeys(SHA_SHAPED.findall(evidence.lower())))
    if not shas:
        return

    d = project_dir(project)
    if not (d / ".git").is_dir():
        sys.exit(
            f"REFUSED: cycle {cycle_id}'s evidence names commits ({', '.join(shas)}), "
            f"and {project} has no git history to name them in.\n"
            f"  Commit the failing test and the code, then close the cycle."
        )

    unresolved = [
        s
        for s in shas
        if subprocess.run(
            ["git", "-C", str(d), "cat-file", "-t", s], capture_output=True, **DECODE
        ).returncode
        != 0
    ]
    if unresolved:
        sys.exit(
            f"REFUSED: cycle {cycle_id}'s evidence cites {', '.join(unresolved)}, "
            f"which resolve to nothing in {project}.\n"
            f"  Evidence is the whole argument that this cycle happened; a SHA that does not exist "
            f"makes it unreadable rather than merely unproven.\n"
            f"  Cite the commits git actually has — `git log --oneline` in {d}."
        )


def cmd_cycle(project: str, cycle_id: str, new_state: str, agent: str | None, evidence: str | None) -> None:
    valid = {"queued", "red", "green", "done", "blocked"}
    if new_state not in valid:
        sys.exit(f"state must be one of {sorted(valid)}")

    # A `done` with no evidence is indistinguishable from a lie. Refuse it.
    if new_state == "done" and not evidence:
        sys.exit(
            f"REFUSED: cycle {cycle_id} cannot be 'done' without evidence.\n"
            f"  Pass what actually ran and what it printed:\n"
            f'      harness.py cycle {project} {cycle_id} done --evidence "<runner> 24 passed, '
            f'cov 93%; quality gates clean; a1b2c3d [RED] -> e4f5g6h [GREEN]"'
        )

    # Presence was the whole check, and any non-empty string satisfied it: `--evidence "yes"` closed
    # a cycle, and so did two SHAs that exist in no repository. Evidence names commits, so resolve
    # them. This does not make the cited commits the *right* ones — nothing here can — but it moves
    # a fabricated SHA from undetectable to impossible, and it does so by asking git, the one store
    # in this system the harness does not write and an agent cannot quietly amend.
    #
    # Refused here, before the state is loaded and long before anything is written, so a rejected
    # `done` leaves nothing half-applied (lesson 0013).
    if new_state == "done" and evidence:
        _refuse_unresolvable_shas(project, cycle_id, evidence)

    state = load_state(project)

    # And a `done` under the coverage gate is the same lie with a number on it. `coverage_gate`
    # was written into every cycle file by the installer, validated to 0-100, quoted in the
    # PLAYBOOK's definition of done and in the reconcile-auditor's checklist — and read by nothing
    # that could refuse. In a harness whose whole argument is that a hook exiting 2 is a constraint
    # and a paragraph is a prior, that made it a prior wearing a constraint's name.
    #
    # Checked here rather than in `green`, alongside the evidence rule and for the same reason:
    # `green` runs many times inside a cycle and coverage climbing to the gate is the normal shape
    # of the work. `done` is where the claim is made.
    # The evidence rule asks for two commit SHAs and then believes whatever it is handed. This
    # asks git instead: the test that opened the gate has to exist in the project's history.
    #
    # It does not make the test a good one — nothing here can — but it moves the cost of a fake
    # `assert False` from "one line, invisible" to "one line, committed, and sitting in the diff a
    # reviewer reads". Git is the one store in this system the harness does not write and an agent
    # cannot quietly amend without it showing.
    if new_state == "done":
        recorded = load_state(project).get("last_red_test") or []
        test_path = next((str(a) for a in recorded if not str(a).startswith("-")), None)

        # A check that only runs when the data is present is a check anyone can skip by removing
        # the data. Hand-written state that simply omitted `last_red_test` closed a cycle with no
        # refusal at all — the same fail-open-on-missing shape as every other bug this harness has
        # had. The cycle file says whether this cycle was ever supposed to have a test, and that
        # file is the user's own declaration rather than something `red` writes.
        declared = next(
            (c for c in (project_config(project).get("cycles") or []) if str(c.get("id")) == str(cycle_id)),
            {},
        )
        if declared.get("first_test") and not test_path:
            sys.exit(
                f"REFUSED: cycle {cycle_id} declares a first test ({declared['first_test']}), "
                f"and no RED run is on record for {project}.\n"
                f"  Either the gate was never opened for this cycle, or the state saying so was "
                f"replaced.\n"
                f"  Run `red`, or set the cycle's first_test to null if it genuinely has no test."
            )

        if test_path:
            if not (project_dir(project) / ".git").is_dir():
                sys.exit(
                    f"REFUSED: cycle {cycle_id} claims evidence, but {project} has no git history.\n"
                    f"  Evidence names a [RED] and a [GREEN] commit; there are none to name.\n"
                    f"  Commit the failing test and the code, then close the cycle."
                )
            if not git(project, "log", "--all", "--format=%H", "--", test_path):
                sys.exit(
                    f"REFUSED: cycle {cycle_id} opened the gate with {test_path}, "
                    f"and no commit in {project} touches it.\n"
                    f"  The test that justified writing the code was never committed, so the "
                    f"history does not show the order the gate exists to prove.\n"
                    f"  Commit it — `test(...): … [RED]` — then close the cycle."
                )

    if new_state == "done":
        gate = project_config(project).get("coverage_gate")
        actual = state.get("coverage")
        if gate is not None and actual is not None and actual < gate:
            sys.exit(
                f"REFUSED: cycle {cycle_id} cannot be 'done' at {actual}% coverage; "
                f"{project}'s gate is {gate}%.\n"
                f"  Cover the behaviour this cycle added, re-run green, then close it.\n"
                f"  If the gate itself is wrong, change it in .claude/cycles/{project}.json "
                f"deliberately — do not route around it here."
            )

    for c in state["cycles"]:
        if str(c["id"]) == str(cycle_id):
            c["state"] = new_state
            if agent:
                c["agent"] = agent
            if evidence:
                c["evidence"] = evidence
                c["verified_at"] = time.strftime("%Y-%m-%d %H:%M")
                # Recorded on the cycle, not left in state: the next `red` replaces the gate and
                # the breadth of this cycle would be gone by the time anyone reviewed it.
                if new_state == "done":
                    c["touched"] = list(state.get("last_touched") or [])
            save_state(project, state)
            print(f"cycle {cycle_id} -> {new_state}")
            return
    sys.exit(f"no cycle {cycle_id} in {project}")


# ---------------------------------------------------------------- token accounting


def find_transcript(hook_payload: dict | None = None) -> Path | None:
    """Locate this session's transcript.

    A hook is handed `transcript_path` outright — always trust it. Falling back to a filesystem
    search is for the command line, and it has to cope with worktrees: entering one moves the
    transcript to a slug dir derived from the worktree path, not the project root. So match every
    dir whose name starts with the project slug and take the most recently written log across them.
    """
    if hook_payload and (tp := hook_payload.get("transcript_path")):
        return Path(tp)
    if tp := os.environ.get("CLAUDE_TRANSCRIPT_PATH"):
        return Path(tp)

    # Claude Code slugs the project path by replacing both separators and underscores with dashes.
    # On Windows that also means the backslash separator and the drive colon: `D:\a\b_c` becomes
    # `D--a-b-c`. Leaving them in yields a slug that is still an absolute path, and `Path.glob`
    # rejects it outright ("Non-relative patterns are unsupported").
    slug = (
        str(ROOT).replace("\\", "-").replace("/", "-").replace(":", "-").replace("_", "-")
    )
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return None

    logs = [log for d in base.glob(f"{slug}*") if d.is_dir() for log in d.glob("*.jsonl")]
    if not logs:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime)


def billable(usage: dict) -> int:
    """Cache reads are ~10% of input price, but for a burn dashboard raw volume is the honest number."""
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )


def iter_json(path: Path):
    for line in path.read_text(**DECODE).splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def collect_stats(transcript: Path) -> dict:
    """Attribute token usage to each subagent.

    Subagent turns are not in the session transcript. They live in sibling files,
    `<session-id>/subagents/agent-<agentId>.jsonl`, one per spawn. Every line there carries
    `agentId` and every assistant line carries `attributionAgent` (the subagent type) and `usage`.

    The main chain names the spawn: the `Agent` tool's `toolUseResult` holds `agentId`,
    `description` and `resolvedModel` on one record. Join on `agentId`.
    """
    main_tokens = 0
    meta: dict[str, dict] = {}

    for d in iter_json(transcript):
        if d.get("type") == "assistant" and not d.get("isSidechain"):
            main_tokens += billable((d.get("message") or {}).get("usage") or {})
        result = d.get("toolUseResult")
        if isinstance(result, dict) and (aid := result.get("agentId")):
            meta[aid] = {
                "description": result.get("description") or "?",
                "model": result.get("resolvedModel") or "?",
            }

    per_agent: dict[str, dict] = {}
    subagent_dir = transcript.parent / transcript.stem / "subagents"

    for f in sorted(subagent_dir.glob("agent-*.jsonl")) if subagent_dir.is_dir() else []:
        aid = f.stem.removeprefix("agent-")
        tokens = turns = 0
        kind = None
        for d in iter_json(f):
            if d.get("type") != "assistant":
                continue
            kind = kind or d.get("attributionAgent")
            tokens += billable((d.get("message") or {}).get("usage") or {})
            turns += 1
        if not turns:
            continue
        info = meta.get(aid, {})
        label = f"{kind or 'agent'}: {info.get('description', aid[:8])}"
        per_agent[label] = {
            "tokens": tokens,
            "turns": turns,
            "model": info.get("model", "?"),
        }

    return {"main": main_tokens, "agents": per_agent}


def fmt_tokens(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


# ---------------------------------------------------------------- dashboard

GLYPH = {"queued": "[ ]", "red": "[R]", "green": "[G]", "done": "[x]", "blocked": "[!]"}


LOGGED_CYCLE = re.compile(r"\bcycle-(\d+)\b")


def log_drift(project: str, cycles: list[dict]) -> tuple[int, int] | None:
    """`(highest defined id, highest id in the log)` when the log has run past the cycle file.

    The cycle file records what was *planned*; the log records what happened. When they diverge the
    board is not wrong about anything it says — every number on it is internally consistent with the
    file it read — which is precisely why nobody notices. So compare against the record the harness
    does not write.
    """
    ids = [int(c["id"]) for c in cycles if str(c["id"]).lstrip("-").isdigit()]
    if not ids:
        return None
    subjects = git(project, "log", "--all", "--format=%s")
    if not subjects:
        return None
    logged = [int(n) for n in LOGGED_CYCLE.findall(subjects)]
    if not logged:
        return None
    return (max(ids), max(logged)) if max(logged) > max(ids) else None


def render(project: str, stats: dict | None) -> str:
    s = load_state(project)
    gate = s["gate"]["state"]
    cov = f"{s['coverage']}%" if s.get("coverage") is not None else "--"
    done = sum(1 for c in s["cycles"] if c["state"] == "done")
    unproven = [c for c in s["cycles"] if c["state"] == "done" and not c.get("evidence")]

    lines = [
        f"### {project}",
        "",
        f"`gate {gate}` · `coverage {cov}` · `cycles {done}/{len(s['cycles'])}`",
        "",
        "| # | cycle | state | agent | tokens | files | evidence |",
        "|---|-------|-------|-------|--------|-------|----------|",
    ]
    for c in s["cycles"]:
        if c["state"] != "done":
            ev = "-"
        else:
            ev = "yes" if c.get("evidence") else "**MISSING**"
        # How much guarded code this cycle opened on one failing test. The gate is per-project, so
        # this is the number that says whether "one test" meant one behaviour or a free hand.
        touched = c.get("touched")
        breadth = str(len(touched)) if touched else ("-" if c["state"] != "done" else "0")
        title = f"{c['title']} _(orphan)_" if c.get("orphan") else c["title"]
        lines.append(
            f"| {c['id']} | {title} | {GLYPH[c['state']]} {c['state']} "
            f"| {c['agent']} | {fmt_tokens(c['tokens'])} | {breadth} | {ev} |"
        )

    if (drift := log_drift(project, s["cycles"])) is not None:
        highest_id, highest_logged = drift
        lines += [
            "",
            f"> **Cycle file behind the log**: `.claude/cycles/{project}.json` defines up to id "
            f"{highest_id}, and the log carries `cycle-{highest_logged}`. The board can only ever "
            f"render `n/{len(s['cycles'])}` — it is not lying, it is reporting faithfully on a file "
            f"that stopped describing the project. Add the missing cycles to the cycle file.",
        ]

    orphans = [c for c in s["cycles"] if c.get("orphan")]
    if orphans:
        ids = ", ".join(str(c["id"]) for c in orphans)
        lines += [
            "",
            f"> **Orphan cycle(s) {ids}**: the state records them, `.claude/cycles/{project}.json` "
            f"no longer defines them. Either the cycle file lost an entry it should still have, or "
            f"these rows describe work that is no longer in the plan. Reconcile deliberately.",
        ]

    if unproven:
        ids = ", ".join(str(c["id"]) for c in unproven)
        lines += ["", f"> **Unproven completion**: cycle(s) {ids} are `done` with no evidence. Treat as not done."]

    if stats and stats["agents"]:
        lines += [
            "",
            "**Subagent token burn (this session)**",
            "",
            "| agent | model | turns | tokens |",
            "|---|---|---|---|",
        ]
        for label, v in sorted(stats["agents"].items(), key=lambda kv: -kv[1]["tokens"]):
            lines.append(f"| {label} | {v['model']} | {v['turns']} | {fmt_tokens(v['tokens'])} |")
        lines.append(f"| _orchestrator (main)_ | - | - | {fmt_tokens(stats['main'])} |")

    return "\n".join(lines)


def cmd_status(projects: list[str], write: bool, hook_payload: dict | None = None) -> None:
    transcript = find_transcript(hook_payload)
    stats = collect_stats(transcript) if transcript and transcript.exists() else None
    targets = projects or known_projects()
    body = "\n\n".join(render(p, stats) for p in targets)
    doc = f"# Harness progress\n\n_generated by `harness.py status` — do not hand-edit_\n\n{body}\n"
    print(doc)
    if write:
        (ROOT / "PROGRESS.md").write_text(doc, encoding="utf-8")


def git(project: str, *args: str) -> str:
    d = project_dir(project)
    if not (d / ".git").is_dir():
        return ""
    proc = subprocess.run(["git", "-C", str(d), *args], capture_output=True, **DECODE)
    return proc.stdout.strip()


def cmd_handoff() -> None:
    """Write the next session's starting point. Derived from state, never from chat history."""
    out = [
        "# Session handoff",
        "",
        f"_generated by `harness.py handoff` at {time.strftime('%Y-%m-%d %H:%M')} — do not hand-edit_",
        "",
        "## Next action",
        "",
    ]

    next_action = None
    blockers: list[str] = []

    for project in known_projects():
        s = load_state(project)
        blocked = [c for c in s["cycles"] if c["state"] == "blocked"]
        unproven = [c for c in s["cycles"] if c["state"] == "done" and not c.get("evidence")]
        pending = [c for c in s["cycles"] if c["state"] != "done"]

        # Carry the recorded reason, not just the title. The title says which cycle; only the
        # reason says why, and re-deriving it is the waste this file exists to prevent.
        blockers += [
            f"`{project}` cycle {c['id']} is **blocked**: {c['title']}"
            + (f" — {c['evidence']}" if c.get("evidence") else " — no reason recorded")
            for c in blocked
        ]
        blockers += [
            f"`{project}` cycle {c['id']} is `done` with no evidence — re-verify or reopen it" for c in unproven
        ]

        if pending and not next_action and not blocked:
            c = pending[0]
            next_action = f"`/harness-build {project}` — cycle {c['id']}: {c['title']}"

    # A blocked board has no next action, but it is not a finished one. Saying so unconditionally
    # declares completion directly above the blockers it just listed, and contradicts
    # next_cycle.py, which still points at the blocked cycle.
    if next_action:
        out.append(next_action)
    elif blockers:
        out.append("Nothing is dispatchable — every remaining cycle is blocked. Clear the blockers below first.")
    else:
        out.append("All cycles are done and evidenced. Run the Definition of Done in `docs/PLAYBOOK.md`.")
    out += ["", "## Blockers and risks", ""]
    out += [f"- {b}" for b in blockers] or ["- none recorded"]

    out += ["", "## Where each project stands", ""]
    for project in known_projects():
        s = load_state(project)
        done = sum(1 for c in s["cycles"] if c["state"] == "done")
        cov = f"{s['coverage']}%" if s.get("coverage") is not None else "--"
        head = git(project, "log", "--oneline", "-1") or "no commits"
        out.append(
            f"- **{project}** — cycles {done}/{len(s['cycles'])}, gate `{s['gate']['state']}`, "
            f"coverage `{cov}`, head `{head}`"
        )

    out += [
        "",
        "## Startup for the next session",
        "",
        "1. `./init.sh` — verify the environment and the harness itself. Fix any failure before adding scope.",
        "2. Read `CLAUDE.md`, then `PROGRESS.md` for the board.",
        "3. Take the next action above. Do not skip a cycle; later tests depend on earlier code.",
        "",
        "A cycle is done only when evidence is recorded. `harness.py cycle <project> <id> done` refuses without it.",
        "",
    ]

    doc = "\n".join(out)
    (ROOT / "HANDOFF.md").write_text(doc, encoding="utf-8")
    print(doc)


# ------------------------------------------------------- reference documents
#
# ADRs and lessons are reference material: they earn their keep only when the agent who needs one
# actually reads it, and they cost context only when opened. Both of those point at the same
# mechanism — an index cheap enough to read every time, entries opened on match.
#
# That is also what keeps the set from growing without bound. Compaction here is not shorter
# prose, which saves nothing if nobody reads the file and everything if everybody reads all of
# it. It is retirement: a lesson whose failure mode is now blocked by a gate probe, a test or a
# lint rule is marked `mechanised` and drops out of the index, because the check has become the
# lesson. A superseded ADR drops out for the same reason — it is history, and history that
# presents itself as guidance is worse than no record at all.

FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(?P<key>[A-Za-z ]+):\*\*\s*(?P<value>.*?)\s*$", re.M)


def doc_fields(text: str) -> dict[str, str]:
    """The `**Key:** value` lines a lesson or ADR carries near the top.

    Tolerant of the leading `- ` that some of the existing ADRs use and others do not; a parser
    that silently returned nothing for half the corpus would quietly hide those documents from
    every index that depends on it.
    """
    return {m.group("key").strip().lower(): m.group("value").strip() for m in FIELD_RE.finditer(text)}


def doc_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            # The number is already the first column of the index; repeating it in the title
            # costs width that the trigger line needs more.
            return re.sub(r"^(?:Lesson|ADR)[- ]?\d+\s*[:—-]\s*", "", line[2:].strip())
    return fallback


def one_line(text: str, width: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def read_index(directory: Path, summary_key: str) -> list[tuple[str, str, str, dict]]:
    entries = []
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fields = doc_fields(text)
        entries.append((path.name, doc_title(text, path.stem), fields.get(summary_key, ""), fields))
    return entries


def cmd_lessons(show_all: bool) -> None:
    directory = ROOT / "docs" / "lessons"
    entries = read_index(directory, "trigger")
    if not entries:
        print("No lessons recorded yet. docs/lessons/0000-how-to-write-one.md sets the bar.")
        return

    live, retired = [], []
    for name, title, trigger, fields in entries:
        if name.startswith("0000"):
            continue
        (retired if fields.get("status", "active").lower() == "mechanised" else live).append(
            (name, title, trigger, fields)
        )

    if not live and not retired:
        # 0000 is the guide, not a lesson, so a repo carrying only that has recorded nothing yet.
        print("No lessons recorded yet. docs/lessons/0000-how-to-write-one.md sets the bar.")
        return

    print(f"Lessons — {len(live)} live, {len(retired)} mechanised. Open only what applies.\n")
    for name, title, trigger, _ in live:
        print(f"  {name[:4]}  {title}")
        if trigger:
            print(f"        {one_line(trigger)}")
    if retired:
        print(f"\n  {len(retired)} retired — the failure mode is now blocked by a check, so the check")
        print("  is the lesson. `lessons --all` lists them.")
        if show_all:
            print()
            for name, title, _, fields in retired:
                print(f"  {name[:4]}  {title}")
                print(f"        mechanised by: {one_line(fields.get('enforced by', 'unrecorded'))}")
    print(f"\n  Full text: {directory.as_posix()}/<file>")


def cmd_adrs(show_all: bool) -> None:
    directory = ROOT / "docs" / "adr"
    entries = [e for e in read_index(directory, "date") if not e[0].startswith("0000")]
    if not entries:
        print("No ADRs recorded yet. docs/adr/0000-template.md is the template.")
        return

    accepted, superseded = [], []
    for name, title, _, fields in entries:
        status = fields.get("status", "accepted").lower()
        (superseded if status.startswith("superseded") else accepted).append((name, title, status))

    print(f"ADRs — {len(accepted)} accepted, {len(superseded)} superseded.\n")
    for name, title, _ in accepted:
        print(f"  {name[:4]}  {title}")
    if superseded:
        print(f"\n  {len(superseded)} superseded — history, not guidance.")
        if show_all:
            for name, title, status in superseded:
                print(f"  {name[:4]}  {title}  ({status})")
    print(f"\n  Full text: {directory.as_posix()}/<file>")


def cmd_stats(write: bool) -> None:
    # Run as a SubagentStop hook, stdin carries the session's transcript_path. Run by hand, it doesn't.
    payload = None
    if not sys.stdin.isatty():
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = None

    transcript = find_transcript(payload)
    if not transcript or not transcript.exists():
        sys.exit(0)
    if write:
        cmd_status([], write=True, hook_payload=payload)
        sys.exit(0)
    print(json.dumps(collect_stats(transcript), indent=2))


# ---------------------------------------------------------------- entry


def require_known_project(project: str) -> None:
    """Refuse an unknown project by name, and say which ones exist.

    `green no-such-project` used to surface a `FileNotFoundError` carrying a `PosixPath` — an
    internal type answering a typo. The harness knows the answer: the cycle files are the list.
    """
    if (CYCLE_DIR / f"{project}.json").exists():
        return
    known = known_projects()
    sys.exit(
        f"unknown project {project!r}; known: {', '.join(known) if known else '(none defined)'}\n"
        f"  Projects are declared by their cycle file, {CYCLE_DIR.as_posix()}/<project>.json."
    )


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]

    if cmd == "gate":
        cmd_gate()
    elif cmd == "red":
        if len(args) < 2:
            sys.exit("usage: harness.py red <project> <test-path> [runner args]")
        require_known_project(args[0])
        cmd_red(args[0], args[1:])
    elif cmd == "green":
        if not args:
            sys.exit("usage: harness.py green <project>")
        require_known_project(args[0])
        cmd_green(args[0])
    elif cmd == "quality":
        if not args:
            sys.exit("usage: harness.py quality <project>")
        cmd_quality(args[0])
    elif cmd == "suite":
        if not args:
            sys.exit("usage: harness.py suite <project>")
        cmd_suite(args[0])
    elif cmd == "history":
        repo = None
        if "--repo" in args:
            i = args.index("--repo")
            repo = Path(args[i + 1]).resolve()
            args = args[:i] + args[i + 2:]
        if not args:
            sys.exit("usage: harness.py history <project> [--repo PATH]")
        cmd_history(args[0], repo)
    elif cmd == "cycle":
        evidence = None
        if "--evidence" in args:
            i = args.index("--evidence")
            evidence = " ".join(args[i + 1 :]).strip() or None
            args = args[:i]
        if len(args) < 3:
            sys.exit(
                "usage: harness.py cycle <project> <cycle-id> "
                "<queued|red|green|done|blocked> [agent] [--evidence TEXT]"
            )
        require_known_project(args[0])
        agent = args[3] if len(args) > 3 else None
        cmd_cycle(args[0], args[1], args[2], agent, evidence)
    elif cmd == "status":
        write = "--write" in args
        cmd_status([a for a in args if not a.startswith("--")], write)
    elif cmd == "stats":
        cmd_stats("--write" in args)
    elif cmd == "handoff":
        cmd_handoff()
    elif cmd == "lessons":
        cmd_lessons("--all" in args)
    elif cmd == "adrs":
        cmd_adrs("--all" in args)
    else:
        sys.exit(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    main()
