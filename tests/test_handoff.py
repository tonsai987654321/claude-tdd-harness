"""HANDOFF.md is the first thing the next session reads. It must not lie about blocked work.

`cmd_handoff` only sets a next action for a project with pending cycles *and no blocked ones* --
correctly, since you should not skip past a blocked cycle. But the fallback when no next action was
found is unconditional: "All cycles are done and evidenced." So a board where every project is
either finished or blocked produces a handoff that declares completion three lines above the
blockers it just listed, and disagrees with `next_cycle.py`, which correctly still points at the
blocked cycle.

That is the exact failure mode CLAUDE.md names first -- documentation that lies -- in the generated
file the next session trusts most.
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
def root_with_a_blocked_cycle(tmp_path: Path) -> Path:
    """One project: cycle 0 done and evidenced, cycle 1 blocked. Nothing is dispatchable."""
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {
                "build_order": 1,
                "runner": "pytest",
                "cycles": [
                    {"id": 0, "title": "scaffold"},
                    {"id": 1, "title": "DoD: coverage, prettier clean"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert run_harness(tmp_path, "cycle", "demo-api", "0", "done", "--evidence", "pytest 5 passed").returncode == 0
    assert run_harness(tmp_path, "cycle", "demo-api", "1", "blocked", "--evidence", BLOCK_REASON).returncode == 0
    return tmp_path


BLOCK_REASON = "prettier exits 1 on 39 tracked files; nothing enforces it"


def test_handoff_does_not_claim_completion_when_a_cycle_is_blocked(root_with_a_blocked_cycle: Path) -> None:
    """The next action must not say everything is done while a cycle is blocked."""
    proc = run_harness(root_with_a_blocked_cycle, "handoff")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    doc = (root_with_a_blocked_cycle / "HANDOFF.md").read_text(encoding="utf-8")
    next_action = doc.split("## Next action", 1)[1].split("##", 1)[0]

    assert "All cycles are done and evidenced" not in next_action, (
        "handoff declared completion while cycle 1 is blocked:\n" + doc
    )


def test_handoff_surfaces_why_a_cycle_is_blocked(root_with_a_blocked_cycle: Path) -> None:
    """A blocker naming only the cycle title says which cycle, never why.

    The reason is recorded in state; if the handoff drops it, the next session pays to re-derive
    exactly what this one already established -- the waste the constitution exists to prevent.
    """
    proc = run_harness(root_with_a_blocked_cycle, "handoff")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    doc = (root_with_a_blocked_cycle / "HANDOFF.md").read_text(encoding="utf-8")

    assert BLOCK_REASON in doc, "handoff listed the blocker but not the recorded reason:\n" + doc
