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


# ------------------------------------------------------------------ guarding the guard


def _probe(root, rel):
    """Ask the real gate about one path, the way Claude Code does."""
    import json as _json
    import os as _os
    import subprocess as _sp
    import sys as _sys

    harness = root / ".claude" / "scripts" / "harness.py"
    payload = _json.dumps({"tool_input": {"file_path": str(root / rel)}})
    return _sp.run(
        [_sys.executable, str(harness), "gate"],
        input=payload, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**_os.environ, "CLAUDE_PROJECT_DIR": str(root)},
    )


def _harness_root(tmp_path, gate_state="SHUT"):
    import json as _json
    import shutil as _shutil

    src = Path(__file__).resolve().parents[1] / "scripts" / "harness.py"
    (tmp_path / ".claude" / "scripts").mkdir(parents=True)
    _shutil.copy2(src, tmp_path / ".claude" / "scripts" / "harness.py")
    (tmp_path / ".claude" / "cycles").mkdir()
    (tmp_path / ".claude" / "state").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".claude" / "cycles" / "api.json").write_text(
        _json.dumps({"project": "api", "runner": "pytest", "cycles": []}), encoding="utf-8"
    )
    (tmp_path / ".claude" / "state" / "api.json").write_text(
        _json.dumps({"project": "api", "gate": {"state": gate_state}, "coverage": None, "cycles": []}),
        encoding="utf-8",
    )
    (tmp_path / "projects" / "api" / "app").mkdir(parents=True)
    return tmp_path


def test_the_gates_own_state_cannot_be_written(tmp_path):
    """The exploit this closes: `.claude/state/` is gitignored, so writing
    {"gate": {"state": "OPEN"}} into it opened the gate with no test ever run and left no trace in
    git. Every `guarded` pattern is forced to start with <projects_dir>/<project>/, so no config
    could express a harness-root path — the hole was structural, not an oversight.
    """
    root = _harness_root(tmp_path)
    assert _probe(root, ".claude/state/api.json").returncode == 2


def test_an_open_gate_does_not_open_the_harness_itself(tmp_path):
    """A mechanism that can rewrite its own verdict is not a mechanism."""
    root = _harness_root(tmp_path, gate_state="OPEN")
    assert _probe(root, "projects/api/app/x.py").returncode == 0, "an open gate must allow app/"
    for protected in (".claude/state/api.json", ".claude/scripts/harness.py", ".claude/settings.json"):
        assert _probe(root, protected).returncode == 2, protected


def test_committed_config_stays_editable(tmp_path):
    """The line is auditability, not importance. Cycle files and harness.json are committed and
    the documented install steps require editing them; blocking those would break the workflow and
    buy nothing, because lowering a coverage_gate is a visible act in a reviewable diff."""
    root = _harness_root(tmp_path)
    assert _probe(root, ".claude/cycles/api.json").returncode == 0
    assert _probe(root, ".claude/harness.json").returncode == 0


def test_the_loud_door_still_opens_the_protected_set(tmp_path):
    """HARNESS_GATE_BYPASS keeps working here on purpose. The point is not that these are
    unopenable — it is that opening them says so, instead of looking like nothing happened."""
    import os as _os
    import subprocess as _sp
    import sys as _sys
    import json as _json

    root = _harness_root(tmp_path)
    proc = _sp.run(
        [_sys.executable, str(root / ".claude" / "scripts" / "harness.py"), "gate"],
        input=_json.dumps({"tool_input": {"file_path": str(root / ".claude/state/api.json")}}),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**_os.environ, "CLAUDE_PROJECT_DIR": str(root), "HARNESS_GATE_BYPASS": "1"},
    )
    assert proc.returncode == 0
    assert "PROTECTED" in proc.stderr, "a bypassed write to the machinery must be audible"


# ------------------------------------------------------------------ the self-test's verdict


def _verdict(root, passed):
    import json as _json
    (root / ".claude" / "state" / ".selftest.json").write_text(
        _json.dumps({"gate_verified": passed, "at": 0, "exit": 0 if passed else 1}), encoding="utf-8")


def test_a_failed_self_test_refuses_production_code(tmp_path):
    """Four checks used to live only in a script somebody had to remember to run. init.sh writes
    its verdict now and a SessionStart hook runs it, so a harness that has stopped enforcing stops
    being written against — fail closed rather than fail quiet."""
    root = _harness_root(tmp_path, gate_state="OPEN")
    _verdict(root, False)
    assert _probe(root, "projects/api/app/x.py").returncode == 2, "an open gate must not override this"


def test_a_failed_self_test_leaves_the_repair_path_open(tmp_path):
    """The difference between a brake and a brick, and it is only the placement of one check.

    Ordered before the exemptions, a failed verdict also refused tests and config — so the
    self-test's own "gate allows tests/" probe returned 2, the verdict could never flip back, and
    the repo was unrepairable by exactly the edits that repair it. Whatever gets refused, the way
    out has to stay open.
    """
    root = _harness_root(tmp_path)
    _verdict(root, False)
    assert _probe(root, "projects/api/tests/test_x.py").returncode == 0
    assert _probe(root, ".claude/harness.json").returncode == 0
    assert _probe(root, "projects/api/README.md").returncode == 0


def test_a_repo_that_has_never_been_verified_is_not_refused(tmp_path):
    """Missing means never verified, not verified-bad. Refusing here would brick a fresh clone
    before its first init.sh, which is the one command that would fix it."""
    root = _harness_root(tmp_path, gate_state="OPEN")
    assert not (root / ".claude" / "state" / ".selftest.json").exists()
    assert _probe(root, "projects/api/app/x.py").returncode == 0


def test_writes_under_an_open_gate_are_recorded(tmp_path):
    """The gate is per-project: one failing test opens every guarded path until green. Narrowing
    that needs language-specific import analysis and would block honest multi-file cycles, so the
    breadth is made visible instead — a cycle that opened on one test and touched fourteen files
    is a fact a reviewer can act on."""
    import json as _json

    root = _harness_root(tmp_path, gate_state="OPEN")
    for name in ("a.py", "b.py", "a.py"):
        assert _probe(root, f"projects/api/app/{name}").returncode == 0

    state = _json.loads((root / ".claude" / "state" / "api.json").read_text(encoding="utf-8"))
    touched = state["gate"]["touched"]
    assert sorted(touched) == ["projects/api/app/a.py", "projects/api/app/b.py"], touched
