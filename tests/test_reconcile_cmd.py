"""Rebuilding a lost state file should be a command, not an afternoon.

`.claude/state/` is machine-local by design, so a fresh clone renders a board of zeros against a
finished repo. Recovering from that cost two rounds of subagent auditing, four full suite runs and a
reconcile-auditor pass -- and every fact needed was already in the git log, which is where `history`
already goes to derive test-before-code ordering.

The hard part is what reconcile is *not* allowed to do. A `[RED]` commit followed by a `[GREEN]`
commit proves the order the gate exists to prove. It does not prove the suite passes now, that
coverage clears the gate, or that the quality gates ran -- and those are what `done` asserts. A
reconcile that wrote `done` from the log alone would be the exact failure the rest of this branch
attacks: the board asserting something nobody checked, this time with the harness itself as the
liar.

So it reconstructs as far as the log can carry it, `green`, and stops. Closing a cycle stays a
judgement someone makes after watching the suite run.
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


def state_of(root: Path) -> list[dict]:
    return json.loads((root / ".claude" / "state" / "demo-api.json").read_text(encoding="utf-8"))["cycles"]


def row(root: Path, cycle_id: int) -> dict:
    return next(c for c in state_of(root) if int(c["id"]) == cycle_id)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {
                "build_order": 1,
                "runner": "pytest",
                "cycles": [
                    {"id": 0, "title": "scaffold"},
                    {"id": 1, "title": "auth"},
                    {"id": 2, "title": "billing"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    d = tmp_path / "projects" / "demo-api"
    d.mkdir(parents=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    for subject in (
        "test(cycle-0): scaffold [RED]",
        "feat(cycle-0): scaffold [GREEN]",
        "test(cycle-1): auth [RED]",
        "feat(cycle-1): auth [GREEN]",
        "test(cycle-2): billing [RED]",
    ):
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", subject], cwd=d, check=True, capture_output=True
        )
    return tmp_path


def test_a_completed_pair_is_rebuilt_as_green(root: Path) -> None:
    assert run_harness(root, "reconcile", "demo-api", "--write").returncode == 0

    assert row(root, 0)["state"] == "green"


def test_the_rebuilt_evidence_names_commits_that_exist(root: Path) -> None:
    assert run_harness(root, "reconcile", "demo-api", "--write").returncode == 0

    evidence = row(root, 1)["evidence"]
    assert "[RED]" in evidence and "[GREEN]" in evidence, evidence
    # The SHAs it writes must survive the check `cycle ... done` applies to evidence, or reconcile
    # would be manufacturing exactly the unverifiable citation this branch made impossible.
    assert run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", evidence).returncode == 0


def test_reconcile_never_writes_done(root: Path) -> None:
    """The log proves order. It does not prove the suite passes, which is what `done` asserts."""
    assert run_harness(root, "reconcile", "demo-api", "--write").returncode == 0

    assert [c["state"] for c in state_of(root)] != ["done", "done", "done"]
    assert not any(c["state"] == "done" for c in state_of(root))


def test_a_lone_red_is_rebuilt_as_red(root: Path) -> None:
    assert run_harness(root, "reconcile", "demo-api", "--write").returncode == 0

    assert row(root, 2)["state"] == "red", "a cycle whose GREEN was never committed is not implemented"


def test_a_cycle_already_closed_is_not_downgraded(root: Path) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root / "projects" / "demo-api",
        capture_output=True, encoding="utf-8", check=True,
    ).stdout.strip()
    assert run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", f"pytest 9 passed; {sha}").returncode == 0

    assert run_harness(root, "reconcile", "demo-api", "--write").returncode == 0

    assert row(root, 0)["state"] == "done", "reconcile overwrote a verified close with a guess"
    assert "pytest 9 passed" in row(root, 0)["evidence"], "it replaced evidence somebody actually observed"


def test_without_write_it_only_reports(root: Path) -> None:
    r = run_harness(root, "reconcile", "demo-api")

    assert r.returncode == 0, r.stderr
    assert "cycle-0" in r.stdout or "0" in r.stdout
    assert not (root / ".claude" / "state" / "demo-api.json").exists(), "a dry run wrote the state file"


def test_it_says_that_green_is_not_done(root: Path) -> None:
    """The output has to state its own limit, or someone will read the board as finished work."""
    r = run_harness(root, "reconcile", "demo-api")

    assert "done" in r.stdout.lower(), r.stdout
