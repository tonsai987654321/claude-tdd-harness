"""A repo can be healthy locally and enforce nothing.

`init.sh` proves the local gate hook is wired, but every local check is evidence about a cooperative
agent — the CI `history` check is the only actual boundary (it runs where the agent does not, and
reads a git log the harness never writes). The installer renders the ordering workflow to
`docs/ci/tdd-ordering.yml`, inert until a human copies it into `.github/workflows/`. So a repo can
pass `init.sh` fully green with zero boundary enforcement, and nothing said so.

`harness.py version` — already the one advisory `init.sh` surfaces even under `--quiet`, and already
the place a repo's harness posture is reported — now also notes when no workflow invokes
`harness.py history`. Advisory, never a failure: a repo may opt out of CI deliberately, and a warning
that cannot be acted on becomes noise.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"

# The advisory's load-bearing phrase, so the test pins the message a human reads, not an exit code.
ADVISORY = "evidence, not a boundary"


def version(root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "version"],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )
    assert proc.returncode == 0, f"version must never fail: {proc.stderr}"
    return proc.stdout + proc.stderr


def _wire_workflow(root: Path, body: str) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "tdd-ordering.yml").write_text(body, encoding="utf-8")


def test_a_repo_with_no_ordering_workflow_is_warned(tmp_path: Path) -> None:
    out = version(tmp_path)
    assert ADVISORY in out, f"a repo with no CI boundary must be told so:\n{out}"


def test_a_repo_that_wires_history_in_ci_is_not_warned(tmp_path: Path) -> None:
    _wire_workflow(tmp_path, "jobs:\n  order:\n    steps:\n      - run: python3 harness.py history --all --repo .\n")
    out = version(tmp_path)
    assert ADVISORY not in out, f"a repo that wires history-in-CI must not be warned:\n{out}"


def test_a_workflow_that_does_not_invoke_history_still_warns(tmp_path: Path) -> None:
    """The presence of *a* workflow is not the boundary — one that runs `history` is."""
    _wire_workflow(tmp_path, "jobs:\n  lint:\n    steps:\n      - run: ruff check .\n")
    out = version(tmp_path)
    assert ADVISORY in out, f"a workflow that never runs history is not the boundary:\n{out}"


def test_the_advisory_is_not_a_failure(tmp_path: Path) -> None:
    """It must not shut the gate or exit non-zero — a repo may opt out of CI on purpose."""
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "version"],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}, cwd=tmp_path,
    )
    assert proc.returncode == 0
