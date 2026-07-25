"""`green` must prove the cycle's test needs the cycle's code.

The RED gate proves a test failed *before* the code was written. It does not prove the test still
needs that code at green time — between `red` and `green` the test file (never gated) can be rewritten
to something that passes trivially, and the production code written under the open gate then certifies
a cycle nothing constrains. ADR-0003 names this hole; ADR-0010 closes the reachable part of it.

So `green`, after the suite passes, reverts the guarded files this cycle touched to their pre-cycle
content and reruns the test that opened the gate. If the test still passes with the code gone, it
proves nothing and the gate stays OPEN — the same refusal `red` makes for a test that passes before
the code exists, moved to the other end of the cycle.

These drive the real `red`/`green` CLI against throwaway git repos with a stand-in pytest runner, so
the check is exercised end to end and never against the harness's own state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"

# A runner the throwaway repo can actually run: this suite already runs under pytest, so the
# interpreter has it. `uv run pytest` (the shipped default) is not available in tmp_path.
RUNNER = {
    "cmd": [sys.executable, "-m", "pytest"],
    "red_args": ["-q"],
    "green_args": ["-q"],
    "red_exit_codes": [1, 2],
    "no_tests_exit": 5,
    "writable_hint": "app/",
}


def run(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root), **env}, cwd=root,
    )


def record_touch(root: Path, rel: str) -> None:
    """Drive the real gate hook so a guarded write is recorded, exactly as the PreToolUse hook does."""
    subprocess.run(
        [sys.executable, str(HARNESS), "gate"],
        input=json.dumps({"tool_input": {"file_path": str(root / rel)}}),
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def git(d: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)


def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def state(root: Path) -> dict:
    return json.loads((root / ".claude" / "state" / "api.json").read_text(encoding="utf-8"))


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A harness root with one project `api`, a git repo, and the RED test committed (no code yet)."""
    root = tmp_path
    (root / ".claude" / "scripts").mkdir(parents=True)
    shutil.copy2(HARNESS, root / ".claude" / "scripts" / "harness.py")
    (root / ".claude" / "cycles").mkdir()
    (root / ".claude" / "state").mkdir()
    (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (root / ".claude" / "harness.json").write_text(
        json.dumps({"runners": {"pytest": RUNNER}}), encoding="utf-8")
    (root / ".claude" / "cycles" / "api.json").write_text(
        json.dumps({"project": "api", "runner": "pytest", "cycles": [{"id": 0, "title": "feature"}]}),
        encoding="utf-8")
    (root / ".claude" / "state" / "api.json").write_text(
        json.dumps({"project": "api", "gate": {"state": "SHUT"}, "cycles": []}), encoding="utf-8")
    d = root / "projects" / "api"
    write(d / "app" / "__init__.py", "")
    write(d / "tests" / "__init__.py", "")
    return root


def _init_repo_with_red(root: Path, test_body: str) -> Path:
    """Commit the app package and a RED test, so HEAD is the [RED] commit — code absent."""
    d = root / "projects" / "api"
    write(d / "tests" / "test_feature.py", test_body)
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@e.com")
    git(d, "config", "user.name", "t")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "test(cycle-0): feature [RED]")
    return d


def test_a_real_cycle_still_passes_green(proj: Path) -> None:
    """The test genuinely needs the code: reverting the code makes it fail, so `green` proceeds."""
    d = _init_repo_with_red(proj, "from app.feature import f\n\ndef test_f():\n    assert f() == 1\n")

    red = run(proj, "red", "api", "tests/test_feature.py")
    assert red.returncode == 0, red.stdout + red.stderr
    assert state(proj)["gate"].get("base_sha"), "red must record the baseline commit to revert against"

    write(d / "app" / "feature.py", "def f():\n    return 1\n")
    record_touch(proj, "projects/api/app/feature.py")

    green = run(proj, "green", "api")
    assert green.returncode == 0, f"a real cycle must pass green:\n{green.stdout}{green.stderr}"
    assert state(proj)["gate"]["state"] == "SHUT"
    assert (d / "app" / "feature.py").read_text() == "def f():\n    return 1\n", "code must be restored intact"


