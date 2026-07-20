"""A typo should get usage, not a traceback.

`main` guards `quality`, `suite` and `history` and then indexes `args[0]`, `args[1]`, `args[2]`
bare for `red`, `green` and `cycle` -- so `harness.py cycle` answers with `IndexError: list index
out of range`, and `harness.py green no-such-project` leaks a `PosixPath` out of a
`FileNotFoundError`.

Cosmetic in isolation. But this is the tool whose whole argument is that a discipline can be made
to feel mechanical, and a mechanism that answers a typo with a stack trace reads as one nobody
finished. An unknown project should say which projects exist -- the harness knows.
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
def root(tmp_path: Path) -> Path:
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {"build_order": 1, "runner": "pytest", "cycles": [{"id": 0, "title": "scaffold"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("cycle",), "usage: harness.py cycle"),
        (("cycle", "demo-api"), "usage: harness.py cycle"),
        (("cycle", "demo-api", "0"), "usage: harness.py cycle"),
        (("red",), "usage: harness.py red"),
        (("green",), "usage: harness.py green"),
    ],
)
def test_a_missing_argument_gets_usage(root: Path, argv: tuple[str, ...], expected: str) -> None:
    r = run_harness(root, *argv)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr, f"a missing argument printed a stack trace:\n{r.stderr}"
    assert expected in r.stderr, r.stderr


@pytest.mark.parametrize("cmd", ["red", "green", "cycle"])
def test_an_unknown_project_is_named_along_with_the_known_ones(root: Path, cmd: str) -> None:
    extra = {"cycle": ("0", "red"), "red": ("tests/test_x.py",)}.get(cmd, ())
    argv = (cmd, "no-such-project") + extra

    r = run_harness(root, *argv)

    assert r.returncode != 0
    assert "Traceback" not in r.stderr, f"an unknown project printed a stack trace:\n{r.stderr}"
    assert "no-such-project" in r.stderr, r.stderr
    assert "demo-api" in r.stderr, f"the harness knows which projects exist and did not say:\n{r.stderr}"
    assert "PosixPath" not in r.stderr, f"an internal path type leaked to the user:\n{r.stderr}"
