"""One git repository can hold many projects, each with its own RED-before-GREEN ledger.

`history` used to assume the log it walks belongs to a single project: it read the whole log and
prepended one project's prefix to every file in every commit. That is only correct when the repo
contains one project, which is why the harness forced each `projects/<name>/` to be its own git
repo. See ADR-0001.

The fix scopes attribution by path. A file is judged by where it sits: a commit touching
`projects/A/...` contributes to A's ledger and a commit touching `projects/B/...` to B's, in one
shared log. A file under no project prefix — a root lockfile, a CI config — is in no ledger, the
same way scaffolding never was.

Each test builds a real throwaway monorepo: one `git init` at the root, projects as plain subdirs,
no nested `.git`. `history <p>` is run with `CLAUDE_PROJECT_DIR` at that root, the way the
dispatcher runs it — no `--repo`, so the check must find the enclosing git root itself.
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
def monorepo(tmp_path: Path) -> Path:
    """One git repo at the root; projects live under projects/<name>/ as plain directories."""
    root = tmp_path / "monorepo"
    root.mkdir()
    git(root, "init", "-q")
    return root


def history(root: Path, project: str) -> subprocess.CompletedProcess[str]:
    """Run `history <project>` against the monorepo root — no --repo, project_dir has no .git."""
    return subprocess.run(
        [sys.executable, str(HARNESS), "history", project],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def test_interleaved_projects_do_not_cross_credit(monorepo: Path) -> None:
    """A RED banked for A must not be spendable by B's code. Each project balances on its own."""
    commit(monorepo, "test(A): z-score [RED]", {"projects/A/tests/test_a.py": "def test(): assert True\n"})
    commit(monorepo, "test(B): parser [RED]", {"projects/B/tests/test_b.py": "def test(): assert True\n"})
    commit(monorepo, "feat(A): z-score [GREEN]", {"projects/A/src/a.py": "A = 1\n"})
    commit(monorepo, "feat(B): parser [GREEN]", {"projects/B/src/b.py": "B = 1\n"})

    a = history(monorepo, "A")
    b = history(monorepo, "B")

    assert a.returncode == 0, f"A's own history is clean but was rejected:\n{a.stdout}{a.stderr}"
    assert b.returncode == 0, f"B's own history is clean but was rejected:\n{b.stdout}{b.stderr}"


def test_interleaved_projects_still_catch_a_real_violation(monorepo: Path) -> None:
    """B ships untested code while A has a spare banked RED. B must fail — it cannot spend A's credit."""
    commit(monorepo, "test(A): z-score [RED]", {"projects/A/tests/test_a.py": "def test(): assert True\n"})
    commit(monorepo, "feat(B): untested", {"projects/B/src/b.py": "B = 1\n"})

    a = history(monorepo, "A")
    b = history(monorepo, "B")

    assert a.returncode == 0, f"A banked a RED and shipped nothing untested:\n{a.stdout}{a.stderr}"
    assert b.returncode != 0, "B shipped untested code but spent A's banked RED"
    assert "no test commit before it" in b.stdout, f"failed for the wrong reason:\n{b.stdout}{b.stderr}"


def test_one_commit_spanning_two_projects_is_split_by_path(monorepo: Path) -> None:
    """A single commit touching A's test and B's code banks for A and is a violation for B."""
    commit(monorepo, "wip: A test, B code", {
        "projects/A/tests/test_a.py": "def test(): assert True\n",
        "projects/B/src/b.py": "B = 1\n",
    })
    commit(monorepo, "feat(A): [GREEN]", {"projects/A/src/a.py": "A = 1\n"})

    a = history(monorepo, "A")
    b = history(monorepo, "B")

    assert a.returncode == 0, f"A's test banked, A's code spent — clean:\n{a.stdout}{a.stderr}"
    assert b.returncode != 0, "B's code in the spanning commit had no B test before it"
    assert "no test commit before it" in b.stdout, f"failed for the wrong reason:\n{b.stdout}{b.stderr}"


