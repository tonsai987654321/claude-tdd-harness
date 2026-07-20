"""A freshly installed repo shows a reader nothing.

`PROGRESS.md` and `HANDOFF.md` do not exist until somebody runs `status --write` and `handoff`. So
between installing the harness and finishing the first cycle, the repo has no board at all -- and
now that the board is committed rather than ignored, that gap is what a reviewer cloning the repo
actually sees.

The install is also the moment the layout is most confusing: the constitution names both files, the
end-of-session ritual regenerates both, and neither is there. Writing them at install time costs one
call each and makes the layout true from the first commit.

They are generated from state, so an install-time copy is not a claim about work — it is a board
with every cycle queued, which is exactly what an install is.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN_ROOT / "scripts" / "harness_init.py"


def install(target: Path) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target),
         "--owner", "alice", "--project", "api:pytest:90"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return proc


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    install(tmp_path)
    return tmp_path


def test_the_board_exists_immediately_after_install(repo: Path) -> None:
    assert (repo / "PROGRESS.md").is_file(), "a fresh clone of a new repo shows a reader no board at all"


def test_the_handoff_exists_immediately_after_install(repo: Path) -> None:
    assert (repo / "HANDOFF.md").is_file()


def test_the_board_lists_the_installed_project(repo: Path) -> None:
    """An empty file would satisfy the check above while telling a reader nothing."""
    board = (repo / "PROGRESS.md").read_text(encoding="utf-8")

    assert "api" in board, board


def test_the_board_claims_no_finished_work(repo: Path) -> None:
    """It is generated from state, so it must read as an install: everything queued, nothing done."""
    board = (repo / "PROGRESS.md").read_text(encoding="utf-8")

    assert "queued" in board, board
    assert "cycles 0/" in board, f"a fresh install rendered completed cycles:\n{board}"


def test_a_reinstall_does_not_overwrite_a_real_board(repo: Path) -> None:
    """The board carries observed work by the second install. Regenerating it here would discard
    state the installer never read."""
    board = repo / "PROGRESS.md"
    board.write_text("# a board describing real finished work\n", encoding="utf-8")

    install(repo)

    assert "real finished work" in board.read_text(encoding="utf-8"), "the installer overwrote a live board"