def test_a_test_that_passes_without_its_code_is_refused(proj: Path) -> None:
    """The catchable fake: the test is rewritten after RED to pass trivially, so the code it is
    supposed to drive is not actually needed. Reverting the code and rerunning must still pass —
    which is the tell — and `green` refuses, leaving the gate OPEN."""
    d = _init_repo_with_red(proj, "from app.missing import x\n\ndef test_x():\n    assert x\n")

    red = run(proj, "red", "api", "tests/test_feature.py")
    assert red.returncode == 0, red.stdout + red.stderr

    # Production written under the open gate...
    write(d / "app" / "feature.py", "def f():\n    return 1\n")
    record_touch(proj, "projects/api/app/feature.py")
    # ...but the test is rewritten to something that passes without ever touching it.
    write(d / "tests" / "test_feature.py", "def test_trivial():\n    assert True\n")

    green = run(proj, "green", "api")
    assert green.returncode != 0, f"green must refuse a test that passes without its code:\n{green.stdout}"
    assert state(proj)["gate"]["state"] == "OPEN", "the gate must stay OPEN on a refused green"
    assert "tests/test_feature.py" in (green.stdout + green.stderr), "the refusal must name the test"


def test_the_working_tree_is_restored_after_a_refusal(proj: Path) -> None:
    """A quality check that leaves the project half-reverted is worse than no check. The touched
    file must be back to its working content whether the check passes or fails."""
    d = _init_repo_with_red(proj, "from app.missing import x\n\ndef test_x():\n    assert x\n")
    run(proj, "red", "api", "tests/test_feature.py")

    body = "def f():\n    return 1\n"
    write(d / "app" / "feature.py", body)
    record_touch(proj, "projects/api/app/feature.py")
    write(d / "tests" / "test_feature.py", "def test_trivial():\n    assert True\n")

    run(proj, "green", "api")  # refused
    assert (d / "app" / "feature.py").exists(), "the reverted file must be recreated"
    assert (d / "app" / "feature.py").read_text() == body, "and with its exact working content"


def test_the_bypass_skips_the_check_loudly(proj: Path) -> None:
    """The genuinely-undriveable case has an escape hatch, and like HARNESS_GATE_BYPASS it says so
    on stderr instead of looking like nothing happened."""
    d = _init_repo_with_red(proj, "from app.missing import x\n\ndef test_x():\n    assert x\n")
    run(proj, "red", "api", "tests/test_feature.py")

    write(d / "app" / "feature.py", "def f():\n    return 1\n")
    record_touch(proj, "projects/api/app/feature.py")
    write(d / "tests" / "test_feature.py", "def test_trivial():\n    assert True\n")

    green = run(proj, "green", "api", HARNESS_QUALITY_BYPASS="1")
    assert green.returncode == 0, f"bypass must let green through:\n{green.stdout}{green.stderr}"
    assert state(proj)["gate"]["state"] == "SHUT"
    assert "bypass" in green.stderr.lower(), "a bypassed quality check must be audible on stderr"


def test_greening_one_project_does_not_revert_another(proj: Path) -> None:
    """The revert reads only this project's touched list, so greening A must never disturb B —
    the same per-project scoping ADR-0001 gives the gate and the ledger."""
    d = _init_repo_with_red(proj, "from app.feature import f\n\ndef test_f():\n    assert f() == 1\n")

    # A second project's production file sitting in the same harness root must be left untouched:
    # the revert reads api's touched list alone, which never names other/.
    other = proj / "projects" / "other" / "app"
    write(other / "keep.py", "SENTINEL = 42\n")

    run(proj, "red", "api", "tests/test_feature.py")
    write(d / "app" / "feature.py", "def f():\n    return 1\n")
    record_touch(proj, "projects/api/app/feature.py")

    run(proj, "green", "api")
    assert (other / "keep.py").read_text() == "SENTINEL = 42\n", "greening api must not touch other/"
