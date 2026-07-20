"""Every command and file the harness tells someone to use has to exist.

The gate's block message named `/red`, the handoff named `/build`, and `auto_resume.sh` launched
`claude -p "/continue"` — none of which this plugin has ever defined. They were the names in the
repo the harness was extracted from, and they survived the extraction because nothing checks a
string that is only ever read by a human under pressure. The gate's refusal is the most-read text
the harness produces, and it was telling people to run a command that does not exist.

These tests are about the plugin's own surface, so they stay in the plugin (`NOT_VENDORED`) — a
scaffolded repo has no `commands/` directory to check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# Where a slash command could be emitted to a user or executed. Docs included: a README that names
# a command nobody can run is the same defect, found by the same check.
SEARCHED = [
    *(PLUGIN_ROOT / "scripts").glob("*.py"),
    *(PLUGIN_ROOT / "scripts").glob("*.sh"),
    *(PLUGIN_ROOT / "agents").glob("*.md"),
    *(PLUGIN_ROOT / "commands").glob("*.md"),
    *(PLUGIN_ROOT / "templates").rglob("*.md"),
    *(PLUGIN_ROOT / "templates").rglob("*.tmpl"),
    # docs/, minus the lessons. A lesson's whole job is to record what used to be true, and three
    # of them name `/red`, `/build` and `/continue` precisely because those commands never
    # existed — holding the history to the current command set would delete the history.
    *(p for p in (PLUGIN_ROOT / "docs").rglob("*.md") if "lessons" not in p.parts),
    PLUGIN_ROOT / "README.md",
]

# Slash commands Claude Code provides, or that belong to other tools. These are not ours to define.
FOREIGN = {"/plugin", "/help", "/clear", "/config", "/init", "/model", "/compact", "/fast"}

# A `/name` following `}` is an interpolated path segment (`f"{base}/alembic.ini"`), and one
# followed by `.` or `/` is a longer path. Everything else that looks like a command is treated as
# one until it is either defined here or listed as foreign — the default has to be suspicion, or
# the check stops catching the thing it exists for.
SLASH = re.compile(r"(?<![\w/\-}])/([a-z][a-z0-9-]{2,})(?![\w./-])")


def defined_commands() -> set[str]:
    return {f"/{p.stem}" for p in (PLUGIN_ROOT / "commands").glob("*.md")}


def test_every_slash_command_the_plugin_emits_is_defined() -> None:
    known = defined_commands() | FOREIGN
    unknown: dict[str, list[str]] = {}
    for path in SEARCHED:
        if not path.is_file():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in SLASH.finditer(line):
                name = f"/{match.group(1)}"
                if name in known:
                    continue
                # A path fragment like `/usr/bin` or a URL tail is not a command.
                if "://" in line or f"{name}/" in line:
                    continue
                unknown.setdefault(name, []).append(f"{path.relative_to(PLUGIN_ROOT)}:{line_no}")

    assert not unknown, "slash commands that no file in commands/ defines: " + "; ".join(
        f"{name} at {', '.join(where)}" for name, where in sorted(unknown.items())
    )


def test_every_command_file_declares_a_description() -> None:
    """The description is what the user sees in the command picker. A command with none is a
    command nobody finds."""
    for path in (PLUGIN_ROOT / "commands").glob("*.md"):
        head = path.read_text(encoding="utf-8").split("---")
        assert len(head) >= 3, f"{path.name} has no frontmatter block"
        assert "description:" in head[1], f"{path.name} declares no description"


def test_every_agent_declares_a_name_and_description() -> None:
    for path in (PLUGIN_ROOT / "agents").glob("*.md"):
        head = path.read_text(encoding="utf-8").split("---")
        assert len(head) >= 3, f"{path.name} has no frontmatter block"
        assert "name:" in head[1], f"{path.name} declares no name"
        assert "description:" in head[1], f"{path.name} declares no description"


def test_nothing_outside_the_manifests_hardcodes_a_version() -> None:
    """A version that must be remembered in three places will be wrong in one of them.

    The CI workflow pins the tag it fetches harness.py from, which made it a third home for the
    version alongside the two manifests. It is a template placeholder now, rendered at install
    time — so this asserts the duplication has not crept back rather than that the copies agree.
    """
    import json

    version = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    pinned = re.compile(r"tdd-harness--v(\d+\.\d+\.\d+)")

    stale: list[str] = []
    for path in [
        *PLUGIN_ROOT.glob("*.md"),
        *(PLUGIN_ROOT / "templates").rglob("*"),
        *(PLUGIN_ROOT / "scripts").glob("*"),
        *(PLUGIN_ROOT / "commands").glob("*.md"),
        *(PLUGIN_ROOT / "agents").glob("*.md"),
    ]:
        if not path.is_file():
            continue
        for found in pinned.findall(path.read_text(encoding="utf-8")):
            if found != version:
                stale.append(f"{path.relative_to(PLUGIN_ROOT)}: {found} (current is {version})")

    assert not stale, "version references that have drifted: " + "; ".join(stale)


@pytest.mark.parametrize("manifest", ["plugin.json", "marketplace.json"])
def test_the_two_manifests_agree_on_the_version(manifest: str) -> None:
    """`claude plugin validate` enforces this, but only if someone runs it. The version is the
    distribution mechanism — a mismatch ships the fix to nobody. See docs/lessons/0004."""
    import json

    plugin = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((PLUGIN_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
    assert entry["version"] == plugin["version"]