def test_a_shared_root_file_is_in_no_projects_ledger(monorepo: Path) -> None:
    """A commit touching only root tooling is counted by no project — scaffolding never was."""
    commit(monorepo, "test(A): z-score [RED]", {"projects/A/tests/test_a.py": "def test(): assert True\n"})
    commit(monorepo, "chore: root tooling", {
        "pyproject.toml": "[project]\nname = 'x'\n",
        ".github/workflows/ci.yml": "on: push\n",
    })
    commit(monorepo, "feat(A): z-score [GREEN]", {"projects/A/src/a.py": "A = 1\n"})

    a = history(monorepo, "A")

    assert a.returncode == 0, f"A's history is clean:\n{a.stdout}{a.stderr}"
    assert "2 commits" in a.stdout, f"the root-tooling commit leaked into A's ledger:\n{a.stdout}"


# --- `history --all`: the CI arm. A single `history <reponame>` invocation checks one project and
# vacuous-passes a monorepo with many; --all discovers every project and checks each. See issue #12.


def history_all(root: Path) -> subprocess.CompletedProcess[str]:
    """Run `history --all` against the monorepo root, the way CI does (`--repo .`)."""
    return subprocess.run(
        [sys.executable, str(HARNESS), "history", "--all", "--repo", str(root)],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)}, cwd=root,
    )


def test_all_catches_a_violation_a_single_project_check_would_miss(monorepo: Path) -> None:
    """The regression. A is clean, B ships untested code. The old single-project CI invocation named
    after the repo matched no file in a monorepo and exited 0; --all must fail on B."""
    commit(monorepo, "test(A): z-score [RED]", {"projects/A/tests/test_a.py": "def test(): assert True\n"})
    commit(monorepo, "feat(A): z-score [GREEN]", {"projects/A/src/a.py": "A = 1\n"})
    commit(monorepo, "feat(B): untested", {"projects/B/src/b.py": "B = 1\n"})

    r = history_all(monorepo)

    assert r.returncode != 0, f"a monorepo with an untested project passed --all:\n{r.stdout}{r.stderr}"
    assert "B" in r.stdout, f"the failing project was not named:\n{r.stdout}{r.stderr}"


def test_all_is_green_when_every_project_is_clean(monorepo: Path) -> None:
    """Both projects test-first — --all reports each and exits 0."""
    commit(monorepo, "test(A): z-score [RED]", {"projects/A/tests/test_a.py": "def test(): assert True\n"})
    commit(monorepo, "feat(A): z-score [GREEN]", {"projects/A/src/a.py": "A = 1\n"})
    commit(monorepo, "test(B): parser [RED]", {"projects/B/tests/test_b.py": "def test(): assert True\n"})
    commit(monorepo, "feat(B): parser [GREEN]", {"projects/B/src/b.py": "B = 1\n"})

    r = history_all(monorepo)

    assert r.returncode == 0, f"a clean monorepo was rejected:\n{r.stdout}{r.stderr}"
    assert "A" in r.stdout and "B" in r.stdout, f"--all did not check every project:\n{r.stdout}"


def test_all_on_a_flat_single_project_repo_still_checks_the_one(tmp_path: Path) -> None:
    """A flat checkout — src/ at the root, no projects/ dir — has one project named after the repo.
    --all must fall back to checking it, so single-project CI is unchanged by the loop."""
    repo = tmp_path / "flat"
    repo.mkdir()
    git(repo, "init", "-q")
    commit(repo, "feat: untested", {"src/a.py": "A = 1\n"})

    r = history_all(repo)

    assert r.returncode != 0, f"a flat repo shipping untested code passed --all:\n{r.stdout}{r.stderr}"


def test_workflow_template_uses_the_all_loop() -> None:
    """Guard the YAML regression: the shipped CI workflow must run the per-project loop, not a single
    `history <reponame>` that vacuous-passes a monorepo (issue #12).

    Skipped where the source template is absent: this suite is vendored into scaffolded repos, which
    do not carry the plugin's `templates/` tree. The guard is meaningful only in the source repo."""
    wf_path = REPO_ROOT / "templates" / "workflows" / "tdd-ordering.yml"
    if not wf_path.is_file():
        pytest.skip("no source workflow template here (vendored suite)")
    wf = wf_path.read_text(encoding="utf-8")
    assert "history --all" in wf, "the CI workflow does not use the per-project --all loop"
    assert 'history "${GITHUB_REPOSITORY##*/}"' not in wf, "the CI workflow still checks one project by repo name"
