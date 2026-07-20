"""The board discards the evidence a blocked cycle carries.

`cmd_cycle` requires evidence only for `done`, but *stores* it for any state that supplies one. So
a `blocked` cycle can hold a full account of why it is blocked -- which DoD items are met, which
one is not -- and `render` prints `-`, because it decides the column on the state before it looks
at the value:

    if c["state"] != "done":
        ev = "-"

A reader of the board learns that a cycle is blocked and nothing about why. `HANDOFF.md` does print
the reason in full, so the two generated files disagree about what the reader is allowed to know,
and `PROGRESS.md` -- the one CLAUDE.md points at as "the board" -- is the stricter for no stated
reason.

`blocked` is the state where the reason is the whole point.
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

BLOCK_REASON = (
    "DoD 1-4 met: pytest 41 passed, cov 94%, ruff clean, mypy --strict clean. "
    "DoD 5 not met: the staging deploy needs a SECRET_KEY this account cannot mint."
)


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
                "cycles": [{"id": 0, "title": "scaffold"}, {"id": 1, "title": "deploy"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_a_blocked_cycles_reason_reaches_the_board(root: Path) -> None:
    assert run_harness(root, "cycle", "demo-api", "1", "blocked", "--evidence", BLOCK_REASON).returncode == 0

    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "SECRET_KEY" in r.stdout, f"the board knows why the cycle is blocked and prints '-':\n{r.stdout}"


def test_a_cycle_with_no_evidence_still_reads_as_a_dash(root: Path) -> None:
    assert run_harness(root, "cycle", "demo-api", "1", "red").returncode == 0

    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    row = next(line for line in r.stdout.splitlines() if line.startswith("| 1 |"))
    assert row.rstrip().endswith("| - |"), row


def test_the_reason_does_not_break_the_table(root: Path) -> None:
    """Evidence is free text and the board is a markdown table. A pipe would split the row."""
    assert (
        run_harness(root, "cycle", "demo-api", "1", "blocked", "--evidence", "a | b | c, and\nmore").returncode == 0
    )

    r = run_harness(root, "status")

    row = next(line for line in r.stdout.splitlines() if line.startswith("| 1 |"))
    assert row.count("|") == 8, f"the evidence text added columns to the row:\n{row}"


def test_a_done_with_no_evidence_is_still_called_out(root: Path) -> None:
    """The MISSING marker is the anti-false-done signal; widening the column must not lose it."""
    state = root / ".claude" / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "demo-api.json").write_text(
        json.dumps(
            {
                "project": "demo-api",
                "gate": {"state": "SHUT"},
                "coverage": None,
                "cycles": [
                    {"id": 0, "title": "scaffold", "state": "done", "agent": "-", "tokens": 0, "evidence": ""},
                    {"id": 1, "title": "deploy", "state": "queued", "agent": "-", "tokens": 0, "evidence": ""},
                ],
            }
        ),
        encoding="utf-8",
    )

    r = run_harness(root, "status")

    assert "MISSING" in r.stdout, r.stdout
