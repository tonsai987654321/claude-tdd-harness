"""The config layer must not quietly weaken the gate.

`harness.py` used to carry its guarded paths and runner definitions as module constants. Making
them configurable is the whole point of the plugin, and it is also the easiest way to ship a gate
that protects less than it appears to. These tests pin the two things that would be silent:

  * the defaults still compile to exactly the patterns the constants held, and
  * a runner name the config does not define is fatal rather than falling back to pytest.

The second matters more than it looks. A typo'd runner that silently became `pytest` would run
`uv run pytest` against a JS project, get exit 4 (usage error), and refuse to open the gate — and
the operator would read that as "my test isn't failing properly", not "my config is wrong".
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "harness.py"


def load_harness(root: Path):
    """Import harness.py as a module, rooted at `root` rather than at this repo."""
    spec = importlib.util.spec_from_file_location("harness_under_test", HARNESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = root
    mod.STATE_DIR = root / ".claude" / "state"
    mod.CYCLE_DIR = root / ".claude" / "cycles"
    mod.CONFIG_PATH = root / ".claude" / "harness.json"
    return mod


@pytest.fixture
def harness(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    return load_harness(tmp_path)


# The patterns harness.py carried as a module constant before the config layer existed. If a
# change to the defaults moves one of these, that is a decision — it should break a test.
LEGACY_PATTERNS = [
    r"(?:^|/)projects/(?P<project>[^/]+)/app/",
    r"(?:^|/)projects/(?P<project>[^/]+)/src/",
    r"(?:^|/)projects/(?P<project>[^/]+)/alembic/versions/",
    r"(?:^|/)projects/(?P<project>[^/]+)/alembic/env\.py$",
]


def test_defaults_compile_to_the_patterns_the_constants_held(harness) -> None:
    got = {p.pattern for p in harness.guarded_patterns()}
    assert got == set(LEGACY_PATTERNS)


@pytest.mark.parametrize(
    "path, guarded",
    [
        ("/repo/projects/foo/app/main.py", True),
        ("/repo/projects/foo/src/index.ts", True),
        ("/repo/projects/foo/alembic/versions/0001_x.py", True),
        ("/repo/projects/foo/alembic/env.py", True),
        # Named exactly, so its neighbours are not swept in with it.
        ("/repo/projects/foo/alembic/alembic.ini", False),
        ("/repo/projects/foo/tests/test_main.py", False),
        ("/repo/projects/foo/README.md", False),
        # A worktree lives inside the harness root; an anchored pattern would miss it entirely.
        ("/repo/.claude/worktrees/wt/projects/foo/app/main.py", True),
    ],
)
def test_default_guarded_set_matches_the_right_paths(harness, path: str, guarded: bool) -> None:
    hit = any(p.search(path) for p in harness.guarded_patterns())
    assert hit is guarded


def test_project_name_is_captured_for_the_gate_message(harness) -> None:
    match = next(m for p in harness.guarded_patterns() if (m := p.search("/x/projects/billing-api/app/y.py")))
    assert match.group("project") == "billing-api"


def test_config_replaces_the_guarded_set_rather_than_adding_to_it(harness, tmp_path: Path) -> None:
    (tmp_path / ".claude" / "harness.json").write_text(json.dumps({"guarded": ["lib/"]}), encoding="utf-8")
    patterns = harness.guarded_patterns()
    assert any(p.search("/r/projects/foo/lib/a.go") for p in patterns)
    # `app/` was not named, so it is no longer guarded. Shallow merge, documented as such.
    assert not any(p.search("/r/projects/foo/app/main.py") for p in patterns)


def test_projects_dir_is_configurable(harness, tmp_path: Path) -> None:
    (tmp_path / ".claude" / "harness.json").write_text(
        json.dumps({"projects_dir": "services", "guarded": ["app/"]}), encoding="utf-8"
    )
    patterns = harness.guarded_patterns()
    assert any(p.search("/r/services/foo/app/main.py") for p in patterns)
    assert not any(p.search("/r/projects/foo/app/main.py") for p in patterns)


def test_a_malformed_config_falls_back_to_defaults_instead_of_crashing(harness, tmp_path: Path) -> None:
    """A broken config must not wedge every Write in the session behind a traceback."""
    (tmp_path / ".claude" / "harness.json").write_text("{not json", encoding="utf-8")
    assert {p.pattern for p in harness.guarded_patterns()} == set(LEGACY_PATTERNS)


def test_unknown_runner_is_fatal(harness, tmp_path: Path) -> None:
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "foo.json").write_text(json.dumps({"runner": "jest", "cycles": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        harness.runner_spec("foo")
    assert "jest" in str(exc.value)


def test_runner_defaults_to_pytest_when_the_cycle_file_is_silent(harness, tmp_path: Path) -> None:
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "foo.json").write_text(json.dumps({"cycles": []}), encoding="utf-8")
    assert harness.runner_spec("foo")["cmd"][:3] == ["uv", "run", "pytest"]


@pytest.mark.parametrize(
    "runner, out, expected",
    [
        ("pytest", "TOTAL      120     6    95%\n", 95),
        ("pytest", "no coverage table here", None),
        # vitest colours the summary line; the escapes must be stripped before matching.
        ("vitest", "\x1b[1mAll files\x1b[0m |\x1b[33m 87.5 \x1b[0m|", 88),
        ("vitest", "All files | 80 |", 80),
    ],
)
def test_coverage_scraping_per_runner(harness, runner: str, out: str, expected) -> None:
    spec = harness.DEFAULT_CONFIG["runners"][runner]
    assert harness.scrape_coverage(spec, out) == expected
