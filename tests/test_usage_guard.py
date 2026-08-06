"""The brake has to survive the step it authorises.

`usage_guard.py` was a point reading against a fixed line: `five_hour >= 95` means stop. But the
caller does not stop *at* the reading — it reads once, then dispatches a whole cycle (two
subagents plus rework rounds) and does not look again until the next cycle begins. A cycle has
historically cost 10-15 points. So a 94% reading, correctly under the line, was a green light for
a step that ends at 105%, and the loop died mid-cycle at exactly the number the brake existed to
prevent. Observed: 103%, 105%.

Three separate holes fed the same failure, and each is pinned here:

* **No headroom.** The threshold left 5 points for a step that costs three times that. The guard
  now measures the step from its own trail and brakes on the *projection*, not the reading.
* **Stale meant go.** The statusline stops rendering within seconds of the main thread blocking in
  a tool call, so "stale" is the normal state while a cycle runs — the highest-burn interval was
  the one interval the brake sat out. A stale reading is now a floor to extrapolate from, not an
  absence, and `unknown` means only "there is no snapshot".
* **`--eta` invented a number.** With no snapshot it printed 300 and exited 0, indistinguishable
  from a real five-minute wait, so the post-kill resume woke into the still-closed window, burned
  a partial cycle, died, and repeated. It now refuses rather than guessing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "usage_guard.py"

GO, BRAKE, UNKNOWN = 0, 10, 2


def snapshot(five=None, seven=None, age=0.0, now=None):
    now = time.time() if now is None else now
    data = {"captured_at": now - age}
    if five is not None:
        pct, resets = five
        data["five_hour"] = {"used_percentage": pct, "resets_at": now + resets}
    if seven is not None:
        pct, resets = seven
        data["seven_day"] = {"used_percentage": pct, "resets_at": now + resets}
    return data


@pytest.fixture
def state(tmp_path: Path):
    class State:
        snap = tmp_path / "usage.json"
        trail = tmp_path / "usage_trail.json"

        def write(self, data):
            self.snap.write_text(json.dumps(data), encoding="utf-8")

        def seed_trail(self, entries):
            self.trail.write_text(json.dumps(entries), encoding="utf-8")

        def run(self, *args, **env):
            return subprocess.run(
                [sys.executable, str(GUARD), *args],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "USAGE_SNAPSHOT": str(self.snap),
                    "USAGE_TRAIL": str(self.trail),
                    **env,
                },
            )

        def decision(self, *args, **env):
            r = self.run("--json", *args, **env)
            return json.loads(r.stdout), r

    return State()


# --- unknown means one thing: there is no snapshot ----------------------------------------------


def test_a_missing_snapshot_is_unknown_and_says_it_may_never_appear(state):
    r = state.run()

    assert r.returncode == UNKNOWN
    assert str(state.snap) in r.stdout
    assert "statusline" in r.stdout, "the message has to name the missing prerequisite"


def test_a_snapshot_without_any_figure_is_unknown_not_a_green_light(state):
    state.write({"captured_at": time.time(), "five_hour": {}})

    assert state.run().returncode == UNKNOWN


# --- headroom: the brake covers the cycle it is about to authorise ------------------------------


def test_a_low_reading_proceeds(state):
    state.write(snapshot(five=(10, 3600)))

    assert state.run("--cycle-start").returncode == GO


def test_a_reading_under_the_ceiling_still_brakes_when_one_cycle_would_cross_100(state):
    """84% + a 15% cycle = 99%, x1.3 safety = past 100. The old guard called this go."""
    state.write(snapshot(five=(84, 3600)))

    d, r = state.decision("--cycle-start")

    assert r.returncode == BRAKE
    assert d["windows"]["five_hour"]["step_source"] == "assumed"
    assert d["windows"]["five_hour"]["projected"] >= 100


def test_the_ceiling_still_stops_it_outright(state):
    state.write(snapshot(five=(96, 3600)))

    d, r = state.decision()

    assert r.returncode == BRAKE
    assert "ceiling" in d["reason"]


def test_the_step_is_measured_from_the_gap_between_two_cycle_starts(state):
    now = time.time()
    reset = now + 3600
    state.seed_trail(
        [
            {"at": now - 4000, "boundary": True, "five_hour": 20, "five_hour_reset": reset},
            {"at": now - 2000, "boundary": True, "five_hour": 26, "five_hour_reset": reset},
        ]
    )
    state.write(snapshot(five=(30, 3600), now=now))

    d, r = state.decision("--cycle-start")

    # 20 -> 26 -> 30: the largest cycle cost seen is 6, not the mean and not the latest. The
    # reading being judged closes the most recent step, so it counts as a boundary itself.
    assert d["windows"]["five_hour"]["step"] == 6
    assert d["windows"]["five_hour"]["step_source"] == "measured"
    assert r.returncode == GO


def test_mid_cycle_readings_do_not_get_mistaken_for_the_cost_of_a_whole_cycle(state):
    """Half a step passed off as the whole one is headroom the caller does not have."""
    now = time.time()
    reset = now + 3600
    state.seed_trail(
        [
            {"at": now - 4000, "boundary": True, "five_hour": 20, "five_hour_reset": reset},
            {"at": now - 3000, "boundary": False, "five_hour": 45, "five_hour_reset": reset},
        ]
    )
    state.write(snapshot(five=(50, 3600), now=now))

    d, _ = state.decision("--cycle-start")

    # The cycle ran 20 -> 50 and cost 30. Counting the mid-cycle reading as a boundary would
    # report the 5 points spent after it and call that the price of a cycle.
    assert d["windows"]["five_hour"]["step"] == 30, (
        "a non-boundary reading was counted as a cycle boundary"
    )


def test_a_gap_across_a_window_reset_is_not_a_cycle_cost(state):
    now = time.time()
    state.seed_trail(
        [
            {"at": now - 4000, "boundary": True, "five_hour": 5, "five_hour_reset": now - 100},
            {"at": now - 2000, "boundary": True, "five_hour": 40, "five_hour_reset": now + 3600},
        ]
    )
    state.write(snapshot(five=(50, 3600), now=now))

    d, _ = state.decision("--cycle-start")

    # 5 -> 40 spans a reset and is not a 35-point cycle; only 40 -> 50 happened inside one window.
    assert d["windows"]["five_hour"]["step"] == 10


# --- staleness is a floor, not an absence -------------------------------------------------------


def test_a_stale_reading_is_charged_a_cycle_rather_than_waved_through(state):
    """A frozen snapshot is what a running cycle looks like. 70% + one step is over the line."""
    state.write(snapshot(five=(70, 3600), age=600))

    d, r = state.decision()

    assert r.returncode == BRAKE, "a stale snapshot was treated as permission to proceed"
    assert d["stale"] is True
    assert d["windows"]["five_hour"]["effective"] > 70


def test_a_stale_reading_is_extrapolated_by_the_measured_burn_rate(state):
    now = time.time()
    reset = now + 3600
    state.seed_trail(
        [
            {"at": now - 1200, "boundary": True, "five_hour": 10, "five_hour_reset": reset},
            {"at": now - 600, "boundary": True, "five_hour": 16, "five_hour_reset": reset},
        ]
    )
    state.write(snapshot(five=(16, 3600), age=600, now=now))

    d, _ = state.decision()

    # 6 points per 600s, carried over 600s of silence: ~22%, not the 16% the file still says.
    assert 21 <= d["windows"]["five_hour"]["effective"] <= 23


def test_extrapolation_never_charges_more_than_one_cycle(state):
    """A burst rate times an unbounded age is a brake that can never be satisfied."""
    now = time.time()
    reset = now + 400000
    state.seed_trail(
        [
            {"at": now - 3400, "boundary": True, "seven_day": 10, "seven_day_reset": reset},
            {"at": now - 3200, "boundary": True, "seven_day": 20, "seven_day_reset": reset},
        ]
    )
    state.write(snapshot(seven=(20, 400000), age=3000, now=now))

    d, r = state.decision()

    # 0.05%/s over 3000s of silence is 150 points. One cycle cost 10, and 10 is the charge.
    assert d["windows"]["seven_day"]["effective"] == 30
    assert r.returncode == GO


def test_a_snapshot_nothing_is_refreshing_is_reported_not_extrapolated(state):
    """The headless resume has no statusline. Braking forever on an unattended file is a silent stop."""
    state.write(snapshot(seven=(40, 400000), age=20000))

    r = state.run()

    assert r.returncode == UNKNOWN
    assert "refreshing" in r.stdout


def test_a_snapshot_older_than_its_own_window_is_spent_not_stale(state):
    now = time.time()
    state.write(snapshot(five=(99, -60), age=7200, now=now))

    d, r = state.decision()

    assert r.returncode == GO
    assert "rolled over" in d["reason"]


# --- both windows are hard limits ---------------------------------------------------------------


def test_an_exhausted_seven_day_window_brakes_while_the_five_hour_is_fine(state):
    state.write(snapshot(five=(5, 3600), seven=(97, 200000)))

    d, r = state.decision()

    assert r.returncode == BRAKE
    assert d["tripped"] == ["seven_day"]


# --- --eta refuses rather than inventing a wait --------------------------------------------------


def test_eta_refuses_when_there_is_no_snapshot_to_read(state):
    r = state.run("--eta")

    assert r.returncode != 0
    assert r.stdout.strip() == "", f"a number the guard cannot justify reached the caller: {r.stdout!r}"


def test_eta_refuses_when_the_snapshot_carries_no_reset_time(state):
    state.write({"captured_at": time.time(), "five_hour": {"used_percentage": 99}})

    r = state.run("--eta")

    assert r.returncode != 0
    assert r.stdout.strip() == ""


def test_eta_does_not_add_its_own_reading_to_the_trail(state):
    """It follows a decision by seconds off the same frozen file: a duplicate, not an observation."""
    state.write(snapshot(five=(96, 3600)))
    state.run("--cycle-start")
    before = state.trail.read_text(encoding="utf-8")

    state.run("--eta")

    assert state.trail.read_text(encoding="utf-8") == before


def test_eta_comes_from_the_window_that_tripped(state):
    state.write(snapshot(five=(5, 3600), seven=(97, 200000)))

    r = state.run("--eta")

    assert r.returncode == 0
    assert int(r.stdout) > 190000, "the wait was taken from the untripped 5h window"


def test_eta_after_an_unrelated_kill_returns_the_soonest_window_not_the_furthest(state):
    """Nothing tripped: the caller is asking when it may retry, and 5h reopens long before 7d."""
    state.write(snapshot(five=(40, 3600), seven=(50, 400000)))

    r = state.run("--eta")

    assert r.returncode == 0
    assert int(r.stdout) < 4000, "an unbraked loop was parked until the 7-day window reset"


# --- readings the guard cannot trust ------------------------------------------------------------


def test_a_figure_that_is_not_a_number_does_not_crash_the_brake(state):
    state.write(
        {
            "captured_at": time.time(),
            "five_hour": {"used_percentage": "n/a", "resets_at": "2026-08-06T12:00:00Z"},
        }
    )

    r = state.run("--cycle-start")

    assert r.returncode == UNKNOWN, "an unparseable figure exited with a code the caller cannot read"
    assert "Traceback" not in r.stderr


def test_an_unreadable_timestamp_is_treated_as_old_not_as_fresh(state):
    state.write({"captured_at": None, "five_hour": {"used_percentage": 70, "resets_at": time.time() + 3600}})

    d, r = state.decision()

    assert d["stale"] is True
    assert r.returncode == BRAKE


def test_a_reading_already_past_100_is_never_talked_back_down(state):
    """min(pct, 100) on a stale 103% window is a fabrication in the permissive direction."""
    state.write(snapshot(five=(103, 3600), age=600))

    d, r = state.decision()

    assert d["windows"]["five_hour"]["effective"] >= 103
    assert r.returncode == BRAKE


def test_one_freak_cycle_does_not_pin_the_estimate_forever(state):
    now = time.time()
    reset = now + 100000
    marks = [{"at": now - 9000, "boundary": True, "five_hour": 0, "five_hour_reset": reset}]
    marks.append({"at": now - 8900, "boundary": True, "five_hour": 40, "five_hour_reset": reset})
    # Eleven cheap cycles after it push the 40-point outlier out of the window that is consulted.
    for i in range(11):
        marks.append(
            {"at": now - 8000 + i * 100, "boundary": True, "five_hour": 41 + i, "five_hour_reset": reset}
        )
    state.seed_trail(marks)
    state.write(snapshot(five=(52, 100000), now=now))

    d, _ = state.decision("--cycle-start")

    assert d["windows"]["five_hour"]["step"] == 1


# --- degradation ---------------------------------------------------------------------------------


def test_an_unwritable_trail_degrades_to_the_assumed_step_and_still_decides(state, tmp_path):
    state.write(snapshot(five=(84, 3600)))

    r = state.run("--cycle-start", USAGE_TRAIL=str(tmp_path / "no" / "such" / "dir" / "t.json"))

    assert r.returncode == BRAKE
    assert "Traceback" not in r.stderr


def test_a_corrupt_trail_is_ignored_rather_than_fatal(state):
    state.trail.write_text("{not json", encoding="utf-8")
    state.write(snapshot(five=(10, 3600)))

    r = state.run("--cycle-start")

    assert r.returncode == GO
    assert "Traceback" not in r.stderr
