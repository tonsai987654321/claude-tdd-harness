#!/usr/bin/env python3
"""Live usage brake, fed by the statusline snapshot.

The 5-hour / 7-day rate-limit figures exist only inside the statusline command's
stdin, which no tool can read directly. A statusline that tees them to
`~/.claude/state/usage.json` on every render is a PREREQUISITE THIS PLUGIN DOES NOT
INSTALL: it is the user's own statusline command, configured once outside the harness.
Without it there is never a snapshot, this always exits 2, and the brake is off — which
is safe but silent, so the message says which of the two it is.

    usage_guard.py                 -> human line + exit 0 (go) / 10 (brake) / 2 (unknown)
    usage_guard.py --cycle-start   -> same, and record this reading as a cycle boundary
    usage_guard.py --eta           -> seconds until the tripped window resets (+ buffer)
    usage_guard.py --json          -> the raw decision as JSON

Why this is a budget and not a threshold
----------------------------------------
A bare `pct >= 95` check is a point reading authorising an unbounded step. The caller
checks, then dispatches two subagents and however many rework rounds a cycle needs, and
does not look again until the next cycle starts. At 94% that is a green light for a step
that has historically cost 10-15 points — so the loop sails past 100 and dies mid-cycle,
which is the exact failure the brake exists to prevent.

So the guard measures the step. Every `--cycle-start` reading is appended to a trail;
the gap between two consecutive boundary readings inside one rate-limit window IS the
cost of one cycle. The brake fires when the *projected* end of the next cycle crosses
100, not when the current reading crosses a fixed line. With no history yet it assumes
DEFAULT_STEP, because "unknown cost" must not read as "free".

Why a stale snapshot no longer means "go"
-----------------------------------------
The statusline only renders when the main session does; measured, it stops rendering
within ~5s of the main thread blocking in a tool call and does not render again until
that call returns. A cycle blocks it for far longer than STALE_S. So "stale" is not a
rare degraded state — it is the *normal* state during the highest-burn interval, and
treating it as permission to proceed disabled the brake exactly when it mattered.

A stale snapshot still carries the last number that was true. That is a floor, not an
absence: usage only moves up inside a window. So a stale reading is extrapolated forward
(by the measured burn rate, or by one whole step if there is no rate yet) and the brake
decides on that. Only a missing, malformed, or figure-less snapshot is genuinely unknown.

Exit 2 (unknown) is the one case where the caller proceeds unbraked, and it now means
one thing only: there is no snapshot to read.

Both state files are per-account, not per-session, and that is right for the snapshot — the
rate-limit pool is shared, so a second Claude session spending against it is spending against
this loop too. The trail is shared as a consequence: two loops running at once interleave their
boundaries and the deltas between them come out too large. That biases the step upward and brakes
early, which is the survivable direction, so it is left unlocked rather than serialised.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Both streams, always. Windows gives this process the console codepage; an em dash in a message
# or a Thai cycle title then raises UnicodeEncodeError and the command dies reporting something.
# POSIX is already UTF-8, so this is a no-op there. See docs/lessons/0009.

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

SNAPSHOT = Path(os.path.expanduser(os.environ.get("USAGE_SNAPSHOT", "~/.claude/state/usage.json")))
TRAIL = Path(os.path.expanduser(os.environ.get("USAGE_TRAIL", "~/.claude/state/usage_trail.json")))

CEILING = float(os.environ.get("USAGE_BRAKE_PCT", "95"))  # absolute stop, whatever the projection
DEFAULT_STEP = float(os.environ.get("USAGE_DEFAULT_STEP", "15"))  # assumed cycle cost, no history
SAFETY = float(os.environ.get("USAGE_SAFETY", "1.3"))  # margin on the measured cost
STALE_S = float(os.environ.get("USAGE_STALE_S", "180"))
# Past this, the reading is not a live session's stale number — nothing is writing the file at all.
ABANDONED_S = float(os.environ.get("USAGE_ABANDONED_S", "3600"))
TRAIL_MAX = 40
BUFFER_S = 300  # resume 5 min past the reset

DECODE = {"encoding": "utf-8"}

# The two windows, in the order their name appears in the snapshot. Both are hard limits; braking
# on the 5h alone let a exhausted 7-day window kill the loop while the guard printed "go".
WINDOWS = ("five_hour", "seven_day")


def _load(path: Path, default):
    try:
        data = json.loads(path.read_text(**DECODE))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _num(value) -> float | None:
    """A figure the snapshot cannot be read as a number is a figure the guard does not have.

    Everything here comes from someone else's statusline through `jq`, so a field can arrive as a
    string, a null, or an ISO timestamp. Letting that raise would exit 1 — a code the caller has no
    branch for, from the one tool whose job is to be decidable.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_windows(data: dict) -> dict:
    """{name: {pct, resets_at}} for every window the snapshot actually carries a figure for."""
    out = {}
    for name in WINDOWS:
        w = data.get(name) or {}
        pct = _num(w.get("used_percentage")) if isinstance(w, dict) else None
        if pct is None:
            continue
        out[name] = {"pct": pct, "resets_at": _num(w.get("resets_at"))}
    return out


