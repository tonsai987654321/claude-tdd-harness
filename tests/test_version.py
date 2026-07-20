"""The version stamp exists and nothing reads it.

`harness_init.py` already writes `.claude/.harness-version` — that half of lesson 0005 landed. But
no check compares it with the installed plugin, so a repo carrying a vendored `.claude/scripts/`
hundreds of lines behind the plugin looks exactly like a current one. The real gap was found by
diffing by hand after a plugin update: 632 vendored lines against 1169, five subcommands missing,
plus a local addition the plugin lacks — a fork, not a stale copy, and nothing said so.

`version` is the read. It stays a warning and never a failure: the vendored copy is allowed to be a
fork, and a repo cloned by someone with no plugin installed is not drifting, it is unmanaged.
Silence in that case, because a warning nobody can act on is one they learn to skip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"


def run_version(root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), "version"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root), **env},
        cwd=root,
    )


def stamp(root: Path, version: str) -> None:
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".harness-version").write_text(f"{version}\n# written by harness_init.py\n", encoding="utf-8")


def fake_plugin(tmp_path: Path, version: str) -> Path:
    p = tmp_path / "plugin"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tdd-harness", "version": version}), encoding="utf-8"
    )
    return p


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".claude" / "cycles").mkdir(parents=True)
    return r


def test_a_stamp_behind_the_installed_plugin_warns(root: Path, tmp_path: Path) -> None:
    stamp(root, "0.6.0")

    r = run_version(root, CLAUDE_PLUGIN_ROOT=str(fake_plugin(tmp_path, "0.9.0")))

    assert r.returncode == 0, "drift is a warning, not a failure — the fork is allowed"
    assert "0.6.0" in r.stdout and "0.9.0" in r.stdout, r.stdout
    assert "harness-init" in r.stdout, f"the warning does not say how to re-sync:\n{r.stdout}"


def test_a_matching_stamp_does_not_warn(root: Path, tmp_path: Path) -> None:
    stamp(root, "0.9.0")

    r = run_version(root, CLAUDE_PLUGIN_ROOT=str(fake_plugin(tmp_path, "0.9.0")))

    assert r.returncode == 0, r.stderr
    assert "!!" not in r.stdout, r.stdout


def test_no_installed_plugin_is_silent_about_drift(root: Path, tmp_path: Path) -> None:
    """A clone on a machine with no plugin is unmanaged, not behind."""
    stamp(root, "0.6.0")

    r = run_version(root, CLAUDE_PLUGIN_ROOT="", HOME=str(tmp_path / "empty-home"))

    assert r.returncode == 0, r.stderr
    assert "!!" not in r.stdout, r.stdout
    assert "0.6.0" in r.stdout, "it should still report what this repo carries"


def test_an_unstamped_repo_says_so_rather_than_crashing(root: Path, tmp_path: Path) -> None:
    r = run_version(root, CLAUDE_PLUGIN_ROOT=str(fake_plugin(tmp_path, "0.9.0")))

    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "unstamped" in r.stdout.lower(), r.stdout


def test_init_sh_reads_the_stamp() -> None:
    """The check has to run somewhere a person sees it, and init.sh is the one command they run.

    Reads the template in the plugin and the rendered script in a scaffolded repo, because this
    file is vendored and `templates/` is not. Hard-coding the template path made this test fail in
    every installed repo — and a red vendored suite makes `init.sh` write gate_verified=false,
    which shuts that repo's gate. The assertion is about the same line either way.
    """
    template = REPO_ROOT / "templates" / "init.sh.tmpl"
    rendered = REPO_ROOT.parent / "init.sh"  # .claude/harness-tests/../../init.sh
    source = next((p for p in (template, rendered) if p.is_file()), None)
    assert source is not None, "neither templates/init.sh.tmpl nor a rendered init.sh was found"

    text = source.read_text(encoding="utf-8")

    assert "version" in text.split("$H")[-1] or '$H" version' in text, "init.sh never calls harness.py version"
