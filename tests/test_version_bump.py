"""A released version number must describe exactly one set of files.

Five merges landed — a gate fix, cycle dependencies, a reconcile command, a regression that shut
every scaffolded repo's gate — and `plugin.json` still said `0.8.0` throughout. So the tag
`tdd-harness--v0.8.0` named one tree, the repo shipped a different one under the same number, and
every downstream reading of that number was wrong:

* `.claude/.harness-version` in an installed repo recorded `0.8.0` for code that was not 0.8.0.
* `harness.py version` compared `0.8.0` against `0.8.0` and stayed silent — the drift warning built
  in #8 could not fire, because the number it watches never moved.
* The CI workflow pins its checker to `tdd-harness--v<version>`, so a project would fetch the tagged
  tree while its local harness ran something else.

The lesson this repo keeps relearning: a rule stated in prose is a prior, not a constraint. "Bump
the version when you change the code" belongs in a check, because remembering is exactly what fails
under pressure — and it failed here five times in one day, in the repo whose whole argument is that
mechanisms beat intentions.

The rule, mechanically: if a tag exists for the version currently declared, the files that ship must
match that tag. Changing them means the number has to move first.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# What a consumer actually receives. Docs and this suite are not vendored into installed repos, so
# a typo fix in a lesson does not warrant a release; a change under these does.
SHIPPED = ["scripts/", "templates/", ".claude-plugin/", "agents/", "commands/"]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(PLUGIN_ROOT), *args], capture_output=True, encoding="utf-8", errors="replace"
    )


def declared_version() -> str:
    return json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]


def test_the_declared_version_has_not_already_shipped_different_files() -> None:
    version = declared_version()
    tag = f"tdd-harness--v{version}"

    if git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    if git("rev-parse", "--verify", f"{tag}^{{commit}}").returncode != 0:
        # No tag yet: this version is unreleased, which is the correct state for work in progress.
        return

    changed = git("diff", "--name-only", tag, "HEAD", "--", *SHIPPED).stdout.split()

    assert not changed, (
        f"{tag} is already published, and these shipped files have changed since it:\n  "
        + "\n  ".join(sorted(changed))
        + f"\n\nBump the version in .claude-plugin/plugin.json and marketplace.json before merging. "
        f"Shipping different files under {version} makes every version stamp, every drift warning "
        f"and every CI pin that reads that number wrong."
    )
