"""`link_projects.sh`, actually run.

docs/lessons/0001 recorded that this script had never been executed once. It still had not been,
and it did not work: it reads owner, projects_dir and the project names line by line out of an
embedded Python program, and on Windows `read` hands back the `\\r` that `print()` emits. Every
value carried one, so `projects\\r` matched no directory and every project reported MISSING —
and `gh repo clone alice\\r/api\\r` could not have succeeded either.

`--status` is the read-only mode, so these tests never touch the network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "link_projects.sh"

# bash is not optional for this harness — init.sh is a bash script and is the documented entry
# point — so its absence is a broken environment rather than a reason to skip.
BASH = shutil.which("bash")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "scripts").mkdir(parents=True)
    (tmp_path / ".claude" / "cycles").mkdir()
    shutil.copy2(SCRIPT, tmp_path / ".claude" / "scripts" / "link_projects.sh")
    (tmp_path / ".claude" / "harness.json").write_text(
        json.dumps({"owner": "alice", "projects_dir": "projects"}), encoding="utf-8"
    )
    (tmp_path / ".claude" / "cycles" / "api.json").write_text(
        json.dumps({"project": "api", "build_order": 1, "cycles": []}), encoding="utf-8"
    )
    return tmp_path


def status(repo: Path) -> str:
    assert BASH, "bash is required to run the harness at all"
    proc = subprocess.run(
        [BASH, str(repo / ".claude" / "scripts" / "link_projects.sh"), "--status"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=repo,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_a_checked_out_project_is_reported_present(repo: Path) -> None:
    """The line that proves the config values survived the read loop intact.

    With a trailing CR on `projects_dir`, the path never resolves and this project is reported
    MISSING however correctly it is checked out.
    """
    project = repo / "projects" / "api"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)

    out = status(repo)
    assert "api" in out
    assert "MISSING" not in out


def test_a_directory_with_no_checkout_is_not_called_missing(repo: Path) -> None:
    """Reporting this as MISSING sent the user into `gh repo clone` against a non-empty
    directory, which fails."""
    (repo / "projects" / "api").mkdir(parents=True)

    out = status(repo)
    assert "MISSING" not in out
    assert "no git checkout" in out


def test_an_absent_project_is_reported_missing(repo: Path) -> None:
    assert "MISSING" in status(repo)
