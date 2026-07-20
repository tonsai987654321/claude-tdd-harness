"""The cycle file can fall behind the git log with nothing checking.

One project's `.claude/cycles/<project>.json` defined ids 0-5 while its log carried complete
RED->GREEN pairs for cycle-10, cycle-11 and cycle-12. `status` reads the cycle file, so the board
could only ever render `n/6`. It was not lying -- it was reporting faithfully on a file that had
stopped describing the project, which is the harder failure to see, because every number on the
board is internally consistent.

The log is the record the harness does not write. `status` scans each project's commit subjects for
`cycle-<n>` and says so when n exceeds the highest id the cycle file defines. It would have fired
the first time the board rendered after cycle 6.
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


def run_harness(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        cwd=root,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {
                "build_order": 1,
                "runner": "pytest",
                "cycles": [{"id": 0, "title": "scaffold"}, {"id": 1, "title": "auth"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def repo_with_subjects(root: Path, *subjects: str) -> Path:
    d = root / "projects" / "demo-api"
    d.mkdir(parents=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    for s in subjects:
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", s], cwd=d, check=True, capture_output=True
        )
    return d


def test_a_log_ahead_of_the_cycle_file_is_reported(root: Path) -> None:
    repo_with_subjects(
        root,
        "test(cycle-1): auth [RED]",
        "test(cycle-4): rate limiting [RED]",
        "feat(cycle-4): rate limiting [GREEN]",
    )

    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "cycle-4" in r.stdout, f"the log runs to cycle-4 and the file defines 1; the board says nothing:\n{r.stdout}"


def test_a_log_that_matches_the_cycle_file_is_quiet(root: Path) -> None:
    repo_with_subjects(root, "test(cycle-0): scaffold [RED]", "feat(cycle-1): auth [GREEN]")

    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "drift" not in r.stdout.lower(), f"warned about a log that matches the file:\n{r.stdout}"


def test_a_project_with_no_repo_yet_is_quiet(root: Path) -> None:
    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "drift" not in r.stdout.lower(), f"warned about a project that has not been cloned:\n{r.stdout}"
