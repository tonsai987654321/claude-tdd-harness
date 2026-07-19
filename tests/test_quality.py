"""`quality` and `suite` — the commands that took four hardcoded copies out of prose.

The linter, formatter and type checker used to be named in `init.sh`, in the implementer agent,
in the reviewer agent and in the auditor agent. Four copies of one fact, none of them reachable
by a repo that lints with something else, and a change to one of them silently disagreed with the
other three. These tests pin the property that replaced them: the commands come from the config.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "harness.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A harness root with one project whose runner is invented, not pytest or vitest.

    Deliberately invented: if these tests used a real runner they would pass for a repo that
    happens to match the shipped defaults, which is the exact assumption being removed.
    """
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "cycles").mkdir()
    (tmp_path / "projects" / "widget").mkdir(parents=True)
    (tmp_path / ".claude" / "cycles" / "widget.json").write_text(
        json.dumps({"project": "widget", "runner": "madeup", "cycles": []}), encoding="utf-8"
    )
    return tmp_path


def write_config(root: Path, runner: dict) -> None:
    (root / ".claude" / "harness.json").write_text(
        json.dumps({"projects_dir": "projects", "runners": {"madeup": runner}}), encoding="utf-8"
    )


def test_quality_runs_the_commands_the_config_declares(repo: Path) -> None:
    write_config(repo, {
        "cmd": [sys.executable, "-c", "pass"],
        "red_exit_codes": [1],
        "quality": [
            [sys.executable, "-c", "print('LINTER RAN')"],
            [sys.executable, "-c", "print('TYPECHECKER RAN')"],
        ],
    })
    proc = run(repo, "quality", "widget")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "LINTER RAN" in proc.stdout
    assert "TYPECHECKER RAN" in proc.stdout


def test_quality_stops_at_the_first_failure(repo: Path) -> None:
    """A gate that keeps going and reports at the end invites reading only the last line."""
    write_config(repo, {
        "cmd": [sys.executable, "-c", "pass"],
        "red_exit_codes": [1],
        "quality": [
            [sys.executable, "-c", "raise SystemExit(1)"],
            [sys.executable, "-c", "print('SHOULD NOT RUN')"],
        ],
    })
    proc = run(repo, "quality", "widget")
    assert proc.returncode != 0
    assert "SHOULD NOT RUN" not in proc.stdout


def test_quality_expands_the_guarded_path(repo: Path) -> None:
    """`{writable}` is how `mypy --strict app/` stops being a hardcoded `app/`."""
    write_config(repo, {
        "cmd": [sys.executable, "-c", "pass"],
        "red_exit_codes": [1],
        "writable_hint": "lib/",
        "quality": [[sys.executable, "-c", "import sys; print('CHECKED', sys.argv[1])", "{writable}"]],
    })
    proc = run(repo, "quality", "widget")
    assert "CHECKED lib/" in proc.stdout


def test_a_runner_with_no_quality_commands_fails_loudly(repo: Path) -> None:
    """Silence here would mean a repo running no quality gates and looking identical to one that
    passed them all — the shape of docs/lessons/0002."""
    write_config(repo, {"cmd": [sys.executable, "-c", "pass"], "red_exit_codes": [1]})
    proc = run(repo, "quality", "widget")
    assert proc.returncode != 0
    assert "quality" in (proc.stdout + proc.stderr)


def test_suite_does_not_touch_gate_state(repo: Path) -> None:
    """`init.sh` runs this on every invocation, including in the middle of a RED cycle.

    `green` would shut the gate on success, so running the health check would silently revoke
    the write permission an open cycle depends on.
    """
    state = repo / ".claude" / "state"
    state.mkdir()
    before = json.dumps({
        "project": "widget", "gate": {"state": "OPEN"}, "coverage": None, "cycles": [],
    })
    (state / "widget.json").write_text(before, encoding="utf-8")

    write_config(repo, {"cmd": [sys.executable, "-c", "pass"], "red_exit_codes": [1], "green_args": []})
    assert run(repo, "suite", "widget").returncode == 0

    assert (state / "widget.json").read_text(encoding="utf-8") == before


def test_suite_reports_a_failing_run(repo: Path) -> None:
    write_config(repo, {"cmd": [sys.executable, "-c", "raise SystemExit(1)"], "red_exit_codes": [1], "green_args": []})
    assert run(repo, "suite", "widget").returncode != 0


def test_suite_accepts_an_empty_project(repo: Path) -> None:
    """Before the first cycle there is nothing to collect, and that is the correct state."""
    write_config(repo, {
        "cmd": [sys.executable, "-c", "raise SystemExit(5)"],
        "red_exit_codes": [1],
        "green_args": [],
        "no_tests_exit": 5,
    })
    assert run(repo, "suite", "widget").returncode == 0


def test_projects_dir_is_honoured(repo: Path) -> None:
    """project_dir hardcoded `projects/` while the gate patterns read `projects_dir` from config —
    a repo that renamed it got a gate guarding one directory and a runner looking in another."""
    (repo / "services" / "widget").mkdir(parents=True)
    (repo / ".claude" / "harness.json").write_text(
        json.dumps({
            "projects_dir": "services",
            "runners": {"madeup": {
                "cmd": [sys.executable, "-c", "import os; print('CWD', os.path.basename(os.getcwd()))"],
                "red_exit_codes": [1], "green_args": [],
            }},
        }), encoding="utf-8",
    )
    proc = run(repo, "suite", "widget")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CWD widget" in proc.stdout


