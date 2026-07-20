"""The board is the artifact that shows the discipline, and it was the one artifact that did not ship.

`CLAUDE.md` tells the user their reviewer is "an interviewer reading a GitHub repo", and lists
`PROGRESS.md` in the layout as though it were part of that repo. The installed `.gitignore` then
hid it. An interviewer cloning a finished repo saw a board of zeros -- which is exactly the state a
reconcile session started in.

`HANDOFF.md` and `.claude/state/` stay ignored, and the reason is in the code rather than in taste:
`cmd_handoff` stamps `strftime` into its header on every render, so the file is dirty after every
run whether or not anything changed, and the state JSON is rewritten constantly. `cmd_status` writes
no timestamp. Only one of the three carries information at the rate it changes.

Retiring an ignore line is *not* automated. The top-up path adds lines a repo is missing, and its
failure mode is ignoring too much. Removing lines has the opposite failure mode -- committing a file
someone deliberately hid -- and that is not worth automating to save one line of hand-editing.
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


def ignore_lines(repo: Path) -> set[str]:
    return {ln.strip() for ln in (repo / ".gitignore").read_text(encoding="utf-8").splitlines()}


def test_a_fresh_install_lets_the_board_ship(repo: Path) -> None:
    assert "PROGRESS.md" not in ignore_lines(repo), "the artifact CLAUDE.md points the reviewer at is hidden from them"


def test_the_handoff_and_the_state_stay_local(repo: Path) -> None:
    """Both are rewritten on every run — HANDOFF.md stamps the time into its own header."""
    lines = ignore_lines(repo)
    assert "HANDOFF.md" in lines
    assert ".claude/state/" in lines


def test_an_existing_repo_is_told_rather_than_edited(repo: Path) -> None:
    """The one case that matters: a repo installed before this change still hides its board."""
    gitignore = repo / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8") + "PROGRESS.md\n", encoding="utf-8")

    out = install(repo).stdout

    assert "PROGRESS.md" in ignore_lines(repo), "the installer removed an ignore rule on its own"
    assert "PROGRESS.md" in out, f"the installer said nothing about a rule it no longer ships:\n{out}"
    assert "no longer" in out.lower(), out


def test_a_repo_that_does_not_hide_the_board_is_not_nagged(repo: Path) -> None:
    out = install(repo).stdout

    assert "no longer" not in out.lower(), f"warned about a rule the repo does not have:\n{out}"
