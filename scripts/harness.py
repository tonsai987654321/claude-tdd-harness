#!/usr/bin/env python3
"""TDD harness: mechanical RED gate, cycle tracking, per-subagent token accounting.

Domain-agnostic. Everything project-specific — which repos exist, which test runner each one
uses, which paths count as production code — is read from `.claude/harness.json`. See
`harness_config()` for the schema and the defaults applied when the file is absent.

Subcommands
    gate      PreToolUse hook. Blocks edits to production code while the RED gate is shut.
    red       Run a test, require it to fail, open the gate for one project.
    green     Run the full suite; on pass, shut the gate and record coverage.
    cycle     Move a TDD cycle between states. `done` requires --evidence.
    status    Render the dashboard to stdout and to PROGRESS.md.
    stats     Attribute token usage to each subagent from the session transcript.
    handoff   Write HANDOFF.md: next action, blockers, where each project stands.
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

# The dashboard draws with non-ASCII glyphs. A Windows console inherits a legacy codepage
# (cp874 here), and printing one of them raises UnicodeEncodeError — the whole command dies
# over a separator dot. POSIX stdout is already UTF-8, so this is a no-op there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE_DIR = ROOT / ".claude" / "state"
CYCLE_DIR = ROOT / ".claude" / "cycles"
CONFIG_PATH = ROOT / ".claude" / "harness.json"

# Applied when `.claude/harness.json` is absent or omits a key. These reproduce the behaviour the
# harness shipped with before it was made configurable, so an un-configured repo still gets a
# working gate for a Python/`app/` or a JS/`src/` layout.
DEFAULT_CONFIG: dict = {
    "projects_dir": "projects",
    # Production code. Touching it requires a failing test on record. A trailing "/" means "this
    # directory and everything under it"; anything else is an exact filename.
    #
    # `alembic/env.py` is named exactly rather than gating `alembic/`, because it is what
    # `alembic upgrade head` executes on every container boot — it ships, and it encodes
    # behaviour. `alembic.ini` beside it is config and cannot be driven by a test.
    "guarded": ["app/", "src/", "alembic/versions/", "alembic/env.py"],
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
        return cfg["runners"][name]
    except KeyError:
        sys.exit(f"harness: project '{project}' declares runner '{name}', which .claude/harness.json does not define.")


# ---------------------------------------------------------------- state


def state_path(project: str) -> Path:
    return STATE_DIR / f"{project}.json"


def load_state(project: str) -> dict:
    p = state_path(project)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    cycles = []
    seed = CYCLE_DIR / f"{project}.json"
    if seed.exists():
        cycles = [
            {"id": c["id"], "title": c["title"], "state": "queued", "agent": "-", "tokens": 0, "evidence": ""}
            for c in json.loads(seed.read_text(encoding="utf-8"))["cycles"]
        ]
    return {"project": project, "gate": {"state": "SHUT"}, "coverage": None, "cycles": cycles}


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

    return [p.stem for p in sorted(CYCLE_DIR.glob("*.json"), key=rank)]


def project_dir(project: str) -> Path:
    return ROOT / "projects" / project


def project_config(project: str) -> dict:
    p = CYCLE_DIR / f"{project}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def runner_for(project: str) -> str:
    return project_config(project).get("runner", "pytest")


# ---------------------------------------------------------------- gate (PreToolUse hook)


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

    match = next((m for g in guarded_patterns(cfg) if (m := g.search(rel))), None)
    if not match:
        sys.exit(0)
    if target.name in set(cfg["exempt_names"]):
        sys.exit(0)
    if any(re.search(p, rel) for p in cfg["exempt_patterns"]):
        sys.exit(0)

    project = match.group("project")
    rel = target.relative_to(root).as_posix()
    gate = load_state(project)["gate"]

    if gate.get("state") == "OPEN":
        sys.exit(0)

    if os.environ.get("HARNESS_GATE_BYPASS") == "1":
        print(f"harness: gate bypassed for {rel} (HARNESS_GATE_BYPASS=1)", file=sys.stderr)
        sys.exit(0)

    deny(
        f"BLOCKED by TDD gate: {rel}\n"
        f"  Gate for '{project}' is SHUT. No failing test is on record.\n"
        f"  Write the test first, then open the gate:\n"
        f"      /red {project} <test-path>\n"
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
    state["gate"] = {"state": "SHUT", "closed_at": time.time()}
    state["coverage"] = coverage
    save_state(project, state)
    print(f"\nGREEN. Gate SHUT for '{project}'. Coverage: {coverage if coverage is not None else '?'}%")


# ---------------------------------------------------------------- cycles


def cmd_cycle(project: str, cycle_id: str, new_state: str, agent: str | None, evidence: str | None) -> None:
    valid = {"queued", "red", "green", "done", "blocked"}
    if new_state not in valid:
        sys.exit(f"state must be one of {sorted(valid)}")

    # A `done` with no evidence is indistinguishable from a lie. Refuse it.
    if new_state == "done" and not evidence:
        sys.exit(
            f"REFUSED: cycle {cycle_id} cannot be 'done' without evidence.\n"
            f"  Pass what actually ran and what it printed:\n"
            f'      harness.py cycle {project} {cycle_id} done --evidence "pytest 24 passed, cov 93%; '
            f'ruff clean; mypy clean; a1b2c3d [RED] -> e4f5g6h [GREEN]"'
        )

    state = load_state(project)
    for c in state["cycles"]:
        if str(c["id"]) == str(cycle_id):
            c["state"] = new_state
            if agent:
                c["agent"] = agent
            if evidence:
                c["evidence"] = evidence
                c["verified_at"] = time.strftime("%Y-%m-%d %H:%M")
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
        "| # | cycle | state | agent | tokens | evidence |",
        "|---|-------|-------|-------|--------|----------|",
    ]
    for c in s["cycles"]:
        if c["state"] != "done":
            ev = "-"
        else:
            ev = "yes" if c.get("evidence") else "**MISSING**"
        lines.append(
            f"| {c['id']} | {c['title']} | {GLYPH[c['state']]} {c['state']} "
            f"| {c['agent']} | {fmt_tokens(c['tokens'])} | {ev} |"
        )

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
            next_action = f"`/build {project}` — cycle {c['id']}: {c['title']}"

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


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]

    if cmd == "gate":
        cmd_gate()
    elif cmd == "red":
        cmd_red(args[0], args[1:])
    elif cmd == "green":
        cmd_green(args[0])
    elif cmd == "cycle":
        evidence = None
        if "--evidence" in args:
            i = args.index("--evidence")
            evidence = " ".join(args[i + 1 :]).strip() or None
            args = args[:i]
        agent = args[3] if len(args) > 3 else None
        cmd_cycle(args[0], args[1], args[2], agent, evidence)
    elif cmd == "status":
        write = "--write" in args
        cmd_status([a for a in args if not a.startswith("--")], write)
    elif cmd == "stats":
        cmd_stats("--write" in args)
    elif cmd == "handoff":
        cmd_handoff()
    else:
        sys.exit(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    main()