# ------------------------------------------------------------------ upgrading an older repo


def test_a_config_predating_quality_still_works_for_a_shipped_runner(tmp_path: Path) -> None:
    """The upgrade path, which is where this whole class of bug lives.

    `.claude/harness.json` is repo-owned and a re-sync never rewrites it, so a repo installed
    before `quality` existed receives a harness.py that requires a key its config cannot have. A
    runner that shares a name with a shipped one inherits what it did not mention; without that,
    `init.sh` failed on every project of every upgraded repo, exactly the shape of lesson 0005.
    """
    (tmp_path / ".claude" / "cycles").mkdir(parents=True)
    (tmp_path / "projects" / "widget").mkdir(parents=True)
    (tmp_path / ".claude" / "cycles" / "widget.json").write_text(
        json.dumps({"project": "widget", "runner": "pytest", "cycles": []}), encoding="utf-8"
    )
    # A pre-0.3.0 config: a full runners block, written before `quality` was a key.
    (tmp_path / ".claude" / "harness.json").write_text(json.dumps({
        "projects_dir": "projects",
        "runners": {"pytest": {"cmd": ["uv", "run", "pytest"], "red_exit_codes": [1, 2]}},
    }), encoding="utf-8")

    proc = run(tmp_path, "quality", "widget")
    out = proc.stdout + proc.stderr
    assert 'defines no "quality" commands' not in out
    # It inherited the shipped pytest gates and tried to run them — which is what the repo was
    # already running in the version it is upgrading from.
    assert "ruff" in out


def test_a_custom_runner_still_has_to_declare_its_own(repo: Path) -> None:
    """The fallback fills gaps for runners the plugin ships. It cannot invent one for `madeup`,
    and guessing there would be worse than the hard error."""
    write_config(repo, {"cmd": [sys.executable, "-c", "pass"], "red_exit_codes": [1]})
    proc = run(repo, "quality", "widget")
    assert proc.returncode != 0


def test_keys_the_user_named_still_win(repo: Path) -> None:
    """Filling a gap must never soften a decision the config made explicitly."""
    (repo / ".claude" / "cycles" / "widget.json").write_text(
        json.dumps({"project": "widget", "runner": "pytest", "cycles": []}), encoding="utf-8"
    )
    (repo / ".claude" / "harness.json").write_text(json.dumps({
        "projects_dir": "projects",
        "runners": {"pytest": {
            "cmd": [sys.executable, "-c", "pass"],
            "red_exit_codes": [1],
            "quality": [[sys.executable, "-c", "print('MINE RAN')"]],
        }},
    }), encoding="utf-8")
    proc = run(repo, "quality", "widget")
    assert "MINE RAN" in proc.stdout
    assert "ruff" not in proc.stdout


# ------------------------------------------------------------------ the coverage gate


def cycle_repo(tmp_path: Path, gate: int, coverage: int | None) -> Path:
    (tmp_path / ".claude" / "cycles").mkdir(parents=True)
    (tmp_path / ".claude" / "state").mkdir()
    (tmp_path / ".claude" / "cycles" / "widget.json").write_text(json.dumps({
        "project": "widget", "runner": "pytest", "coverage_gate": gate,
        "cycles": [{"id": 1, "title": "a behaviour"}],
    }), encoding="utf-8")
    (tmp_path / ".claude" / "state" / "widget.json").write_text(json.dumps({
        "project": "widget", "gate": {"state": "SHUT"}, "coverage": coverage,
        "cycles": [{"id": 1, "title": "a behaviour", "state": "green", "agent": "-",
                    "tokens": 0, "evidence": ""}],
    }), encoding="utf-8")
    return tmp_path


def close(root: Path):
    return run(root, "cycle", "widget", "1", "done", "--evidence", "suite green; aaa -> bbb")


def test_a_cycle_under_the_coverage_gate_cannot_be_closed(tmp_path: Path) -> None:
    """`coverage_gate` was in every cycle file, validated at install, quoted in the PLAYBOOK's
    definition of done — and read by nothing that could refuse. A number only an agent checks is
    the thing this harness exists to argue against."""
    proc = close(cycle_repo(tmp_path, gate=90, coverage=71))
    assert proc.returncode != 0
    assert "71" in proc.stdout + proc.stderr
    assert "90" in proc.stdout + proc.stderr


@pytest.mark.parametrize("coverage", [90, 97])
def test_a_cycle_at_or_above_the_gate_closes(tmp_path: Path, coverage: int) -> None:
    """Exactly at the gate counts as meeting it; each case gets its own tmp_path because
    cycle_repo builds a repo from scratch."""
    assert close(cycle_repo(tmp_path, gate=90, coverage=coverage)).returncode == 0


def test_coverage_that_was_never_measured_does_not_block(tmp_path: Path) -> None:
    """Cycle 0 is scaffolding and runs no suite. Refusing there would make the gate a rule about
    when you are allowed to have measured something, which is not what it is for — and the
    evidence rule already stands between an unmeasured cycle and a silent `done`."""
    assert close(cycle_repo(tmp_path, gate=90, coverage=None)).returncode == 0
