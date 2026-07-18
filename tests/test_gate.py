"""What the TDD gate guards.

The one rule is "no production code without a failing test on record", and the gate is the only
thing that enforces it. So the gate's *boundary* is a decision, not an accident: everything that
ships and encodes behaviour has to be inside it, and everything that cannot be driven by a test has
to be outside it, or the rule is quietly narrower than CLAUDE.md claims.

These drive the real PreToolUse contract -- a JSON payload on stdin, exit 2 to block, exit 0 to
allow -- against a throwaway harness root, so the repo's own gate state is never touched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "harness.py"

BLOCKED, ALLOWED = 2, 0


@pytest.fixture
def gated_root(tmp_path: Path) -> Path:
    """A throwaway harness root whose gate is SHUT (the default with no state on record)."""
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    # harness.py locates the owning harness by walking up for this file, so the marker has to
    # exist -- without it the gate decides the write is outside any harness and waves it through.
    (scripts / "harness.py").touch()
    return tmp_path


def run_gate(root: Path, rel: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_input": {"file_path": str(root / rel)}})
    return subprocess.run(
        [sys.executable, str(HARNESS), "gate"],
        input=payload,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
    )


def test_gate_blocks_the_migration_runner(gated_root: Path) -> None:
    """`alembic/env.py` runs on every deploy, so it is production code and the gate must hold it.

    Container boot is `alembic upgrade head`, and env.py is what that executes -- a bug there takes
    out every table in the database rather than one endpoint. It has already broken this repo twice
    (a URL that died in ConfigParser; a transaction Alembic declined to commit, silently creating
    nothing), both times through a gate that never looked.
    """
    proc = run_gate(gated_root, "projects/demo-api/alembic/env.py")

    assert proc.returncode == BLOCKED, f"exit {proc.returncode}: {proc.stdout}{proc.stderr}"


def test_gate_releases_the_migration_runner_once_a_test_is_red(gated_root: Path) -> None:
    """Gating env.py is only legitimate if RED still opens it — otherwise it is unfixable, not safe."""
    state = gated_root / ".claude" / "state"
    state.mkdir(parents=True)
    (state / "demo-api.json").write_text(
        json.dumps({"project": "demo-api", "gate": {"state": "OPEN"}, "cycles": []}),
        encoding="utf-8",
    )

    proc = run_gate(gated_root, "projects/demo-api/alembic/env.py")

    assert proc.returncode == ALLOWED, f"exit {proc.returncode}: {proc.stdout}{proc.stderr}"


def test_gate_leaves_alembic_config_alone(gated_root: Path) -> None:
    """The boundary is "ships and encodes behaviour". alembic.ini ships but no test can drive it.

    Guards the regex against widening to `alembic/`, which would block scaffolding for no gain.
    """
    proc = run_gate(gated_root, "projects/demo-api/alembic.ini")

    assert proc.returncode == ALLOWED, f"exit {proc.returncode}: {proc.stdout}{proc.stderr}"
