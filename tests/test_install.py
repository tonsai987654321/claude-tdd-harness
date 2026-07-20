"""The installer, driven twice.

Every earlier test installed into an empty directory, where "skip if it exists" and "overwrite"
are indistinguishable. That is why a reinstall being a no-op on `harness.py` — the one file a
plugin update exists to replace — survived for two releases. The second install is the interesting
one, so it is the one these tests run.
"""

from __future__ import annotations

import json
import re
import shutil
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


def test_init_sh_leaves_nothing_untracked(repo: Path) -> None:
    """`init.sh` runs the vendored suite, which writes __pycache__ and .pytest_cache into the
    user's repo — a repo that may contain no Python of its own. These are portfolio repos read by
    reviewers; a committed byte-cache is noticed. Asked of git rather than asserted against a
    string, so the check is about the effect and not about the wording of the block."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    for junk in ("__pycache__/x.pyc", ".pytest_cache/v/cache/lastfailed",
                 ".claude/harness-tests/__pycache__/y.pyc"):
        path = repo / junk
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    untracked = subprocess.run(
        ["git", "status", "--porcelain", "--ignored=no"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "pycache" not in untracked, untracked
    assert "pytest_cache" not in untracked, untracked


def test_a_gitignore_block_from_an_older_version_is_topped_up(repo: Path) -> None:
    """The block has a schema that grows, and 'the marker is already there' skipped the whole
    thing — so a repo scaffolded before __pycache__/ was added kept a block that no longer
    covered what init.sh creates. Same mistake as never rewriting harness.json (lesson 0008)."""
    gitignore = repo / ".gitignore"
    old_block = "\n# --- TDD harness ---\n.claude/state/\nprojects/\n"
    gitignore.write_text("node_modules\n" + old_block, encoding="utf-8")

    install(repo)

    text = gitignore.read_text(encoding="utf-8")
    assert "__pycache__/" in text
    assert "node_modules" in text, "the user's own rules must survive"
    assert text.count("--- TDD harness ---") == 1, "the original block must not be duplicated"

    # And topping up must not repeat itself on the next run.
    install(repo)
    assert gitignore.read_text(encoding="utf-8").count("__pycache__/") == 1


def test_installed_docs_do_not_link_to_files_that_are_not_installed(repo: Path) -> None:
    """FLOW.md ships into every scaffolded repo, and it linked to one of the plugin's own lessons.

    Those are the plugin's history, deliberately not installed — so the link was broken in every
    repo but this one, which is "documentation that lies", the failure CLAUDE.md names. Checked
    where the document actually lands rather than where it was written.
    """
    import re

    broken = []
    for doc in (repo / "docs").rglob("*.md"):
        for target in re.findall(r"\]\(([^)#][^)]*)\)", doc.read_text(encoding="utf-8")):
            if target.startswith(("http", "mailto:")):
                continue
            if not (doc.parent / target).exists():
                broken.append(f"{doc.relative_to(repo)} -> {target}")

    assert not broken, "links that do not resolve in a scaffolded repo: " + "; ".join(broken)


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
    assert interpreter in {"python3", "uv"}, (
        f"gate hook must name a PATH-resolved python3, or uv which supplies one, got {interpreter!r}"
    )
    assert not re.search(r"[A-Za-z]:[/\\]", gate[0]), f"absolute machine path in the gate hook: {gate[0]}"
    assert "$CLAUDE_PROJECT_DIR" in gate[0], "the script path must be resolved by the editor, not baked in"


def test_the_hook_falls_back_to_uv_when_python3_is_absent(monkeypatch) -> None:
    """A machine with uv and no python3 is exactly the machine init.sh now produces, since it
    offers to install uv and nothing else. The hook has to be expressible there — uv fetches 3.12
    itself, and the command stays PATH-resolved rather than pinning this machine's interpreter."""
    real = shutil.which

    def only_uv(name, *args, **kwargs):
        return None if name == "python3" else real(name, *args, **kwargs)

    monkeypatch.setattr(harness_init.shutil, "which", only_uv)
    command, note = harness_init.gate_interpreter()

    assert command.startswith("uv run")
    assert "3.12" in command
    assert note and "uv" in note


def test_the_hook_stays_portable_when_neither_is_found(monkeypatch) -> None:
    """With neither available there is no good answer, and an absolute path is the bad one: it
    would be committed into settings.json and be wrong for every other clone. Keep the portable
    name, say so loudly, and let init.sh's probe of the wired command be the thing that refuses."""
    monkeypatch.setattr(harness_init.shutil, "which", lambda *args, **kwargs: None)
    command, note = harness_init.gate_interpreter()

    assert command == "python3"
    assert note and "uv" in note


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


def test_a_runner_the_repo_added_itself_can_be_scaffolded(repo: Path) -> None:
    """PLAYBOOK has always said a new runner is a config entry and nothing else.

    The installer disagreed: it validated against a tuple hardcoded in its own source, so a repo
    that had added `gotest` to .claude/harness.json still could not scaffold a project with it —
    and the error told the user to do the thing they had already done. Same shape as lesson 0007:
    validation that hardcodes what the config was supposed to own.
    """
    config = repo / ".claude" / "harness.json"
    cfg = json.loads(config.read_text(encoding="utf-8"))
    cfg["runners"]["gotest"] = {
        "cmd": ["go", "test", "./..."], "red_exit_codes": [1],
        "writable_hint": "internal/", "quality": [["go", "vet", "./..."]],
    }
    config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    install(repo, "--project", "svc:gotest:80")

    cycle = json.loads((repo / ".claude" / "cycles" / "svc.json").read_text(encoding="utf-8"))
    assert cycle["runner"] == "gotest"


def test_a_runner_nothing_defines_is_still_refused(repo: Path) -> None:
    """The validation is not gone, it just reads the right list. A typo must still be caught at
    install time rather than at the first `red`, one session later."""
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(repo), "--project", "svc:pytst:80"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode != 0
    assert "pytst" in proc.stderr


def test_valid_specs_still_parse() -> None:
    assert harness_init.Project.parse("api") == harness_init.Project("api", "pytest", 90)
    assert harness_init.Project.parse("web:vitest:80") == harness_init.Project("web", "vitest", 80)
