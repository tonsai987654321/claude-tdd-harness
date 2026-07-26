"""`done` means green confirmed, not just a test committed.

The done-path already refuses a `done` with no evidence, with unresolvable SHAs, with the RED test
never committed, or under the coverage gate. None of those prove the suite is *green*. An agent can
open the gate with `red`, write the code, and mark the cycle `done` — skipping `green` entirely. The
gate stays OPEN forever, but the board reads done: a finished mark on a cycle whose suite was never
confirmed to pass.

`green` is the only thing that shuts the gate, and it does so only on a passing suite (with the code
actually needed, since ADR-0010). State is a protected path, so SHUT cannot be forged. So the gate
being OPEN at `done` is a mechanical proof that green never confirmed — refuse it.

This is project-level "the suite is not currently red"; it does not re-run the suite (that would
break `done`'s contract as a state transition and every done-test that closes a cycle without a
runner). A suite broken *after* a green without reopening the gate stays the cycle-reviewer's job —
the layered cover of ADR-0003.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"


def run_harness(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def _write(root: Path, gate_state: str) -> str:
    """A project with a committed RED test and state whose gate is `gate_state`. Returns the RED SHA."""
    (root / ".claude" / "cycles").mkdir(parents=True)
    (root / ".claude" / "cycles" / "demo-api.json").write_text(
        json.dumps({"build_order": 1, "runner": "pytest",
                    "cycles": [{"id": 0, "title": "auth", "first_test": "tests/test_auth.py"}]}),
        encoding="utf-8")

    d = root / "projects" / "demo-api"
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test_auth.py").write_text("def test_auth():\n    assert True\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "test(cycle-0): auth [RED]"],
    ):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, encoding="utf-8", check=True
    ).stdout.strip()

    (root / ".claude" / "state").mkdir(parents=True)
    gate: dict = {"state": gate_state}
    if gate_state == "OPEN":
        gate["test"] = ["tests/test_auth.py"]  # opened on THIS cycle's declared test
    if gate_state == "SHUT":
        gate["closed_at"] = 1.0  # green sets this; a real close, not the default
    (root / ".claude" / "state" / "demo-api.json").write_text(
        json.dumps({
            "project": "demo-api",
            "gate": gate,
            "coverage": None,
            "last_red_test": ["tests/test_auth.py"],
            "cycles": [{"id": 0, "title": "auth", "state": "green"}],
        }),
        encoding="utf-8")
    return sha


def test_done_is_refused_while_the_gate_is_open(tmp_path: Path) -> None:
    """A RED is open and no green has shut it — the suite was never confirmed to pass."""
    sha = _write(tmp_path, "OPEN")

    r = run_harness(tmp_path, "cycle", "demo-api", "0", "done", "--evidence", f"pytest 1 passed; {sha} [RED]")

    assert r.returncode != 0, "a cycle closed while its gate was still OPEN — green never confirmed"
    assert "green" in r.stderr.lower(), f"the refusal must point at the missing green:\n{r.stderr}"


def test_done_is_allowed_when_the_open_gate_belongs_to_another_cycle(tmp_path: Path) -> None:
    """The gate is per-project. An earlier, genuinely-green cycle must still close while a later
    cycle holds the gate open — the refusal is scoped to the cycle that opened the gate, not the bare
    project state (that false 'green never confirmed' was the bug this fixes)."""
    sha = _write(tmp_path, "OPEN")
    # Redeclare: two cycles, and the open gate was opened on cycle 1's test, not cycle 0's.
    (tmp_path / ".claude" / "cycles" / "demo-api.json").write_text(
        json.dumps({"build_order": 1, "runner": "pytest", "cycles": [
            {"id": 0, "title": "auth", "first_test": "tests/test_auth.py"},
            {"id": 1, "title": "billing", "first_test": "tests/test_billing.py"},
        ]}),
        encoding="utf-8")
    state_p = tmp_path / ".claude" / "state" / "demo-api.json"
    st = json.loads(state_p.read_text(encoding="utf-8"))
    st["gate"]["test"] = ["tests/test_billing.py"]  # gate is open for cycle 1, not cycle 0
    st["cycles"] = [{"id": 0, "title": "auth", "state": "green"}, {"id": 1, "title": "billing", "state": "red"}]
    state_p.write_text(json.dumps(st), encoding="utf-8")

    r = run_harness(tmp_path, "cycle", "demo-api", "0", "done", "--evidence", f"pytest 1 passed; {sha} [RED]")

    assert r.returncode == 0, f"cycle 0 greened; a later cycle's open gate must not block it:\n{r.stdout}{r.stderr}"


def test_done_is_allowed_once_green_has_shut_the_gate(tmp_path: Path) -> None:
    """The honest flow: green ran, shut the gate, and the cycle closes on otherwise-valid evidence."""
    sha = _write(tmp_path, "SHUT")

    r = run_harness(tmp_path, "cycle", "demo-api", "0", "done", "--evidence", f"pytest 1 passed; {sha} [RED]")

    assert r.returncode == 0, f"a green, committed, evidenced cycle was refused:\n{r.stdout}{r.stderr}"
