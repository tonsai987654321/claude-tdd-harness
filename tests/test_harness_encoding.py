"""The harness scripts must not depend on the machine's locale encoding.

Python opens text files, and decodes a child process's output, with the *locale* codec unless the
call names one. On a Thai Windows box that codec is cp874, so `status --write` died trying to put a
`·` into PROGRESS.md -- and evidence text, cycle titles and git subjects in this project are Thai,
so the quieter half of the bug is corruption rather than a crash: the state JSON round-trips through
whatever codepage the machine happens to have.

`-X warn_default_encoding` makes every open/read_text/write_text/subprocess(text=True) that omits
`encoding=` raise EncodingWarning, and `-W error::EncodingWarning` turns that into a crash. That is
what makes these tests catch the bug on a UTF-8 machine (macOS, CI) too, where the ambient locale
would otherwise paper over it -- the tests would still pass on the broken code without it.

Each test drives the real CLI against a throwaway ROOT (harness.py takes it from
CLAUDE_PROJECT_DIR), so nothing here touches the repo's own PROGRESS.md, HANDOFF.md or state.
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
NEXT_CYCLE = REPO_ROOT / "scripts" / "next_cycle.py"

# Thai, and the `·` that actually crashed `status --write`. Every assertion below round-trips this
# through a file or a subprocess boundary.
THAI_TITLE = "อ่านค่ามิเตอร์ · cycle แรก"


def run_harness(
    root: Path, *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI against `root`, with any locale-dependent text I/O made fatal."""
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "warn_default_encoding",
            "-W",
            "error::EncodingWarning",
            str(HARNESS),
            *args,
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root), **(env_extra or {})},
        cwd=root,
    )
    # EncodingWarning is 3.10+ (PEP 597). An older interpreter prints "Invalid -W option ignored"
    # and runs on with the guard silently off, which would make every test here pass against the
    # broken code. Refuse to report a result the guard did not actually cover.
    assert "Invalid -W option" not in proc.stderr, (
        f"{sys.executable} predates EncodingWarning, so these tests cannot see the bug they "
        f"exist to catch. Run the suite on 3.10+ (init.sh pins 3.12)."
    )
    return proc


