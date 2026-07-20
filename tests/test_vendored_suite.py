"""The vendored suite has to pass in the repo it is vendored into.

`init.sh` runs `.claude/harness-tests/` and writes `{"gate_verified": false}` when it is red. The
gate reads that file and refuses production code. So a test that passes here and fails there does
not merely look untidy in a scaffolded repo -- it bricks it: every guarded write is denied until
somebody works out that the failing test was never about their code.

That is exactly what shipped. Three tests added to this plugin drive `harness_init.py` or read
`templates/`, neither of which is vendored (deliberately -- see `NOT_VENDORED` and lesson 0003), so
every repo scaffolded from them came up with a red suite and a shut gate.

It shipped because this plugin's suite only ever ran *here*, where the plugin's own files are all
present. Nothing ran the suite the way an installed repo runs it. This test does, which is the only
version of this check that could have caught it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN_ROOT / "scripts" / "harness_init.py"


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("scaffolded")
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target),
         "--owner", "alice", "--project", "api:pytest:90"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return target


def test_the_vendored_suite_is_green_in_a_scaffolded_repo(scaffolded: Path) -> None:
    """The whole point. A red suite here means init.sh writes gate_verified=false and the gate
    refuses every guarded write in that repo."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(scaffolded / ".claude" / "harness-tests"), "-q"],
        capture_output=True, text=True, encoding="utf-8", cwd=scaffolded,
    )

    assert proc.returncode == 0, (
        "the vendored suite is red in an installed repo, which shuts that repo's gate:\n"
        + proc.stdout[-4000:]
    )
