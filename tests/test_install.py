"""The installer, driven twice.

Every earlier test installed into an empty directory, where "skip if it exists" and "overwrite"
are indistinguishable. That is why a reinstall being a no-op on `harness.py` — the one file a
plugin update exists to replace — survived for two releases. The second install is the interesting
one, so it is the one these tests run.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN_ROOT / "scripts" / "harness_init.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import harness_init  # noqa: E402


def install(target: Path, *extra: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target),
         "--owner", "alice", "--project", "api:pytest:90", *extra],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return proc


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    install(tmp_path)
    return tmp_path


# ------------------------------------------------------------------ the upgrade path


def test_reinstall_resyncs_framework_and_keeps_user_content(repo: Path) -> None:
    """The whole point of the framework/user split.

    A vendored copy that a reinstall refuses to touch strands every fix the plugin will ever
    ship, silently — the repo keeps reporting green against the old gate. The reverse mistake is
    just as bad: an installer that overwrites everything takes the constitution, the glossary and
    the cycle list with it, and those are the only files in the repo nobody can regenerate.
    """
    gate = repo / ".claude" / "scripts" / "harness.py"
    constitution = repo / "CLAUDE.md"
    cycles = repo / ".claude" / "cycles" / "api.json"

    gate.write_text("# a stale vendored copy\n", encoding="utf-8")
    constitution.write_text("# hand-written by the user\n", encoding="utf-8")
    cycles.write_text('{"mine": true}\n', encoding="utf-8")

    install(repo)

    assert gate.read_text(encoding="utf-8") == (PLUGIN_ROOT / "scripts" / "harness.py").read_text(encoding="utf-8")
    assert constitution.read_text(encoding="utf-8") == "# hand-written by the user\n"
    assert cycles.read_text(encoding="utf-8") == '{"mine": true}\n'


def test_reset_overwrites_only_the_named_file(repo: Path) -> None:
    (repo / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    (repo / "CONTEXT.md").write_text("also mine\n", encoding="utf-8")

    install(repo, "--reset", "CLAUDE.md")

    assert "mine\n" != (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert (repo / "CONTEXT.md").read_text(encoding="utf-8") == "also mine\n"


def test_version_stamp_lives_in_a_file_the_installer_rewrites(repo: Path) -> None:
    """A marker in a skip-if-exists file is right once and wrong forever after."""
    stamp = repo / harness_init.VERSION_FILE
    expected = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert stamp.read_text(encoding="utf-8").splitlines()[0] == expected

    stamp.write_text("0.0.1-stale\n", encoding="utf-8")
    install(repo)
    assert stamp.read_text(encoding="utf-8").splitlines()[0] == expected


# ------------------------------------------------------------------ what gets vendored


def test_harness_suite_is_installed(repo: Path) -> None:
    """init.sh runs this suite unconditionally, so its absence must be a broken install.

    The guard that used to wrap it in `if [ -d ... ]` skipped silently in every scaffolded repo,
    and a skipped check reads exactly like a passing one. See docs/lessons/0002.
    """
    installed = sorted(p.name for p in (repo / ".claude" / "harness-tests").glob("test_*.py"))
    expected = sorted(
        p.name for p in (PLUGIN_ROOT / "tests").glob("test_*.py")
        if p.name not in harness_init.NOT_VENDORED
    )
    assert installed == expected
    assert installed, "the scaffolded repo must carry a suite for the gate it vendored"
    # This file drives the installer, which is not vendored. Shipping it would point the
    # scaffolded repo's suite at a script that is not there — red harness, healthy gate.
    assert "test_install.py" not in installed


def test_vendored_shell_scripts_are_lf(repo: Path) -> None:
    """These repos travel. A CRLF init.sh scaffolded on Windows dies on the first Linux CI run."""
    scripts = [repo / "init.sh", *(repo / ".claude" / "scripts").glob("*.sh")]
    assert scripts
    for path in scripts:
        assert b"\r\n" not in path.read_bytes(), f"{path.name} was vendored with CRLF"


def test_scaffolder_is_not_vendored(repo: Path) -> None:
    """It resolves templates relative to __file__ and dies from .claude/scripts/. Lesson 0003."""
    assert not (repo / ".claude" / "scripts" / "harness_init.py").exists()


# ------------------------------------------------------------------ the gate is wired


def test_gate_hook_command_is_portable(repo: Path) -> None:
    """`.claude/settings.json` is committed, so whatever is written here travels to every clone.

    An interpreter resolved to this machine's absolute path works for the author and for nobody
    else — and a hook naming a binary that does not exist is a gate that may never fire, which is
    the one failure the plugin exists to prevent. Everything in the command must be either a name
    resolved from PATH or a path relative to $CLAUDE_PROJECT_DIR.
    """
    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
    ]
    gate = [c for c in commands if "harness.py" in c and c.endswith("gate")]
    assert len(gate) == 1, f"expected exactly one gate hook, got {gate}"

    interpreter = gate[0].split()[0]
    assert interpreter == "python3", f"gate hook must invoke a PATH-resolved python3, got {interpreter!r}"
    assert not re.search(r"[A-Za-z]:[/\\]", gate[0]), f"absolute machine path in the gate hook: {gate[0]}"
    assert "$CLAUDE_PROJECT_DIR" in gate[0], "the script path must be resolved by the editor, not baked in"


def test_reinstall_repoints_rather_than_duplicating_the_hook(repo: Path) -> None:
    """Matching on the exact string would add a second hook whenever the command has been edited.

    Someone whose `python3` is missing is told to point the hook at a working interpreter by hand;
    the next reinstall must repair that one hook, not append a rival beside it. Two gate hooks
    means the gate fires twice, once through an interpreter that may not exist.
    """
    settings_path = repo / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for entry in settings["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            if "harness.py" in hook["command"]:
                hook["command"] = '/some/other/python "$CLAUDE_PROJECT_DIR/.claude/scripts/harness.py" gate'
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    install(repo)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    gate = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
        if "harness.py" in hook["command"]
    ]
    assert len(gate) == 1
    assert "/some/other/python" not in gate[0]


def test_stack_is_declared_once_and_rendered_from_there(tmp_path: Path) -> None:
    """Four places used to name a stack in prose and only one of them was obeyed.

    `{{STACK}}` in the constitution was a placeholder no agent read, while the implementer agent
    carried a different stack as a rule it was told not to reinterpret. Now the config is the
    declaration and CLAUDE.md renders it.
    """
    subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path), "--project", "api:pytest:90",
         "--stack", "Go 1.23, chi, sqlc"],
        capture_output=True, text=True, check=True,
    )
    cfg = json.loads((tmp_path / ".claude" / "harness.json").read_text(encoding="utf-8"))
    assert cfg["stack"] == "Go 1.23, chi, sqlc"
    assert "Go 1.23, chi, sqlc" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_a_stack_with_quotes_still_produces_valid_json(tmp_path: Path) -> None:
    """A config that does not parse falls back to DEFAULT_CONFIG without saying so."""
    subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(tmp_path), "--project", "api:pytest:90",
         "--stack", 'Python 3.12 with "strict" typing'],
        capture_output=True, text=True, check=True,
    )
    cfg = json.loads((tmp_path / ".claude" / "harness.json").read_text(encoding="utf-8"))
    assert '"strict"' in cfg["stack"]


def test_requires_omits_tools_the_chosen_runners_do_not_need(tmp_path: Path) -> None:
    """Defaulting to docker aborted init.sh in the environment check, before the gate self-test.

    The one thing the install is supposed to prove needs neither Docker nor gh.
    """
    install(tmp_path)
    cfg = json.loads((tmp_path / ".claude" / "harness.json").read_text(encoding="utf-8"))
    assert cfg["requires"] == ["python3", "uv"]

    js = tmp_path / "js"
    js.mkdir()
    subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(js), "--project", "web:vitest:80"],
        capture_output=True, text=True, check=True,
    )
    cfg = json.loads((js / ".claude" / "harness.json").read_text(encoding="utf-8"))
    assert cfg["requires"] == ["python3", "uv", "node", "npm"]


# ------------------------------------------------------------------ validation at the boundary


@pytest.mark.parametrize(
    "spec, because",
    [
        ("api:pytst:90", "a typo'd runner is fatal at the first red, one session later"),
        ("api:pytest:200", "a coverage gate above 100 can never be met"),
        ("../escape:pytest:90", "a name with a separator writes outside the cycles directory"),
        ("", "a spec with no name"),
    ],
)
def test_project_spec_is_validated_at_install_time(spec: str, because: str) -> None:
    with pytest.raises(Exception) as exc:
        harness_init.Project.parse(spec)
    assert exc.type.__name__ == "ArgumentTypeError", because


def test_valid_specs_still_parse() -> None:
    assert harness_init.Project.parse("api") == harness_init.Project("api", "pytest", 90)
    assert harness_init.Project.parse("web:vitest:80") == harness_init.Project("web", "vitest", 80)
