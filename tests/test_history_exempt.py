"""`history` holds a stricter definition of production code than the gate does.

`history` is the one check meant to run in CI, where the agent writes the code but not the verdict.
It is therefore the one check that must not be wrong: everything else here lets something wrong look
right, and this makes something *right* look wrong. A team adopting it on a Python or vitest repo
gets a red build on correct work, and the reasonable response to that is to stop trusting the check.

`.claude/harness.json` defines two exemptions and states their intent outright -- `__init__.py` is a
structural file that cannot be driven by a test, and "a write to src/components/Foo.test.tsx is not
held to the RED-first rule". `cmd_gate` honours both. `settle()` consults neither, deciding `code`
from the guarded patterns alone. So one harness carries two definitions of production code, and the
stricter one writes the CI verdict.

Two standard layouts trip it, neither exotic:

* Python. A RED commit adding a test also creates the package markers the test imports through.
  Three empty `__init__.py` files land under `app/`, the ledger reads the commit as code, and it
  banks and spends in the same breath -- so the GREEN that follows arrives at zero.
* Vitest. Tests sit beside the code they test, so every test file is inside the guarded path. It
  banks as a test and spends as code at once, a co-located suite never accumulates a balance, and
  the next real code commit is flagged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "harness.py"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo: Path, subject: str, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", subject)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


def run_history(root: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS), "history", "demo", "--repo", str(repo)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def test_package_markers_beside_a_test_are_not_production_code(tmp_path: Path, project: Path) -> None:
    """The Python shape. Empty `__init__.py` files cannot be driven by a test; the gate says so."""
    commit(project, "test(cycle-1): z-score detection [RED]", {
        "app/__init__.py": "",
        "app/core/__init__.py": "",
        "tests/unit/test_zscore.py": "def test_z(): assert True\n",
    })
    commit(project, "feat(cycle-1): z-score detection [GREEN]", {"app/core/zscore.py": "Z = 1\n"})

    r = run_history(tmp_path, project)

    assert r.returncode == 0, f"correct history reported as a violation:\n{r.stdout}{r.stderr}"


def test_a_co_located_test_file_is_not_production_code(tmp_path: Path, project: Path) -> None:
    """The vitest shape. `src/api/billing.test.ts` is the exact file the config's comment names."""
    commit(project, "test(billing): money crosses the wire as a string", {
        "src/api/billing.test.ts": "it('x', () => {})\n",
        "test/mocks/handlers.ts": "export const handlers = []\n",
    })
    commit(project, "feat(billing): parse Decimal money at the API boundary", {"src/api/billing.ts": "export const x = 1\n"})

    r = run_history(tmp_path, project)

    assert r.returncode == 0, f"a co-located suite can never bank a balance:\n{r.stdout}{r.stderr}"


def test_real_untested_code_is_still_caught(tmp_path: Path, project: Path) -> None:
    """The check must keep failing what it exists to fail. Exempting too much would be worse than
    the bug: a CI boundary that passes everything is one nobody notices has stopped working."""
    commit(project, "feat: ship it", {"app/core/billing.py": "def charge(): ...\n"})

    r = run_history(tmp_path, project)

    assert r.returncode != 0, "production code with no test commit before it was accepted"


def test_a_commit_of_only_exempt_files_does_not_bank_a_red(tmp_path: Path, project: Path) -> None:
    """Exempting a file from `code` must not silently promote it to `tests`.

    Otherwise `__init__.py` alone would bank a RED that a later untested commit could spend, which
    turns this fix into a new way to launder a violation.
    """
    commit(project, "chore: package markers", {"app/__init__.py": "", "app/core/__init__.py": ""})
    commit(project, "feat: ship it", {"app/core/billing.py": "def charge(): ...\n"})

    r = run_history(tmp_path, project)

    assert r.returncode != 0, "empty package markers banked a RED that untested code then spent"
