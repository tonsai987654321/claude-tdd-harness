# Lesson 0014: a limit check is only as good as the step it authorises

**Status:** mechanised
**Enforced by:** tests/test_usage_guard.py
**Date:** 2026-08-06
**Trigger:** you are about to guard an autonomous loop with a threshold on a live measurement

## Expected

`usage_guard.py` read the 5-hour figure before every cycle and refused to start one at 95% or
above. Five points of margin, checked every cycle, on a number the statusline refreshes constantly.
The loop should have stopped at 95% and scheduled itself past the reset.

## Happened

It died mid-cycle at 103% and 105%.

Three separate holes, all pointing the same way — every one of them resolved an absence into
permission to keep going.

1. **The check did not know how big a cycle was.** It read once at the cycle boundary, then
   dispatched two subagents and however many rework rounds the cycle needed, and did not look
   again. A cycle costs 10-15 points. So 94% was, correctly, under the line, and also a green
   light for a step that ends past 100. The margin was a third of the step it was guarding.

2. **Stale meant go.** A snapshot older than 180s returned `unknown`, and the caller was told to
   proceed. Measured: the statusline stops rendering within ~5s of the main thread blocking in a
   tool call and does not render again until it returns. A subagent blocks it for minutes. So
   "stale" was not a rare degraded state — it was the *normal* state during the only interval that
   burns fast, and the brake sat out every one of them.

3. **`--eta` invented a wait.** With no snapshot it printed `300` and exited 0, which the caller
   could not tell from a real five-minute reset. Its only caller is the resume after a limit kill —
   precisely when the snapshot is missing. So the loop woke into the still-closed window, burned a
   partial cycle, died, and repeated.

## Why it got past us

The number was live and the check ran often, and that felt like control. But a guard does not
protect the instant it runs; it protects everything that happens before it runs again. Nothing in
the design ever compared the margin to the interval, because the threshold was chosen against the
*limit* (95 is near 100) rather than against the *step* (95 is one third of a cycle from 100).

The other two are this repo's oldest defect in new clothes: a reading the code failed to obtain,
flattened into a value it then acted on. Stale became "fine". Missing became `300`.

## Next time

**Guard the step, not the instant.** If a check authorises work whose cost you do not know,
measuring that cost is the fix — not lowering the threshold, which only moves where you die. The
guard now records every cycle boundary and brakes when `now + measured_step * safety` crosses 100,
so it self-calibrates to what cycles actually cost on this machine, this week.

**A degraded reading is a floor, not an absence.** Usage only rises inside a window, so a stale
number is a lower bound to extrapolate from — not permission. Reserve `unknown` for "there is no
measurement at all", and make sure that is the only thing it can mean.

**A value the code cannot justify must not leave the function.** `--eta` returns nothing and exits
non-zero rather than a plausible default, because a caller cannot distinguish a good default from a
guess, and will act on both.

**And the correction has the same failure mode as the thing it corrects.** The first version of
this fix extrapolated the stale reading by the measured burn rate with no bound on the age. On a
headless overnight run — where nothing refreshes the file — that charges a burst rate across hours,
brakes on a figure nothing will ever correct, and stops the loop silently, which is the outcome the
whole guard exists to avoid, reached from the other side. Extrapolation now charges at most one
cycle, and past an hour the guard stops extrapolating and says the file is unattended. A safety
margin needs a bound in both directions: too little and it does not protect, too much and it is a
brake that can never be released.
