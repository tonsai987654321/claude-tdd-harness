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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Escaped, not literal: a checker written with the characters it forbids is its own first offender.
THAI = re.compile("[\\u0e00-\\u0e7f]")

# Relative to the repo root. Anything listed here must have a reason in this file's docstring.
EXEMPT = {"tests/test_harness_encoding.py"}

SEARCHED = ("*.py", "*.md", "*.tmpl", "*.sh", "*.json", "*.yml", "*.yaml")

SKIP_DIRS = {".git", "__pycache__", ".venv", ".pytest_cache", "node_modules"}


def files_to_check() -> list[Path]:
    found = []
    for pattern in SEARCHED:
        for path in REPO_ROOT.rglob(pattern):
            if SKIP_DIRS & set(path.parts):
                continue
            if path.relative_to(REPO_ROOT).as_posix() in EXEMPT:
                continue
            found.append(path)
    return found


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


def test_the_encoding_test_still_holds_its_thai() -> None:
    """The exemption is load-bearing. If this file loses its Thai, it stops testing anything."""
    path = REPO_ROOT / "tests" / "test_harness_encoding.py"

    assert THAI.search(path.read_text(encoding="utf-8")), (
        "test_harness_encoding.py has no Thai left in it. It exists to prove the scripts do not "
        "depend on the machine's locale codec; with only ASCII in it, it passes on the broken code "
        "too."
    )