def record(trail: list, windows: dict, at: float, boundary: bool) -> list:
    entry = {"at": at, "boundary": boundary}
    for name, w in windows.items():
        entry[name] = w["pct"]
        entry[f"{name}_reset"] = w["resets_at"]
    trail = [e for e in trail if isinstance(e, dict)] + [entry]
    return trail[-TRAIL_MAX:]


def _same_window(a: dict, b: dict, name: str) -> bool:
    """Two readings are comparable only inside one rate-limit window.

    Across a reset the percentage falls, so a delta spanning the boundary is negative noise at
    best and, if the window reset mid-cycle, an underestimate of the real cost at worst. Drop it.
    """
    ra, rb = a.get(f"{name}_reset"), b.get(f"{name}_reset")
    return ra is not None and ra == rb and name in a and name in b


def measured_step(trail: list, name: str) -> float | None:
    """Cost of one cycle: the largest rise between consecutive boundary readings.

    Largest, not mean — the brake has to survive the expensive cycle, and a mean is dragged down
    by every cheap one. Mid-cycle readings are excluded on purpose: they measure part of a step,
    and a part-step passed off as the whole is headroom the caller does not have.

    The reading being judged is already in the trail when this runs, and belongs there: it is the
    closing end of the cycle that just finished, and so the freshest cost the guard will ever see.
    """
    # Only the recent past. `max` over the whole trail lets one freak cycle pin the estimate high
    # for as long as it survives in the file, and the brake stops tracking what cycles cost now.
    marks = [e for e in trail if isinstance(e, dict) and e.get("boundary") and name in e][-10:]
    deltas = [
        marks[i][name] - marks[i - 1][name]
        for i in range(1, len(marks))
        if _same_window(marks[i - 1], marks[i], name)
    ]
    rises = [d for d in deltas if d > 0]
    return max(rises) if rises else None


def burn_rate(trail: list, name: str) -> float | None:
    """Percent per second across the most recent usable pair of readings, boundary or not."""
    usable = [e for e in trail if isinstance(e, dict) and name in e]
    for i in range(len(usable) - 1, 0, -1):
        a, b = usable[i - 1], usable[i]
        if not _same_window(a, b, name):
            continue
        dt = float(b.get("at", 0)) - float(a.get("at", 0))
        dp = b[name] - a[name]
        if dt >= 30 and dp > 0:
            return dp / dt
    return None


def project(name: str, pct: float, age: float, trail: list) -> tuple[float, float, str]:
    """(effective_now, step, how) — what the reading is worth once its age is paid for."""
    step = measured_step(trail, name)
    how = "measured" if step is not None else "assumed"
    step = DEFAULT_STEP if step is None else step

    if age <= STALE_S:
        return pct, step, how

    # Cap the extrapolation, never the reading. `min(x, 100)` on a window already reporting 103
    # would talk the number *down* — a projection that lowers a measured figure is not a safety
    # margin, it is a fabrication in the direction that lets the loop continue.
    ceiling = max(100.0, pct)

    # Never charge more than one cycle, however long the silence. What a frozen snapshot conceals
    # is the work in flight, and that is one cycle — the loop checks again at the next boundary.
    # Multiplying a burst rate by an unbounded age instead produces a number that only grows, and
    # a brake that can never be satisfied is a stopped loop with no error to show for it.
    rate = burn_rate(trail, name)
    if rate is not None:
        charged = min(rate * age, step)
        return min(pct + charged, ceiling), step, f"{how}, +{charged:.0f}% for {int(age)}s of silence"
    # No rate yet. Charge the whole step: assuming nothing was spent is the assumption that put
    # the loop past 100 in the first place.
    return min(pct + step, ceiling), step, f"{how}, +1 step for {int(age)}s of silence"


