"""A cycle that cannot be made to pass must stop, not grind.

The orchestrator retries a failed cycle with a fresh implementer. Left unbounded, an unattended loop
(the auto-resume tick) can rework the same cycle forever, burning the whole run on one stuck cycle.
The bound lived only in the orchestrator's prompt — "two rework rounds, then blocked" — which is a
prior, not a constraint, and does not survive a compaction or a resumed session that never read it.

So the attempt count is durable state. `harness.py attempt` increments it and reports where the
cycle stands: keep going, escalate to a stronger model for the next round, or stop. `next_cycle`
reads the same count, so a cycle that has exhausted its rounds surfaces as BLOCKED to a resuming
session instead of being handed out to grind again.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"
NEXT_CYCLE = REPO_ROOT / "scripts" / "next_cycle.py"


def run(root: Path, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def _project(root: Path, *, attempts: int | None = None, cycle_state: str = "red") -> None:
    (root / ".claude" / "cycles").mkdir(parents=True)
    (root / ".claude" / "cycles" / "api.json").write_text(
        json.dumps({"build_order": 1, "runner": "pytest",
                    "cycles": [{"id": 0, "title": "auth"}]}),
        encoding="utf-8")
    (root / ".claude" / "state").mkdir(parents=True)
    row: dict = {"id": 0, "title": "auth", "state": cycle_state}
    if attempts is not None:
        row["attempts"] = attempts
    (root / ".claude" / "state" / "api.json").write_text(
        json.dumps({"project": "api", "gate": {"state": "OPEN"}, "coverage": None, "cycles": [row]}),
        encoding="utf-8")


def _attempts(root: Path) -> int:
    state = json.loads((root / ".claude" / "state" / "api.json").read_text(encoding="utf-8"))
    return next(c for c in state["cycles"] if str(c["id"]) == "0").get("attempts", 0)


def test_attempt_increments_durably(tmp_path: Path) -> None:
    _project(tmp_path)
    assert run(tmp_path, HARNESS, "attempt", "api", "0").returncode == 0
    assert _attempts(tmp_path) == 1
    run(tmp_path, HARNESS, "attempt", "api", "0")
    assert _attempts(tmp_path) == 2, "the count must persist across invocations, not reset"


def test_attempt_signals_escalation_then_block(tmp_path: Path) -> None:
    """Bound is 5: rounds 1–3 keep going, round 4 escalates to a stronger model, round 5 blocks."""
    _project(tmp_path, attempts=3)
    escalate = run(tmp_path, HARNESS, "attempt", "api", "0")  # -> 4
    assert "ESCALATE" in escalate.stdout, f"round 4 must call for a stronger model:\n{escalate.stdout}"

    block = run(tmp_path, HARNESS, "attempt", "api", "0")  # -> 5
    assert block.returncode != 0, "round 5 must fail so the loop cannot treat it as go-ahead"
    assert "BLOCKED" in block.stdout + block.stderr, "round 5 must say the cycle is blocked"


def test_next_cycle_reports_an_exhausted_cycle_as_blocked(tmp_path: Path) -> None:
    """A resuming session must see BLOCKED, not be handed the stuck cycle to grind again."""
    _project(tmp_path, attempts=5, cycle_state="red")
    out = run(tmp_path, NEXT_CYCLE).stdout
    assert out.startswith("BLOCKED"), f"an exhausted cycle must surface as BLOCKED, got:\n{out}"
    assert "api" in out and "0" in out


def test_next_cycle_still_builds_a_cycle_with_rounds_left(tmp_path: Path) -> None:
    """Below the bound, the cycle is still the next thing to build — the breaker only trips at the end."""
    _project(tmp_path, attempts=2, cycle_state="red")
    out = run(tmp_path, NEXT_CYCLE).stdout
    assert out.startswith("BUILD"), f"a cycle with rounds left must still build, got:\n{out}"
