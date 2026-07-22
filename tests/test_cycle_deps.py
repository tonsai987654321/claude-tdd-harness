"""A cycle's dependencies live in prose, where nothing can check them.

`CLAUDE.md` says "Work ONE cycle. Never skip: later tests depend on earlier code." That is a real
constraint stated in a place that cannot enforce it. `build_order` orders *projects*; between the
cycles of one project there is nothing -- so cycle 7 can be closed while cycle 3, whose code it is
built on, is still queued, and the board reports both faithfully.

This is the failure this harness exists to attack, committed by the harness itself: a rule that
lives in a paragraph is a prior, not a constraint. So cycles may declare `depends_on`, and closing
one whose dependencies are not closed is refused.

The dangling case is refused louder than the unmet one, and deliberately. An unmet dependency is
the normal state of work in progress. A dependency on an id that does not exist is a typo that
would otherwise be satisfied by nothing forever -- a check that silently passes because its subject
is missing, which is the shape of every fail-open bug on record here.
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


def write_cycles(root: Path, cycles: list[dict]) -> None:
    d = root / ".claude" / "cycles"
    d.mkdir(parents=True, exist_ok=True)
    (d / "demo-api.json").write_text(
        json.dumps({"build_order": 1, "runner": "pytest", "cycles": cycles}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_cycles(
        tmp_path,
        [
            {"id": 0, "title": "schema"},
            {"id": 1, "title": "repository", "depends_on": [0]},
            {"id": 2, "title": "endpoint", "depends_on": [0, 1]},
        ],
    )
    return tmp_path


def test_closing_a_cycle_whose_dependency_is_open_is_refused(root: Path) -> None:
    r = run_harness(root, "cycle", "demo-api", "2", "done", "--evidence", "pytest 12 passed")

    assert r.returncode != 0, "cycle 2 closed while the code it is built on was still queued"
    assert "0" in r.stderr and "1" in r.stderr, r.stderr


def test_the_refusal_names_only_what_is_actually_unmet(root: Path) -> None:
    assert run_harness(root, "cycle", "demo-api", "0", "done", "--evidence", "pytest 4 passed").returncode == 0

    r = run_harness(root, "cycle", "demo-api", "2", "done", "--evidence", "pytest 12 passed")

    assert r.returncode != 0
    assert "cycle 1" in r.stderr, r.stderr
    assert "cycle 0" not in r.stderr, f"it named a dependency that is already closed:\n{r.stderr}"


def test_closing_in_order_works(root: Path) -> None:
    for cycle_id in ("0", "1", "2"):
        r = run_harness(root, "cycle", "demo-api", cycle_id, "done", "--evidence", "pytest 12 passed")
        assert r.returncode == 0, f"cycle {cycle_id} refused despite its dependencies being closed: {r.stderr}"


def test_a_cycle_with_no_declared_dependencies_is_unaffected(root: Path) -> None:
    """Most cycle files will never declare one. The feature must not tax them."""
    write_cycles(root, [{"id": 0, "title": "schema"}, {"id": 1, "title": "unrelated"}])

    assert run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 4 passed").returncode == 0


def test_a_dependency_on_an_id_that_does_not_exist_is_refused(root: Path) -> None:
    """A typo would otherwise be satisfied by nothing, forever, and pass."""
    write_cycles(root, [{"id": 0, "title": "schema"}, {"id": 1, "title": "typo", "depends_on": [7]}])

    r = run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 4 passed")

    assert r.returncode != 0, "a dependency on a cycle that does not exist was treated as satisfied"
    assert "7" in r.stderr, r.stderr


def test_a_cycle_that_depends_on_itself_is_refused(root: Path) -> None:
    """Otherwise it can never be closed, and nothing says why."""
    write_cycles(root, [{"id": 0, "title": "schema"}, {"id": 1, "title": "loop", "depends_on": [1]}])

    r = run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 4 passed")

    assert r.returncode != 0
    assert "itself" in r.stderr.lower() or "own" in r.stderr.lower(), r.stderr


def test_only_done_closes_a_dependency(root: Path) -> None:
    """`green` means the code works, not that the cycle was accepted. Dependencies wait for `done`."""
    assert run_harness(root, "cycle", "demo-api", "0", "green").returncode == 0

    r = run_harness(root, "cycle", "demo-api", "1", "done", "--evidence", "pytest 12 passed")

    assert r.returncode != 0, "a merely-green dependency was treated as finished"


def test_the_board_shows_what_a_cycle_is_waiting_on(root: Path) -> None:
    r = run_harness(root, "status")

    assert r.returncode == 0, r.stderr
    assert "needs" in r.stdout.lower() or "depends" in r.stdout.lower(), (
        f"the board shows three queued cycles and no hint that two cannot start:\n{r.stdout}"
    )


# ---------------------------------------------------------------- the dispatcher

NEXT_CYCLE = REPO_ROOT / "scripts" / "next_cycle.py"


def run_next(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NEXT_CYCLE)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def test_the_dispatcher_does_not_hand_out_a_blocked_cycle(root: Path) -> None:
    """Refusing at `done` is correct and late -- the work is already written by then.

    Declaration order need not be dependency order: cycle 1 may depend on cycle 5. Handing it out
    and refusing the close afterwards spends a whole cycle's work to deliver the refusal, which
    lesson 0013 is about.
    """
    write_cycles(root, [
        {"id": 0, "title": "endpoint", "depends_on": [1]},
        {"id": 1, "title": "schema"},
    ])
    assert run_harness(root, "cycle", "demo-api", "1", "red").returncode == 0

    r = run_next(root)

    assert "demo-api 1" in r.stdout, f"it dispatched a cycle that cannot be closed:\n{r.stdout}"


def test_all_remaining_blocked_is_reported_not_called_done(root: Path) -> None:
    """Silence here would read as a finished project."""
    write_cycles(root, [{"id": 0, "title": "loop-a", "depends_on": [1]}, {"id": 1, "title": "loop-b", "depends_on": [0]}])
    assert run_harness(root, "cycle", "demo-api", "0", "red").returncode == 0

    r = run_next(root)

    assert "DONE" not in r.stdout, f"a deadlocked project reported as finished:\n{r.stdout}"
    assert "BLOCKED" in r.stdout.upper(), r.stdout


# ------------------------------------------------- the parallel frontier (--batch), see ADR-0002


def run_batch(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NEXT_CYCLE), "--batch"],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def write_project(root: Path, name: str, cycles: list[dict], order: int = 1) -> None:
    d = root / ".claude" / "cycles"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(
        json.dumps({"build_order": order, "runner": "pytest", "cycles": cycles}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_batch_lists_every_fresh_project(tmp_path: Path) -> None:
    """Nothing couples projects, so a batch dispatch offers all of them at once — one line each."""
    write_project(tmp_path, "alpha", [{"id": 0, "title": "schema"}], order=1)
    write_project(tmp_path, "beta", [{"id": 0, "title": "setup"}], order=2)

    r = run_batch(tmp_path)

    assert "BUILD alpha 0" in r.stdout, f"a fresh project was left out of the batch:\n{r.stdout}"
    assert "BUILD beta 0" in r.stdout, f"a fresh project was left out of the batch:\n{r.stdout}"


def test_batch_is_the_frontier_one_cycle_per_project(tmp_path: Path) -> None:
    """At most one cycle per project — a project's cycles are ordered, so only its lowest runnable
    one may start. The batch is the frontier, not a project's whole remaining backlog."""
    write_project(tmp_path, "alpha", [
        {"id": 0, "title": "schema"},
        {"id": 1, "title": "repo", "depends_on": [0]},
        {"id": 2, "title": "api", "depends_on": [1]},
    ], order=1)
    write_project(tmp_path, "beta", [{"id": 0, "title": "setup"}], order=2)
    assert run_harness(tmp_path, "cycle", "alpha", "0", "done", "--evidence", "pytest 4 passed").returncode == 0

    r = run_batch(tmp_path)

    assert "BUILD alpha 1" in r.stdout, f"alpha's frontier moved to cycle 1:\n{r.stdout}"
    assert "BUILD beta 0" in r.stdout, r.stdout
    assert "alpha 2" not in r.stdout, f"the backlog leaked into the batch:\n{r.stdout}"