def decide(now: float | None = None, boundary: bool = False, record_reading: bool = True) -> dict:
    now = time.time() if now is None else now
    data = _load(SNAPSHOT, {})
    if not data:
        # "not rendered yet" reads as "wait and it will appear". On a machine whose statusline
        # does not tee the usage figures, it never will, and the brake is off for good — the
        # message has to distinguish a slow start from a missing prerequisite.
        return {
            "status": "unknown",
            "reason": f"no snapshot at {SNAPSHOT} — the brake is OFF. It is written by your "
            "statusline command, not by the harness; if you have not configured one to tee the "
            "usage figures there, this will never change.",
        }

    windows = read_windows(data)
    if not windows:
        return {"status": "unknown", "reason": "snapshot carries no rate-limit figure"}

    # No readable timestamp means no way to tell a live reading from a frozen one. Treat it as
    # maximally old rather than as fresh — the stale path is the conservative one by design.
    captured = _num(data.get("captured_at"))
    age = max(0.0, now - captured) if captured is not None else STALE_S + 1

    # A snapshot older than its own window is not stale, it is spent: the limit has reset since it
    # was taken, so the number in it describes a window that no longer exists.
    live = {
        n: w for n, w in windows.items() if w["resets_at"] is None or w["resets_at"] > now
    }
    expired = sorted(set(windows) - set(live))

    # Older than any session would leave it: this is not a stale reading, it is an unattended file.
    # Extrapolating across it would charge a burst rate over hours and brake for good, on a figure
    # nothing is going to correct — a loop stopped by its own guard with nothing to show a reader.
    # Say the file is unattended instead, and let the reactive path carry the run.
    if live and age > ABANDONED_S:
        return {
            "status": "unknown",
            "reason": f"snapshot is {int(age // 60)} min old — nothing is refreshing it (a headless "
            f"run has no statusline). The brake cannot see this run; rely on the limit message if "
            f"a subagent dies.",
            "age_s": int(age),
            "windows": {},
        }

    if not live:
        return {
            "status": "go",
            "reason": f"snapshot predates the reset of {', '.join(expired)} — window rolled over",
            "age_s": int(age),
            "windows": {},
        }

    trail = record(_load(TRAIL, []), windows, now, boundary)
    if record_reading:
        try:
            TRAIL.parent.mkdir(parents=True, exist_ok=True)
            TRAIL.write_text(json.dumps(trail), **DECODE)
        except OSError:
            pass  # the trail is an optimisation; losing it degrades to DEFAULT_STEP, not to silence

    report, tripped = {}, []
    for name, w in live.items():
        eff, step, how = project(name, w["pct"], age, trail)
        projected = eff + step * SAFETY
        why = None
        if eff >= CEILING:
            why = f"{eff:.0f}% >= ceiling {CEILING:.0f}%"
        elif projected >= 100.0:
            why = f"{eff:.0f}% + one cycle ({step:.0f}% {how} x{SAFETY:g}) projects {projected:.0f}%"
        report[name] = {
            "used_percentage": w["pct"],
            "effective": round(eff, 1),
            "step": round(step, 1),
            "step_source": how,
            "projected": round(projected, 1),
            "resets_at": w["resets_at"],
            "tripped": why is not None,
        }
        if why:
            tripped.append((name, why))

    out = {
        "age_s": int(age),
        "stale": age > STALE_S,
        "ceiling": CEILING,
        "windows": report,
        "expired": expired,
    }
    if tripped:
        out["status"] = "brake"
        out["tripped"] = [n for n, _ in tripped]
        out["reason"] = "; ".join(f"{n}: {w}" for n, w in tripped)
    else:
        out["status"] = "go"
        out["reason"] = "; ".join(
            f"{n}: {r['effective']:.0f}% + {r['step']:.0f}% projects {r['projected']:.0f}%"
            for n, r in report.items()
        )
    return out


def eta_seconds(d: dict) -> int | None:
    """Seconds until the tripped window reopens, or None when that cannot be known.

    None is not 'use a default'. The only caller is the resume after a limit kill — the case where
    the snapshot is most likely to be missing — and a placeholder there schedules a wake that walks
    straight back into the closed window, burns half a cycle and dies again, on a loop. A number
    the guard cannot justify must not leave this function.
    """
    windows = d.get("windows") or {}
    tripped = d.get("tripped") or []
    if tripped:
        # Every tripped window has to reopen, so wait for the last of them.
        resets = [windows[n].get("resets_at") for n in tripped if windows.get(n, {}).get("resets_at")]
    else:
        # Not braked — this is the caller asking "when can I retry?" after something else killed
        # the run. The soonest reopening is the answer; taking the max here hands back the 7-day
        # reset and parks an otherwise healthy loop for days.
        resets = [w.get("resets_at") for w in windows.values() if w.get("resets_at")]
        resets = [min(resets)] if resets else []
    if not resets:
        return None
    return max(60, int(max(resets) - time.time()) + BUFFER_S)


def main() -> None:
    eta_only = "--eta" in sys.argv
    # `--eta` is asked seconds after the decision it follows up, off the same frozen snapshot. Its
    # reading is a duplicate, not an observation, and writing it puts two points a few seconds
    # apart into the series the burn rate is measured from.
    d = decide(boundary="--cycle-start" in sys.argv, record_reading=not eta_only)
    if eta_only:
        eta = eta_seconds(d)
        if eta is None:
            print(
                "no reset time in the snapshot — take it from the limit message that killed the "
                "run (e.g. 'resets 3am') and wait until 5 minutes past it.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(eta)
        return
    if "--json" in sys.argv:
        print(json.dumps(d))
    else:
        print(f"{d['status']}: {d['reason']}")
    # Exit codes: 0 go, 10 brake, 2 unknown.
    sys.exit({"go": 0, "brake": 10, "unknown": 2}[d["status"]])


if __name__ == "__main__":
    main()
