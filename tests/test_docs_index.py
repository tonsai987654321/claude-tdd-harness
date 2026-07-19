"""The reference index — the only thing that makes lessons and ADRs readable at scale.

Retrieval, not loading, is what keeps the archive affordable: the index is one line per document
and the full text is opened on a match. So the two behaviours worth pinning are that a document
appears in the index at all, and that a retired one stops appearing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "harness.py"


def run(root: Path, *args: str) -> str:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    proc = subprocess.run(
        [sys.executable, str(HARNESS), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def write_lesson(root: Path, name: str, title: str, status: str, trigger: str) -> None:
    d = root / "docs" / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"# Lesson {name[:4]}: {title}\n\n"
        f"**Status:** {status}\n"
        f"**Trigger:** {trigger}\n\n## Expected\n\nsomething\n",
        encoding="utf-8",
    )


def test_live_lessons_are_indexed_with_their_trigger(tmp_path: Path) -> None:
    write_lesson(tmp_path, "0001-a.md", "the first surprise", "active", "you are about to deploy")
    out = run(tmp_path, "lessons")
    assert "0001" in out
    assert "the first surprise" in out
    # The trigger is what decides whether anyone opens the file, so it has to be in the index.
    assert "you are about to deploy" in out


def test_mechanised_lessons_leave_the_index(tmp_path: Path) -> None:
    """A lesson whose failure mode is now blocked by a check has become the check.

    This is the compaction mechanism. Without it the index grows forever and the cost of the
    archive grows with it, which is the thing retrieval was supposed to avoid.
    """
    write_lesson(tmp_path, "0001-a.md", "still true", "active", "trigger one")
    write_lesson(tmp_path, "0002-b.md", "now a test", "mechanised", "trigger two")

    out = run(tmp_path, "lessons")
    assert "still true" in out
    assert "now a test" not in out
    assert "1 live, 1 mechanised" in out

    assert "now a test" in run(tmp_path, "lessons", "--all")


def test_superseded_adrs_leave_the_index(tmp_path: Path) -> None:
    d = tmp_path / "docs" / "adr"
    d.mkdir(parents=True)
    # Both field styles appear in the shipped ADRs. A parser that read only one would silently
    # hide half the corpus from the index that is supposed to surface it.
    (d / "0001-kept.md").write_text("# ADR-0001: the standing decision\n\n- **Status:** accepted\n", encoding="utf-8")
    (d / "0002-gone.md").write_text("# ADR-0002: the replaced one\n\n**Status:** superseded by ADR-0003\n", encoding="utf-8")

    out = run(tmp_path, "adrs")
    assert "the standing decision" in out
    assert "the replaced one" not in out
    assert "1 accepted, 1 superseded" in out


def test_an_empty_archive_says_where_to_start(tmp_path: Path) -> None:
    assert "0000-how-to-write-one" in run(tmp_path, "lessons")
    assert "0000-template" in run(tmp_path, "adrs")


def test_every_mechanised_lesson_points_at_a_test_that_exists() -> None:
    """A retired lesson claims a check replaced it. If that check is not there, the lesson has
    been removed from the index on the strength of a promise.

    This is the plugin's own cardinal failure — "documentation that lies", named in CLAUDE.md —
    aimed at the mechanism that retires documentation. It caught a real one on its first run: the
    how-to-write-one guide demonstrated the format with `test_gate_blocks_app_while_shut`, a test
    that has never existed, and shipped that example into every scaffolded repo.

    Resolved by reading the file rather than by collecting with pytest, so it works the same in a
    scaffolded repo, where the tests live under .claude/harness-tests/ and the harness is not
    installed as a package.
    """
    import re

    # This file runs from two places: tests/ in the plugin, and .claude/harness-tests/ in a
    # scaffolded repo. Walk up to whichever holds docs/lessons/ rather than counting parents —
    # the first version counted, counted wrong, and failed in the scaffolded repo only.
    here = Path(__file__).resolve()
    root = next((p for p in here.parents if (p / "docs" / "lessons").is_dir()), None)
    assert root, f"no docs/lessons/ above {here} — the archive should at least carry the guide"
    lessons = sorted((root / "docs" / "lessons").glob("*.md"))
    assert lessons, "no lessons to check — the archive should at least carry the guide"

    broken = []
    for path in lessons:
        line = next(
            (l for l in path.read_text(encoding="utf-8").splitlines() if "**Enforced by:**" in l),
            None,
        )
        if not line:
            continue
        # The reference is what precedes the em dash; everything after it is prose, and prose
        # names commands like `harness.py quality` that are not files to resolve.
        reference = line.split("**Enforced by:**", 1)[1].split("—", 1)[0]
        refs = re.findall(r"`?([\w/.-]+\.py)(?:::(\w+))?`?", reference)
        assert refs, f"{path.name}: Enforced by names no file"
        for filename, testname in refs:
            target = root / filename
            if not target.is_file():
                broken.append(f"{path.name} -> {filename} (no such file)")
                continue
            if testname and f"def {testname}" not in target.read_text(encoding="utf-8"):
                broken.append(f"{path.name} -> {filename}::{testname} (no such test)")

    assert not broken, "mechanised lessons whose enforcing check does not exist: " + "; ".join(broken)