def test_batch_omits_a_finished_project(tmp_path: Path) -> None:
    """A done project is not work; it must not appear in the frontier."""
    write_project(tmp_path, "alpha", [{"id": 0, "title": "only"}], order=1)
    write_project(tmp_path, "beta", [{"id": 0, "title": "setup"}], order=2)
    assert run_harness(tmp_path, "cycle", "alpha", "0", "done", "--evidence", "pytest 4 passed").returncode == 0

    r = run_batch(tmp_path)

    assert "alpha" not in r.stdout, f"a finished project was still offered:\n{r.stdout}"
    assert "BUILD beta 0" in r.stdout, r.stdout


def test_batch_reports_a_blocked_project_without_dispatching_it(tmp_path: Path) -> None:
    """The single-line dispatcher never hands out a blocked cycle; the batch keeps that guarantee
    per project — a deadlocked one is reported, the others still dispatched."""
    write_project(tmp_path, "alpha", [
        {"id": 0, "title": "loop-a", "depends_on": [1]},
        {"id": 1, "title": "loop-b", "depends_on": [0]},
    ], order=1)
    write_project(tmp_path, "beta", [{"id": 0, "title": "setup"}], order=2)
    assert run_harness(tmp_path, "cycle", "alpha", "0", "red").returncode == 0

    r = run_batch(tmp_path)

    assert "BUILD alpha" not in r.stdout, f"a deadlocked project was dispatched:\n{r.stdout}"
    assert "BLOCKED" in r.stdout.upper() and "alpha" in r.stdout, r.stdout
    assert "BUILD beta 0" in r.stdout, f"one project's deadlock stalled the whole batch:\n{r.stdout}"


def test_batch_is_done_when_every_project_is(tmp_path: Path) -> None:
    """The orchestrator's loop terminates on DONE, the same word the single dispatcher uses."""
    write_project(tmp_path, "alpha", [{"id": 0, "title": "only"}], order=1)
    write_project(tmp_path, "beta", [{"id": 0, "title": "only"}], order=2)
    for name in ("alpha", "beta"):
        assert run_harness(tmp_path, "cycle", name, "0", "done", "--evidence", "pytest 4 passed").returncode == 0

    r = run_batch(tmp_path)

    assert r.stdout.strip() == "DONE", f"a fully-built repo did not report DONE:\n{r.stdout}"
