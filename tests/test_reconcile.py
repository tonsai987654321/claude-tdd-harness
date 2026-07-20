"""`load_state` has to reconcile against the cycle file every time, not only on first write.

The seed path runs once: `load_state` returns the state file verbatim the moment it exists. So a
cycle appended to `.claude/cycles/<project>.json` afterwards is never added to the state. It cannot
be marked `done`, cannot be marked anything, and renders on no board -- while `PROGRESS.md` reads
`cycles 0/8` with every row accounted for, so nothing looks wrong.

That is the failure this harness exists to refuse: a board that reads correct and describes
nothing. A real cycle was implemented against a state file that had no row for it, and the only way
to notice was diffing two files by hand.

The reconcile is a left join: carry existing rows through by id, append ids the cycle file defines
and the state lacks, and *flag* -- never silently drop -- an id the state holds that the cycle file
no longer defines. Dropping it would delete evidence of work that happened.
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


def write_cycles(root: Path, *ids: int) -> None:
    cycles = root / ".claude" / "cycles"
    cycles.mkdir(parents=True, exist_ok=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {
                "build_order": 1,
                "runner": "pytest",
                "cycles": [{"id": i, "title": f"cycle {i}"} for i in ids],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def state_of(root: Path) -> dict:
    return json.loads((root / ".claude" / "state" / "demo-api.json").read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A state file that exists and predates the cycle file growing."""
    write_cycles(tmp_path, 0, 1)
    assert run_harness(tmp_path, "cycle", "demo-api", "0", "red").returncode == 0
    assert [c["id"] for c in state_of(tmp_path)["cycles"]] == [0, 1]
    return tmp_path


def test_a_cycle_appended_after_the_state_file_exists_becomes_visible(root: Path) -> None:
    write_cycles(root, 0, 1, 2)

    r = run_harness(root, "cycle", "demo-api", "2", "red")

    assert r.returncode == 0, f"cycle 2 is invisible to the state: {r.stdout}{r.stderr}"
    assert [c["id"] for c in state_of(root)["cycles"]] == [0, 1, 2]


def test_reconciling_does_not_reset_the_rows_that_already_existed(root: Path) -> None:
    write_cycles(root, 0, 1, 2)

    assert run_harness(root, "cycle", "demo-api", "2", "red").returncode == 0

    row0 = next(c for c in state_of(root)["cycles"] if c["id"] == 0)
    assert row0["state"] == "red", "the appended id overwrote work already recorded"


def test_an_id_the_cycle_file_drops_is_flagged_and_kept(root: Path) -> None:
    assert run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 5 passed").returncode == 0
    write_cycles(root, 0)

    assert run_harness(root, "cycle", "demo-api", "0", "green").returncode == 0

    row1 = next((c for c in state_of(root)["cycles"] if c["id"] == 1), None)
    assert row1 is not None, "a cycle with recorded evidence was dropped when the cycle file shrank"
    assert row1.get("orphan") is True, "the row survives but nothing says the cycle file no longer defines it"
    assert row1["evidence"] == "pytest 5 passed"


def test_the_board_says_when_a_row_no_longer_has_a_cycle_defining_it(root: Path) -> None:
    assert run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 5 passed").returncode == 0
    write_cycles(root, 0)

    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "orphan" in r.stdout.lower(), (
        f"the board renders an undefined cycle as though it were normal:\n{r.stdout}"
    )
