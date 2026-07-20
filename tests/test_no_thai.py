"""Nothing this plugin ships should carry Thai text.

The harness is installed into other people's repositories. A Thai sentence in a shipped template is
not a note to ourselves -- it lands in the ADR folder of every repo that runs the installer, in
front of readers who cannot read it. `0003-mechanical-red-gate.md` carried one, immediately
followed by the same sentence in English, so it was duplication that only some readers could see
past.

One file is exempt, deliberately, and it is the whole reason this rule needs writing down rather
than applying by eye: `tests/test_harness_encoding.py` uses Thai *as the test subject*. It exists
because Python decodes files and subprocess output with the machine's locale codec unless told
otherwise, which on a Thai Windows box is cp874 -- and that crashed `status --write`. Take the Thai
out of that file and the test still passes, on both the fixed code and the broken code, which makes
it worse than deleting it: a test that cannot fail reads as protection and is not.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Escaped, not literal: a checker written with the characters it forbids is its own first offender.
THAI = re.compile("[\\u0e00-\\u0e7f]")

# Relative to the repo root. Anything listed here must have a reason in this file's docstring.
EXEMPT = {"tests/test_harness_encoding.py"}

# Machine-generated and enormous. Nobody types Thai into a resolver lockfile, and reading it on
# every run costs more than the risk it covers.
SKIP_FILES = {"uv.lock"}


def files_to_check() -> list[Path]:
    """Everything git tracks, minus the two named exclusions.

    Deliberately not a list of file extensions. The first version was, and it silently skipped
    `.gitignore`, `LICENSE`, `pyproject.toml`, the plist and `templates/gitignore.append` — the last
    of which ships into installed repos. An allowlist of extensions grows a blind spot every time
    somebody adds a file type, and does it quietly. Asking git what exists inverts that: a new file
    is covered the moment it is tracked, and anything skipped has to be named here.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=True
    ).stdout.split()
    return [
        REPO_ROOT / rel
        for rel in tracked
        if rel not in EXEMPT and rel not in SKIP_FILES and (REPO_ROOT / rel).is_file()
    ]


def test_nothing_shipped_carries_thai() -> None:
    offenders = []
    for path in files_to_check():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if THAI.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{n}")

    assert not offenders, "Thai text ships to every repo that installs this plugin:\n  " + "\n  ".join(offenders)


def test_the_check_has_no_blind_spot() -> None:
    """A checker that covers most files is one somebody has to remember the edges of.

    The first version listed the extensions it knew about, which left `.gitignore`, `LICENSE`,
    `pyproject.toml`, `gitignore.append` and the plist unscanned -- including a template that ships
    into installed repos. Whether those files happened to be clean is not the point: the reason to
    write a check rather than delete a line by hand was so nobody has to hold the exceptions in
    their head, and a known gap only half does that.
    """
    tracked = {
        p
        for p in subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=True
        ).stdout.split()
    }
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in files_to_check()}

    missed = tracked - scanned - EXEMPT - SKIP_FILES
    assert not missed, "tracked files no Thai check ever looks at:\n  " + "\n  ".join(sorted(missed))


def test_the_encoding_test_still_holds_its_thai() -> None:
    """The exemption is load-bearing. If this file loses its Thai, it stops testing anything."""
    path = REPO_ROOT / "tests" / "test_harness_encoding.py"

    assert THAI.search(path.read_text(encoding="utf-8")), (
        "test_harness_encoding.py has no Thai left in it. It exists to prove the scripts do not "
        "depend on the machine's locale codec; with only ASCII in it, it passes on the broken code "
        "too."
    )
