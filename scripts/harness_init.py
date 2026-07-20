#!/usr/bin/env python3
"""Install the TDD harness into a repo.

Copies the gate and its tooling into `.claude/`, renders the constitution and docs, wires the
PreToolUse hook into `.claude/settings.json`, and seeds one cycle file per project.

    harness_init.py --target . --owner alice \
        --project billing-api:pytest:90 --project web:vitest:80

Run it again after a plugin update and it *re-syncs*: every file the harness owns is rewritten
from the plugin, every file you own is left exactly as you left it. The two sets are declared
below and the distinction is the whole design —

  * FRAMEWORK — the gate and its tooling. Vendored into the repo so it keeps working in a fresh
    clone with the plugin uninstalled, which means a bugfix in the plugin reaches an installed
    repo only when the installer overwrites it. Skipping these on reinstall stranded every fix
    in every scaffolded repo, undetectably; see docs/lessons/0005.
  * USER — the constitution, the glossary, the config, the cycles, the lessons. Overwriting
    these destroys the only work in the repo nobody can regenerate. `--reset <path>` overwrites
    one of them, named explicitly, because the blanket `--force` that used to do this was the
    only upgrade path and it took the user's content with it.

`.claude/settings.json` and `.gitignore` are *merged* — the harness hooks are added alongside
whatever is already configured rather than replacing the file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Both streams. Every error this script reports goes to stderr, and on a Windows console that is
# a legacy codepage — an em-dash in a message about an unknown runner raised UnicodeEncodeError
# instead of printing it, and anything reading the stream as UTF-8 got nothing at all.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = PLUGIN_ROOT / "templates"

VERSION_FILE = ".claude/.harness-version"

# The runners the shipped template defines. A project scaffolded against a name nothing defines
# produces a cycle file that is fatal at the first `red`, one whole session later — but the set is
# not fixed: a repo that has added `gotest` to its own .claude/harness.json can scaffold against
# it, which is what docs/PLAYBOOK.md has always said and what this used to refuse.
TEMPLATE_RUNNERS = ("pytest", "vitest")


def known_runners(root: Path) -> tuple[str, ...]:
    """Runner names this target will actually be able to run.

    Read from the target's own config when it has one, because that is the file `harness.py`
    resolves against; fall back to the template's set for a fresh install, where the config does
    not exist yet and the template is what is about to be written.
    """
    config = root / ".claude" / "harness.json"
    try:
        names = tuple(json.loads(config.read_text(encoding="utf-8"))["runners"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return TEMPLATE_RUNNERS
    return names or TEMPLATE_RUNNERS


NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

# Tests that drive the installer rather than the gate. They stay in the plugin: the installer is
# deliberately not vendored, so a copy of its suite in a scaffolded repo tests a file that is not
# there and reports the harness red. Which is exactly what it did, once.
NOT_VENDORED = {"test_install.py", "test_plugin_surface.py"}

# Copied into the target's .claude/scripts/. The harness must keep working in a fresh clone with
# the plugin uninstalled — a portfolio repo is read by people who do not have your plugins.
# harness_init.py is deliberately NOT here. It resolves its templates relative to the plugin
# root, so a copy sitting in the target's .claude/scripts/ looks for .claude/templates/ and dies
# on FileNotFoundError (docs/lessons/0003). Re-scaffolding is the plugin's job.
SCRIPTS = [
    "harness.py",
    "next_cycle.py",
    "link_projects.sh",
    "usage_guard.py",
    "watch_subagents.sh",
    "auto_resume.sh",
    "autocont.sh",
]


def plugin_version() -> str:
    manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, json.JSONDecodeError):
        return "unknown"


def gate_interpreter() -> tuple[str, str | None]:
    """The interpreter the hook command names, and a warning when we could not confirm it resolves.

    Always `python3`, and the reasoning matters because the obvious alternative is wrong.

    The hook runs in Claude Code's own shell, not in `init.sh`, so `init.sh`'s `command -v python3`
    never speaks for it. On a machine where `python3` is genuinely absent the hook may never fire,
    and an unfired PreToolUse hook is a gate that is silently off — the worst failure this plugin
    has. Pinning `sys.executable` fixes that for exactly one machine and breaks it for every other:
    `.claude/settings.json` is committed, not gitignored, so whatever goes in it travels to every
    clone. A path like `C:/Users/me/python.exe` in a cloned repo is not a portable gate, it is a
    guaranteed-dead one, and the repo being readable by people who do not have this plugin is the
    whole reason the harness is vendored in the first place.

    So: write the portable name, and warn when it cannot be seen from here. `./init.sh` probes the
    command that is actually wired and fails loudly if it does not run, which is the mechanical
    backstop — but it is a command someone has to run, and the hook fires on its own. Treat the
    warning as real.
    """
    if shutil.which("python3"):
        return "python3", None
    if shutil.which("uv"):
        # uv fetches 3.12 on demand and is itself portable, so this stays correct on any machine
        # that has uv — which init.sh now offers to install. Measured at ~10ms over a direct
        # python3 on a warm cache, which is nothing against a gate that otherwise may not run.
        return (
            "uv run --no-project --python 3.12 python",
            "python3 was not found on PATH, so the gate hook runs through uv, which supplies "
            "Python 3.12 itself. Portable, and about ten milliseconds slower per write.",
        )
    return (
        "python3",
        "neither python3 nor uv was found on PATH. The gate hook still names python3, because an "
        "absolute path here would be committed into .claude/settings.json and be wrong on every "
        "other machine. Run ./init.sh — it offers to install uv, and it probes the wired command "
        "rather than trusting it. (Git Bash shims are invisible to this check, so this may be a "
        "false alarm.)",
    )


@dataclass
class Project:
    name: str
    runner: str
    coverage: int

    @classmethod
    def parse(cls, spec: str, runners: tuple[str, ...] = TEMPLATE_RUNNERS) -> Project:
        """`name`, `name:runner`, or `name:runner:coverage`.

        Every field is validated here rather than at first use. A bad runner or an unreachable
        coverage gate that is accepted at install time surfaces as a fatal error in the middle of
        the first cycle, which is the most expensive place to learn about a typo.
        """
        parts = spec.split(":")
        name = parts[0]
        if not NAME_RE.fullmatch(name):
            raise argparse.ArgumentTypeError(
                f"project spec {spec!r}: name must be one path segment of letters, digits, dot, "
                "dash or underscore"
            )
        runner = parts[1] if len(parts) > 1 and parts[1] else "pytest"
        if runner not in runners:
            raise argparse.ArgumentTypeError(
                f"project spec {spec!r}: unknown runner {runner!r}. This repo defines: "
                f"{', '.join(runners)}. Add a definition for {runner!r} to .claude/harness.json and "
                "run this again — see 'Adding a runner' in docs/PLAYBOOK.md."
            )
        try:
            coverage = int(parts[2]) if len(parts) > 2 and parts[2] else 90
        except ValueError:
            raise argparse.ArgumentTypeError(f"project spec {spec!r}: coverage must be an integer")
        if not 0 <= coverage <= 100:
            raise argparse.ArgumentTypeError(
                f"project spec {spec!r}: coverage {coverage} is not a percentage. A gate above 100 "
                "can never be met."
            )
        return cls(name=name, runner=runner, coverage=coverage)


class Writer:
    """Writes files, tracking which were created, re-synced, or left alone.

    Two kinds, and the difference is the point. `framework` files belong to the harness and are
    rewritten every run, so a plugin update actually reaches the repo. `user` files belong to the
    repo and are never rewritten unless named in --reset.
    """

    def __init__(self, root: Path, reset: set[str]) -> None:
        self.root = root
        self.reset = reset
        self.created: list[str] = []
        self.resynced: list[str] = []
        self.kept: list[str] = []

    def _place(self, rel: str, write: "callable[[Path], None]", framework: bool) -> None:
        dest = self.root / rel
        existed = dest.exists()
        if existed and not framework and rel not in self.reset:
            self.kept.append(rel)
            return
        before = dest.read_bytes() if existed else None
        dest.parent.mkdir(parents=True, exist_ok=True)
        write(dest)
        if not existed:
            self.created.append(rel)
        elif dest.read_bytes() != before:
            self.resynced.append(rel)

    def write(self, rel: str, content: str, framework: bool = False, executable: bool = False) -> None:
        def do(dest: Path) -> None:
            # newline="\n" explicitly: the default translates to os.linesep, which on Windows
            # vendors a CRLF init.sh into a repo that is expected to run on Linux CI.
            dest.write_text(content, encoding="utf-8", newline="\n")
            if executable:
                dest.chmod(0o755)

        self._place(rel, do, framework)

    def copy(self, src: Path, rel: str, framework: bool = False) -> None:
        self._place(rel, lambda dest: shutil.copy2(src, dest), framework)


def render(name: str, **subs: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def derive_requires(projects: list[Project]) -> list[str]:
    """The tools this repo will actually need, from the runners it was given.

    Defaulting to docker and gh made `init.sh` abort in the environment check on a machine with
    no Docker daemon — before it ever reached the gate self-test, which is the one thing the
    install is supposed to prove and which needs neither.
    """
    requires = ["python3", "uv"]
    if any(p.runner == "vitest" for p in projects):
        requires += ["node", "npm"]
    return requires


# Keys the current template writes. A repo installed on an older version keeps its own
# harness.json — that is the point of repo-owned — so a re-sync can deliver a harness.py whose
# schema the config predates. harness.py fills gaps in a *known* runner from its own defaults, so
# nothing breaks; this note exists because a silently-defaulted key is still a key the user never
# chose, and they should know which decisions were made for them.
CONFIG_KEYS = ("stack", "protected")
RUNNER_KEYS = ("quality",)


def config_schema_note(root: Path) -> str:
    path = root / ".claude" / "harness.json"
    if not path.exists():
        return ""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "!! .claude/harness.json is not valid JSON — every command is running on defaults."

    missing = [k for k in CONFIG_KEYS if k not in cfg]
    for name, spec in cfg.get("runners", {}).items():
        missing += [f"runners.{name}.{k}" for k in RUNNER_KEYS if k not in spec]
    if not missing:
        return ""
    return (
        f"!! .claude/harness.json predates this version: no {', '.join(missing)}. "
        "Shipped runners fall back to the plugin's defaults, so nothing is broken — but see "
        "templates/harness.json.tmpl for what is now configurable, or --reset .claude/harness.json "
        "to take the new file (it overwrites your owner, guarded paths and runner edits)."
    )


def merge_settings(root: Path, interpreter: str) -> str:
    """Add the harness hooks to .claude/settings.json without disturbing what is there.

    Returns a human-readable note about what happened.
    """
    path = root / ".claude" / "settings.json"
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return f"!! {path.relative_to(root)} is not valid JSON — left alone. Add the gate hook by hand."

    gate = f'{interpreter} "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" gate'
    stats = f'{interpreter} "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" stats --write'

    hooks = settings.setdefault("hooks", {})
    changed = []

    def ensure(event: str, command: str, subcommand: str, matcher: str | None) -> None:
        """Install `command`, replacing any existing harness hook for the same subcommand.

        Matching on the exact command string would be wrong now that the interpreter is resolved
        per machine: a reinstall under a different interpreter would leave the old hook in place
        and add a second one, and the gate would fire twice — once through an interpreter that
        does not exist.
        """
        entries = hooks.setdefault(event, [])
        marker = f'harness.py" {subcommand}'
        for entry in entries:
            for hook in entry.get("hooks", []):
                existing = hook.get("command", "")
                if "harness.py" in existing and marker in existing:
                    if existing != command:
                        hook["command"] = command
                        changed.append(f"{event} (re-pointed)")
                    return
        block: dict = {"hooks": [{"type": "command", "command": command}]}
        if matcher:
            block["matcher"] = matcher
        entries.append(block)
        changed.append(event)

    ensure("PreToolUse", gate, "gate", "Write|Edit|MultiEdit|NotebookEdit")
    ensure("SubagentStop", stats, "stats", None)

    if not changed:
        return "== .claude/settings.json already wired (both hooks present)"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8", newline="\n")
    return f"++ .claude/settings.json (merged: {', '.join(changed)})"


def append_gitignore(root: Path) -> str:
    """Append the harness block, or top it up if it predates some of its entries.

    Skipping wholesale because the marker is present was the same mistake as never rewriting
    harness.json: this block has a schema that grows. A repo scaffolded before `__pycache__/` was
    added kept a block that no longer covered what `init.sh` creates, and nothing said so.
    """
    path = root / ".gitignore"
    block = (TEMPLATES / "gitignore.append").read_text(encoding="utf-8")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    if "--- TDD harness ---" not in existing:
        path.write_text(existing + block, encoding="utf-8", newline="\n")
        return "++ .gitignore (harness block appended)"

    have = {line.strip() for line in existing.splitlines()}
    missing = [
        line.strip() for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and line.strip() not in have
    ]
    if not missing:
        return "== .gitignore already carries the harness block"

    addition = "\n# --- TDD harness (added by a later version) ---\n" + "\n".join(missing) + "\n"
    path.write_text(existing.rstrip("\n") + "\n" + addition, encoding="utf-8", newline="\n")
    return f"++ .gitignore (topped up: {', '.join(missing)})"


def cycle_stub(project: Project, order: int) -> str:
    body = {
        "project": project.name,
        "build_order": order,
        "brief": f"brief/{order:02d}-{project.name}.md",
        "coverage_gate": project.coverage,
        "runner": project.runner,
        "cycles": [
            {
                "id": 0,
                "title": f"scaffold: {project.name} — project skeleton, test runner, CI, no production code yet",
                "first_test": None,
            },
            {
                "id": 1,
                "title": "TODO: the first behaviour, named as a behaviour and not as a class",
                "first_test": "tests/test_todo.py" if project.runner == "pytest" else "src/todo.test.ts",
            },
        ],
    }
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


def install_lessons(writer: Writer) -> None:
    """Seed docs/lessons/ and its index.

    One file per lesson, mirroring docs/adr/, so an agent can read the index and open only what
    is relevant. A single growing LESSONS.md forces the whole history into context or none of it,
    and "none of it" is what happened: nothing in the harness ever read the file.
    """
    src = TEMPLATES / "docs" / "lessons"
    for entry in sorted(src.glob("*.md")):
        # Seeded, then owned by the repo: lessons written here are the user's record, and a
        # reinstall must not overwrite one they edited.
        writer.copy(entry, f"docs/lessons/{entry.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install or re-sync the TDD harness in a repo.")
    ap.add_argument("--target", default=".", help="repo to install into (default: cwd)")
    ap.add_argument("--owner", default="", help="git host owner, used by link_projects.sh")
    ap.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME[:RUNNER[:COVERAGE]]",
        help="a project to build; repeatable. Runner is any name defined in the target's "
        f".claude/harness.json, or one of {', '.join(TEMPLATE_RUNNERS)} on a fresh install "
        "(default pytest); coverage 0-100 (default 90).",
    )
    ap.add_argument("--purpose", default="", help="one paragraph: what these projects are for")
    ap.add_argument(
        "--stack",
        default="",
        help="what the projects are built with, with versions. Written to .claude/harness.json "
        "and rendered into CLAUDE.md; the agents read it from there rather than carrying one.",
    )
    ap.add_argument(
        "--reset",
        action="append",
        default=[],
        metavar="PATH",
        help="overwrite one repo-owned file, named exactly (e.g. --reset CLAUDE.md). Repeatable. "
        "Harness-owned files are re-synced every run and need no flag.",
    )
    args = ap.parse_args(argv)

    root = Path(args.target).resolve()
    if not root.is_dir():
        print(f"harness-init: {root} is not a directory", file=sys.stderr)
        return 1

    try:
        runners = known_runners(root)
        projects = [Project.parse(s, runners) for s in args.project]
    except argparse.ArgumentTypeError as exc:
        print(f"harness-init: {exc}", file=sys.stderr)
        return 1

    version = plugin_version()
    interpreter, interpreter_note = gate_interpreter()
    writer = Writer(root, set(args.reset))

    # ---------------------------------------------------------------- harness-owned (re-synced)
    for name in SCRIPTS:
        src = PLUGIN_ROOT / "scripts" / name
        if src.exists():
            writer.copy(src, f".claude/scripts/{name}", framework=True)
    # The suite that drives harness.py itself. It goes under .claude/, not the repo's tests/,
    # because a target repo's tests/ is its own — init.sh running that as "the harness suite"
    # would report someone else's failing tests as a broken gate.
    for name in sorted((PLUGIN_ROOT / "tests").glob("test_*.py")):
        if name.name in NOT_VENDORED:
            continue
        writer.copy(name, f".claude/harness-tests/{name.name}", framework=True)

    writer.write("init.sh", (TEMPLATES / "init.sh.tmpl").read_text(encoding="utf-8"),
                 framework=True, executable=True)
    writer.copy(TEMPLATES / "docs" / "PLAYBOOK.md", "docs/PLAYBOOK.md", framework=True)
    writer.copy(TEMPLATES / "docs" / "adr" / "0000-template.md", "docs/adr/0000-template.md",
                framework=True)
    # The ADR that explains why the gate exists travels with the gate. A mechanism whose reason
    # is left behind gets removed by the first person who finds it inconvenient.
    writer.copy(TEMPLATES / "docs" / "adr" / "0003-mechanical-red-gate.md",
                "docs/adr/0003-mechanical-red-gate.md", framework=True)
    if any(p.runner == "vitest" for p in projects):
        writer.copy(TEMPLATES / "docs" / "adr" / "0009-one-gate-many-runners.md",
                    "docs/adr/0009-one-gate-many-runners.md", framework=True)

    # The stamp has to be harness-owned. Putting it in harness.json — which the user edits, and
    # which is therefore never rewritten — would produce a version marker that is correct exactly
    # once and then lies for the rest of the repo's life.
    writer.write(
        VERSION_FILE,
        f"{version}\n"
        "# Written by harness_init.py. The version of the tdd-harness plugin that last re-synced\n"
        "# .claude/scripts/. Compare it with the installed plugin to see whether this repo is\n"
        "# carrying an old gate; re-run /harness-init to bring it forward.\n",
        framework=True,
    )

    for rel in (".claude/state", ".claude/cycles", "brief"):
        (root / rel).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------- repo-owned (preserved)
    stack = args.stack or "TODO: name the stack, with versions. Until this says something, an agent asked to build will pick one for you."
    writer.write(
        ".claude/harness.json",
        render(
            "harness.json.tmpl",
            OWNER=args.owner or "CHANGE-ME",
            REQUIRES=json.dumps(derive_requires(projects)),
            # json.dumps, not the raw string: a stack description with a quote or a backslash in
            # it would otherwise produce a harness.json that does not parse, and every command
            # would fall back to DEFAULT_CONFIG without saying so.
            STACK=json.dumps(stack)[1:-1],
        ),
    )

    # Stubs on purpose — the real cycles are lifted from each brief, and inventing them here
    # would be scope the brief never asked for.
    for order, project in enumerate(projects, start=1):
        writer.write(f".claude/cycles/{project.name}.json", cycle_stub(project, order))

    quality = "the linter, the type checker and the test suite with coverage"
    writer.write(
        "CLAUDE.md",
        render(
            "CLAUDE.md.tmpl",
            REPO_NAME=root.name,
            PROJECT_COUNT=str(len(projects)) if projects else "several",
            PROJECTS_DIR="projects",
            PURPOSE=args.purpose or "TODO: one paragraph — what these projects are, and who reads them.",
            QUALITY_GATES=quality,
            STACK=stack,
        ),
    )
    writer.write(
        "CONTEXT.md",
        render(
            "CONTEXT.md.tmpl",
            REPO_NAME=root.name,
            CONTEXT_NAME="Context name",
            FIRST_PROJECT=projects[0].name if projects else "first-project",
        ),
    )
    install_lessons(writer)

    # ------------------------------------------------------------------------ merged, not owned
    config_note = config_schema_note(root)
    settings_note = merge_settings(root, interpreter)
    gitignore_note = append_gitignore(root)

    # ------------------------------------------------------------------------------------ report
    print(f"Harness {version} installed into {root}\n")
    for rel in writer.created:
        print(f"  ++ {rel}")
    for rel in writer.resynced:
        print(f"  ~~ {rel}  (re-synced from the plugin)")
    print(f"  {settings_note}")
    print(f"  {gitignore_note}")
    if config_note:
        print(f"  {config_note}")
    if writer.kept:
        print("\n  Yours, left untouched (use --reset <path> to overwrite one):")
        for rel in writer.kept:
            print(f"  == {rel}")

    if interpreter_note:
        print(f"\n  !! {interpreter_note}")

    print("\nNext:")
    print("  1. Write the briefs under brief/ — the harness builds what the specs say, nothing more.")
    print("  2. Lift each brief's cycle list into .claude/cycles/<project>.json (the stubs are placeholders).")
    print("  3. Fill in CONTEXT.md before writing code that uses domain words.")
    print("  4. Run ./init.sh — it must prove the gate blocks before you trust it.")
    if not args.stack:
        print('  5. Set "stack" in .claude/harness.json. Every agent reads it from there; while it')
        print("     says TODO, an agent asked to build will choose a stack on your behalf.")
    if not args.owner:
        print('  6. Set "owner" in .claude/harness.json (currently CHANGE-ME) or link_projects.sh cannot clone.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