def run_next_cycle(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the real next_cycle.py against `root`, with locale-dependent text I/O made fatal."""
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "warn_default_encoding",
            "-W",
            "error::EncodingWarning",
            str(NEXT_CYCLE),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        cwd=root,
    )
    assert "Invalid -W option" not in proc.stderr, (
        f"{sys.executable} predates EncodingWarning, so this test cannot see the bug it "
        f"exists to catch. Run the suite on 3.10+ (init.sh pins 3.12)."
    )
    return proc


@pytest.fixture
def harness_root(tmp_path: Path) -> Path:
    """A throwaway repo root holding one seeded project."""
    cycles = tmp_path / ".claude" / "cycles"
    cycles.mkdir(parents=True)
    (cycles / "demo-api.json").write_text(
        json.dumps(
            {
                "build_order": 1,
                "runner": "pytest",
                "cycles": [{"id": 1, "title": THAI_TITLE}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_status_write_survives_a_non_utf8_locale(harness_root: Path) -> None:
    """`status --write` must produce a UTF-8 PROGRESS.md whatever codepage the machine uses."""
    proc = run_harness(harness_root, "status", "--write")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert THAI_TITLE in (harness_root / "PROGRESS.md").read_text(encoding="utf-8")


def test_handoff_survives_a_non_utf8_locale(harness_root: Path) -> None:
    """`handoff` writes HANDOFF.md and shells out to git; neither may use the locale codec."""
    proc = run_harness(harness_root, "handoff")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert THAI_TITLE in (harness_root / "HANDOFF.md").read_text(encoding="utf-8")


def test_handoff_reports_a_thai_commit_subject_verbatim(harness_root: Path) -> None:
    """The handoff quotes each project's git HEAD, so git's output must be decoded as UTF-8.

    ``git`` emits its log as UTF-8 bytes. Decoding them with the locale codec does not raise on a
    Thai codepage -- it silently mojibakes -- so this is the half of the bug that a crash-only test
    would never see. Without a real repo here, harness.py returns early and never shells out.
    """
    repo = harness_root / "projects" / "demo-api"
    repo.mkdir(parents=True)
    subject = "feat: คำนวณค่าไฟ"
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", subject],
    ):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)

    proc = run_harness(harness_root, "handoff")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert subject in (harness_root / "HANDOFF.md").read_text(encoding="utf-8")


def test_thai_evidence_round_trips_through_the_state_file(harness_root: Path) -> None:
    """Evidence is the harness's anti-false-done record, so it must come back out byte-for-byte.

    ``cycle ... done --evidence`` writes state JSON and the dashboard reads it back. Both ends
    used the locale codec, so on a Thai box the record of *why* a cycle was allowed to close was
    the thing at risk -- the one piece of state the whole constitution rests on.
    """
    evidence = "pytest 24 ผ่าน, cov 93%; ruff สะอาด; a1b2c3d [RED] · e4f5g6h [GREEN]"

    done = run_harness(harness_root, "cycle", "demo-api", "1", "done", "--evidence", evidence)
    assert done.returncode == 0, done.stdout + done.stderr

    state_file = harness_root / ".claude" / "state" / "demo-api.json"
    assert json.loads(state_file.read_text(encoding="utf-8"))["cycles"][0]["evidence"] == evidence

    # The board only reports *whether* evidence exists (harness.py renders "yes" or "MISSING"), so
    # the round-trip that matters is reading the state back: a mojibaked file still parses as JSON,
    # and a cycle would go on claiming it was proven with a garbled record behind it.
    board = run_harness(harness_root, "status", "--write")
    assert board.returncode == 0, board.stdout + board.stderr
    assert THAI_TITLE in (harness_root / "PROGRESS.md").read_text(encoding="utf-8")


def test_next_cycle_survives_a_state_file_holding_non_ascii(harness_root: Path) -> None:
    """`/continue` and the auto-resume ask next_cycle.py where to pick up. It reads the state
    harness.py wrote, so it must decode it the way harness.py encoded it: UTF-8, not the locale.

    This is the half of the class `aa966a9` missed. That commit pinned harness.py -- including its
    `build_order`, which reads each cycle file -- but next_cycle.py carries a near-identical copy of
    that function and three more locale reads, and was left alone. The miss stayed invisible because
    the same commit's `ensure_ascii=False` is what put raw UTF-8 into the state file: while titles
    were still \\uXXXX-escaped ASCII, a cp874 read of them happened to work. Repairing the state is
    what detonates this, which is the worst possible time to find it -- the resume path is exactly
    what a fresh session leans on.

    Both directions are covered: reading the title (UnicodeDecodeError on cp874) and printing it
    back out (`·` has no cp874 encoding, so an unreconfigured stdout dies on write).
    """
    # Seed the state file through the real CLI, so it is encoded exactly as production encodes it.
    seeded = run_harness(harness_root, "cycle", "demo-api", "1", "queued")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr

    proc = run_next_cycle(harness_root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Verbatim, not mojibaked: a locale round-trip would corrupt this while still "working".
    assert proc.stdout.strip() == f"BUILD demo-api 1 {THAI_TITLE}", proc.stdout


def test_status_reads_a_transcript_holding_non_ascii(harness_root: Path) -> None:
    """Token accounting parses the session transcript, which is UTF-8 JSONL written by Claude Code.

    Read with the locale codec, a Thai turn either explodes or -- because this reader passes
    errors="replace" -- quietly becomes U+FFFD, which then fails json.loads and is swallowed by
    iter_json's except. The turn's tokens simply vanish from the dashboard, silently.
    """
    agent_task = "อ่านค่ามิเตอร์ย้อนหลัง"
    transcript = harness_root / "session.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(line, ensure_ascii=False)
            for line in (
                {"type": "assistant", "message": {"usage": {"input_tokens": 1200, "output_tokens": 340}}},
                {
                    "toolUseResult": {
                        "agentId": "a1",
                        "description": agent_task,
                        "resolvedModel": "claude-opus-4-8",
                    }
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    subagents = harness_root / "session" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a1.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "attributionAgent": "pea-tdd-implementer",
                "message": {"usage": {"input_tokens": 1000, "output_tokens": 540}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    proc = run_harness(
        harness_root, "status", "--write", env_extra={"CLAUDE_TRANSCRIPT_PATH": str(transcript)}
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    board = (harness_root / "PROGRESS.md").read_text(encoding="utf-8")
    # The Thai task description travels transcript -> board, and the subagent's 1000+540 billable
    # tokens are attributed to it.
    assert f"pea-tdd-implementer: {agent_task}" in board, board
    assert "1.5k" in board, board
