#!/usr/bin/env python3
"""Install the TDD harness into a repo.

Copies the gate and its tooling into `.claude/`, renders the constitution and docs, wires the
PreToolUse hook into `.claude/settings.json`, and seeds one cycle file per project.

    harness_init.py --target . --owner alice \
        --project billing-api:pytest:90 --project web:vitest:80

Idempotent and non-destructive: an existing file is reported and skipped, never overwritten,
unless --force is passed. The exception is `.claude/settings.json`, which is *merged* — the
harness hook is added alongside whatever hooks are already configured rather than replacing
the file, because clobbering a user's hooks to install a gate would be its own small betrayal.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = PLUGIN_ROOT / "templates"

GATE_HOOK = 'python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" gate'
STATS_HOOK = 'python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" stats --write'

# Copied into the target's .claude/scripts/. The harness must keep working in a fresh clone with
# the plugin uninstalled — a portfolio repo is read by people who do not have your plugins.
# harness_init.py is deliberately NOT here. It resolves its templates relative to the plugin
# root, so a copy sitting in the target's .claude/scripts/ looks for .claude/templates/ and dies
# on FileNotFoundError. Re-scaffolding is the plugin's job, not the scaffolded repo's.
SCRIPTS = [
    "harness.py",
    "next_cycle.py",
    "link_projects.sh",
    "usage_guard.py",
    "watch_subagents.sh",
    "auto_resume.sh",
    "autocont.sh",
]


@dataclass
class Project:
    name: str
    runner: str
    coverage: int

    @classmethod
    def parse(cls, spec: str) -> Project:
        """`name`, `name:runner`, or `name:runner:coverage`."""
        parts = spec.split(":")
        if not parts[0]:
            raise argparse.ArgumentTypeError(f"project spec {spec!r} has no name")
        name = parts[0]
        runner = parts[1] if len(parts) > 1 and parts[1] else "pytest"
        try:
            coverage = int(parts[2]) if len(parts) > 2 and parts[2] else 90
        except ValueError:
            raise argparse.ArgumentTypeError(f"project spec {spec!r}: coverage must be an integer")
        return cls(name=name, runner=runner, coverage=coverage)


class Writer:
    """Tracks what was written, skipped, or merged, so the run can report honestly."""

    def __init__(self, root: Path, force: bool) -> None:
        self.root = root
        self.force = force
        self.written: list[str] = []
        self.skipped: list[str] = []

    def write(self, rel: str, content: str, executable: bool = False) -> None:
        dest = self.root / rel
        if dest.exists() and not self.force:
            self.skipped.append(rel)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        if executable:
            dest.chmod(0o755)
        self.written.append(rel)

    def copy(self, src: Path, rel: str) -> None:
        dest = self.root / rel
        if dest.exists() and not self.force:
            self.skipped.append(rel)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self.written.append(rel)


def render(name: str, **subs: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def merge_settings(root: Path) -> str:
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

    hooks = settings.setdefault("hooks", {})
    added = []

    def ensure(event: str, command: str, matcher: str | None) -> None:
        entries = hooks.setdefault(event, [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    return  # Already wired. Installing it twice would fire the gate twice.
        block: dict = {"hooks": [{"type": "command", "command": command}]}
        if matcher:
            block["matcher"] = matcher
        entries.append(block)
        added.append(event)

    ensure("PreToolUse", GATE_HOOK, "Write|Edit|MultiEdit|NotebookEdit")
    ensure("SubagentStop", STATS_HOOK, None)

    if not added:
        return "== .claude/settings.json already wired (both hooks present)"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"++ .claude/settings.json (merged: {', '.join(added)})"


def append_gitignore(root: Path) -> str:
    path = root / ".gitignore"
    block = (TEMPLATES / "gitignore.append").read_text(encoding="utf-8")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if "--- TDD harness ---" in existing:
        return "== .gitignore already carries the harness block"
    path.write_text(existing + block, encoding="utf-8")
    return "++ .gitignore (harness block appended)"


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the TDD harness into a repo.")
    ap.add_argument("--target", default=".", help="repo to install into (default: cwd)")
    ap.add_argument("--owner", default="", help="git host owner, used by link_projects.sh")
    ap.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="NAME[:RUNNER[:COVERAGE]]",
        help="a project to build; repeatable. Runner defaults to pytest, coverage to 90.",
    )
    ap.add_argument("--purpose", default="", help="one paragraph: what these projects are for")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args(argv)

    root = Path(args.target).resolve()
    if not root.is_dir():
        print(f"harness-init: {root} is not a directory", file=sys.stderr)
        return 1

    try:
        projects = [Project.parse(s) for s in args.project]
    except argparse.ArgumentTypeError as exc:
        print(f"harness-init: {exc}", file=sys.stderr)
        return 1

    writer = Writer(root, args.force)

    # 1. Scripts. These make the repo self-contained: the gate keeps working with the plugin gone.
    for name in SCRIPTS:
        src = PLUGIN_ROOT / "scripts" / name
        if src.exists():
            writer.copy(src, f".claude/scripts/{name}")
    # The suite that drives harness.py itself. It goes under .claude/, not the repo's tests/,
    # because a target repo's tests/ is its own — init.sh running that as "the harness suite"
    # would report someone else's failing tests as a broken gate.
    for name in sorted((PLUGIN_ROOT / "tests").glob("test_*.py")):
        writer.copy(name, f".claude/harness-tests/{name.name}")

    for rel in (".claude/state", ".claude/cycles", "brief"):
        (root / rel).mkdir(parents=True, exist_ok=True)

    # 2. Config.
    writer.write(".claude/harness.json", render("harness.json.tmpl", OWNER=args.owner or "CHANGE-ME"))

    # 3. One cycle file per project. These are stubs on purpose — the real cycles are lifted
    #    from each brief, and inventing them here would be scope the brief never asked for.
    for order, project in enumerate(projects, start=1):
        writer.write(f".claude/cycles/{project.name}.json", cycle_stub(project, order))

    # 4. Docs and the constitution.
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
            STACK="TODO: name the stack here. Fixed by the briefs, not open for improvement.",
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
    writer.write("docs/LESSONS.md", render("docs/LESSONS.md.tmpl"))
    writer.copy(TEMPLATES / "docs" / "PLAYBOOK.md", "docs/PLAYBOOK.md")
    writer.copy(TEMPLATES / "docs" / "adr" / "0000-template.md", "docs/adr/0000-template.md")
    # The ADR that explains why the gate exists travels with the gate. A mechanism whose reason
    # is left behind gets removed by the first person who finds it inconvenient.
    writer.copy(TEMPLATES / "docs" / "adr" / "0003-mechanical-red-gate.md", "docs/adr/0003-mechanical-red-gate.md")
    if any(p.runner == "vitest" for p in projects):
        writer.copy(
            TEMPLATES / "docs" / "adr" / "0009-one-gate-many-runners.md",
            "docs/adr/0009-one-gate-many-runners.md",
        )

    # 5. The entrypoint.
    writer.write("init.sh", (TEMPLATES / "init.sh.tmpl").read_text(encoding="utf-8"), executable=True)

    # 6. Hooks and gitignore — merged, not overwritten.
    settings_note = merge_settings(root)
    gitignore_note = append_gitignore(root)

    # ------------------------------------------------------------------ report
    print(f"Harness installed into {root}\n")
    for rel in writer.written:
        print(f"  ++ {rel}")
    print(f"  {settings_note}")
    print(f"  {gitignore_note}")
    if writer.skipped:
        print("\n  Left alone (already present — pass --force to overwrite):")
        for rel in writer.skipped:
            print(f"  == {rel}")

    print("\nNext:")
    print("  1. Write the briefs under brief/ — the harness builds what the specs say, nothing more.")
    print("  2. Lift each brief's cycle list into .claude/cycles/<project>.json (the stubs are placeholders).")
    print("  3. Fill in CONTEXT.md before writing code that uses domain words.")
    print("  4. Run ./init.sh — it must prove the gate blocks before you trust it.")
    if not args.owner:
        print('  5. Set "owner" in .claude/harness.json (currently CHANGE-ME) or link_projects.sh cannot clone.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
