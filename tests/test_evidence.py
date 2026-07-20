"""Evidence has to be checkable, not merely present.

`cmd_cycle` refuses a `done` with no evidence, which is the right instinct. But any non-empty
string satisfies it: `--evidence "yes"` closes a cycle, and so does a citation of two SHAs that
exist in no repository. The harness's own claim is that "a done nobody can check is
indistinguishable from a lie" -- and the check is on the *presence* of evidence, not its truth.

The cheapest check that closes the most common false shape: evidence names commits, so resolve
them. Anything shaped like a SHA gets `git cat-file -t` in that project, and the `done` is refused
if any fails. It does not make the cited commits the *right* ones -- nothing here can -- but it
moves a fabricated SHA from undetectable to impossible, and git is the one store in this system the
harness does not write.
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
                "cycles": [{"id": 0, "title": "scaffold"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def project_repo(root: Path) -> Path:
    """A real git repo at projects/demo-api with one commit, so a real SHA exists to cite."""
    d = root / "projects" / "demo-api"
    d.mkdir(parents=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    (d / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "test(cycle-0): scaffold [RED]"], cwd=d, check=True, capture_output=True)
    return d


def head_sha(d: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, encoding="utf-8", check=True
    ).stdout.strip()


def test_a_sha_that_resolves_is_accepted(root: Path, project_repo: Path) -> None:
    sha = head_sha(project_repo)

    r = run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", f"pytest 5 passed; {sha} [RED]")

    assert r.returncode == 0, f"{r.stdout}{r.stderr}"


def test_a_sha_that_resolves_to_nothing_is_refused(root: Path, project_repo: Path) -> None:
    r = run_harness(
        root, "cycle", "demo-api", "0", "done", "--evidence", "pytest 5 passed; deadbee [RED] -> f00dfac3 [GREEN]"
    )

    assert r.returncode != 0, "a cycle closed on commits that exist in no repository"
    assert "deadbee" in r.stderr, r.stderr


def test_one_bad_sha_among_good_ones_still_refuses(root: Path, project_repo: Path) -> None:
    sha = head_sha(project_repo)

    r = run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", f"{sha} [RED] -> deadbee [GREEN]")

    assert r.returncode != 0, "half-fabricated evidence closed the cycle"


def test_evidence_naming_no_commits_is_left_alone(root: Path, project_repo: Path) -> None:
    """Not every evidence string cites a SHA. This check is about the ones that do."""
    r = run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", "pytest 24 passed, cov 93%")

    assert r.returncode == 0, f"{r.stdout}{r.stderr}"


def test_citing_a_sha_when_the_project_has_no_history_is_refused(root: Path) -> None:
    (root / "projects" / "demo-api").mkdir(parents=True)

    r = run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", "deadbee [RED] -> f00dfac3 [GREEN]")

    assert r.returncode != 0, "evidence named commits in a project that has no commits"
