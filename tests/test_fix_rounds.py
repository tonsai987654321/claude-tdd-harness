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


def _state_of(root: Path, cycle_id: int = 0) -> dict:
    import json as _json
    state = _json.loads((root / ".claude" / "state" / "api.json").read_text(encoding="utf-8"))
    return next(c for c in state["cycles"] if str(c["id"]) == str(cycle_id))


def test_requeuing_a_blocked_cycle_clears_the_budget_and_lets_it_build_again(tmp_path: Path) -> None:
    """The breaker must not be a one-way trap: once the cause is fixed, re-queuing the cycle resets
    its attempt count and next_cycle hands it out again — no hand-edit of protected state."""
    _project(tmp_path, attempts=4, cycle_state="red")
    blocked = run(tmp_path, HARNESS, "attempt", "api", "0")  # -> 5, blocks
    assert blocked.returncode != 0 and _state_of(tmp_path)["state"] == "blocked"
    assert run(tmp_path, NEXT_CYCLE).stdout.startswith("BLOCKED")

    reset = run(tmp_path, HARNESS, "cycle", "api", "0", "queued")
    assert reset.returncode == 0, reset.stderr
    assert _state_of(tmp_path).get("attempts", 0) == 0, "re-queue must clear the attempt budget"
    assert run(tmp_path, NEXT_CYCLE).stdout.startswith("BUILD"), "a reset cycle must be dispatchable again"


def test_a_plain_dispatch_does_not_reset_the_budget(tmp_path: Path) -> None:
    """Only `queued` resets. The `red` the orchestrator sets every dispatch must not, or the breaker
    could never accumulate and would never trip."""
    _project(tmp_path, attempts=3, cycle_state="red")
    run(tmp_path, HARNESS, "cycle", "api", "0", "red", "tdd-implementer")
    assert _state_of(tmp_path).get("attempts", 0) == 3, "a red dispatch must not clear the count"


def test_attempt_refuses_an_already_done_cycle(tmp_path: Path) -> None:
    """Counting an attempt against a finished cycle is a caller error, and unguarded it would
    overwrite `done` with `blocked` at the bound."""
    _project(tmp_path, attempts=4, cycle_state="done")
    r = run(tmp_path, HARNESS, "attempt", "api", "0")
    assert r.returncode != 0, "attempt on a done cycle must be refused"
    assert _state_of(tmp_path)["state"] == "done", "the done cycle must not be clobbered to blocked"


def test_attempt_refuses_a_green_cycle_without_clobbering_it(tmp_path: Path) -> None:
    """An attempt is only meaningful against a cycle actually being dispatched (red/queued). A green
    cycle is not being dispatched, so attempting it must be refused — not silently incremented, and
    never flipped to blocked at the bound."""
    _project(tmp_path, attempts=4, cycle_state="green")
    r = run(tmp_path, HARNESS, "attempt", "api", "0")
    assert r.returncode != 0, "attempt on a green cycle must be refused"
    assert _state_of(tmp_path)["state"] == "green", "a green cycle must not be clobbered to blocked"
    assert _attempts(tmp_path) == 4, "a refused attempt must not increment the count"


def test_attempt_refuses_an_already_blocked_cycle(tmp_path: Path) -> None:
    """Re-attempting an already-blocked cycle must be refused too: the way to resume it is to
    re-queue it, which resets the count. A bare re-attempt must not increment further."""
    _project(tmp_path, attempts=5, cycle_state="blocked")
    r = run(tmp_path, HARNESS, "attempt", "api", "0")
    assert r.returncode != 0, "attempt on a blocked cycle must be refused"
    assert _attempts(tmp_path) == 5, "a refused attempt must not increment the count"


def test_next_cycle_still_builds_a_cycle_with_rounds_left(tmp_path: Path) -> None:
    """Below the bound, the cycle is still the next thing to build — the breaker only trips at the end."""
    _project(tmp_path, attempts=2, cycle_state="red")
    out = run(tmp_path, NEXT_CYCLE).stdout
    assert out.startswith("BUILD"), f"a cycle with rounds left must still build, got:\n{out}"
