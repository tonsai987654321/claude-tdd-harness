---
description: Resume the build from wherever it stopped — next unfinished cycle across all projects, in build order. Self-heals across usage-limit resets.
---

You are running the build **continuously**. Do not stop between cycles for approval; the user has asked for autonomous progress.

## Each turn

0a. **Arm the in-session flag** so the statusline shows `auto-cont: TRUE` while this loop runs (self-expires if the session dies):

   ```bash
   bash "$CLAUDE_PROJECT_DIR/.claude/scripts/autocont.sh" on 3600
   ```

   Disarm it (`autocont.sh off`) whenever you STOP the loop for good — on `DONE`, on a `blocked` cycle, or when the user says stop — and also just before a `ScheduleWakeup` that ends the loop.

0. **Check the usage brake first.** Before starting any cycle, ask whether the *next whole cycle* fits in what is left — not whether the current reading is under a line:

   > The snapshot is `~/.claude/state/usage.json`, and **the harness does not create it** — the rate-limit figures exist only in the statusline command's stdin, so a statusline that tees them there is a prerequisite the user configures once, outside this plugin. Without it the guard returns exit 2 forever and the loop runs unbraked on the reactive path alone. Say so rather than treating a permanent `unknown` as normal.

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py" --cycle-start   # 0 go · 10 brake · 2 unknown
   ```

   `--cycle-start` marks this reading as a cycle boundary in the guard's trail. The gap between two boundaries is what a cycle costs, and that measurement is the whole basis of the brake — **pass the flag here and nowhere else**, or the guard learns the price of half a cycle and leaves you short exactly that much.

   - **exit 10 (brake)** — do NOT start a cycle. Either the reading is at the ceiling, or one more cycle at the measured price would cross 100 and you would die mid-cycle. Pause cleanly at this boundary:

     ```bash
     python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py" --eta   # seconds to reset + 5min
     ```

     - Exit 0 → that number is the wait. Re-arm the flag to cover it (`autocont.sh on $((eta + 600))`) so the statusline does not read FALSE while the loop is merely paused, then `ScheduleWakeup` with `delaySeconds` = that value (runtime clamps to [60, 3600]; if the true wait is longer, schedule 3600 and re-check on wake), `prompt` = `<<autonomous-loop-dynamic>>`, `reason` = the guard's own line. Then stop — do not dispatch.
     - **Non-zero → the guard has no reset time and is refusing to invent one.** Do not substitute a default. Take the reset from the limit message that killed the run and wait until 5 minutes past it; if there is no such message either, stop the loop and say so.
   - **exit 2 (unknown)** — there is no usable measurement: either no snapshot at all, or one so old that nothing is refreshing it (a headless `claude -p` run has no statusline, so it never writes one). A merely *stale* reading no longer lands here — it is extrapolated and can brake. Report which of the two the guard said, then proceed on the reactive path below.
   - **exit 0 (go)** — the next cycle fits; continue to step 1.

1. Ask the harness what is next:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/next_cycle.py"
   ```

   - `DONE` → every cycle of every project is done and evidenced. Run each project's DoD check, report final status, and **stop** (do not schedule another wakeup — call `ScheduleWakeup` with `stop: true`).
   - `BUILD <project> <id> <title>` → that is the cycle to run.

2. **Recover first if the last run was interrupted.** A subagent killed mid-cycle leaves the cycle `red` with the gate `OPEN` and an uncommitted test in the project tree (see `harness.py lessons`). Before dispatching:
   - `git -C projects/<project> status --short` — if there is an untracked/modified test with no matching commit, move it aside (it was never gated) and confirm the baseline suite passes.
   - If `.claude/state/<project>.json` shows the target cycle `red` but no `[RED]` commit exists for it, reset it: `harness.py cycle <project> <id> queued -`, and set the gate back to SHUT.

3. Run the cycle exactly as `/harness-build` specifies: mark it `red`, dispatch `tdd-implementer`, then `cycle-reviewer`, close with `--evidence` on PASS, push. Cycle 0 of a project is scaffold — do it in the main thread.

   **Between the two subagents, check the brake again** — without `--cycle-start`:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py"
   ```

   The moment `tdd-implementer` returns is the first fresh rate-limit reading since the cycle began; the statusline is frozen for the whole of a subagent's run, so this is the only mid-cycle number that exists. On exit 10, do not dispatch the reviewer: leave the cycle `red` (its RED commit stands, and step 2 restarts it cleanly), then pause as in step 0. Half a cycle paused is recoverable; a reviewer killed halfway through is the interrupted state step 2 has to clean up.

4. After the cycle closes, refresh `harness.py status --write` and `harness.py handoff`, then **loop back to step 1** for the next cycle. The guard records the cost of the cycle you just ran on its own, at the next `--cycle-start` — there is nothing to log by hand.

## When a subagent dies from a usage limit

The failure message names a reset time, e.g. "resets 3am (Asia/Bangkok)". Do not retry immediately — the limit resets at a fixed clock time, so an immediate retry just burns another partial cycle. Instead:

1. Recover the interrupted cycle (step 2 above) so the tree is clean and restartable.
2. Compute the wait — seconds until 5 minutes past the reset:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py" --eta
   ```

   This reads the reset time from the statusline snapshot. **A headless run has no snapshot, so this will exit non-zero and print nothing** — that is the guard refusing to guess, not a failure to handle. Take the reset from the failure message instead, e.g. "resets 3am" → wait until 3:05am local. Never fall back to a fixed short delay: it wakes into the same closed window, burns another partial cycle, and dies again on a loop.
3. Schedule the resume with `ScheduleWakeup`:
   - `delaySeconds`: the value above, but the runtime clamps to [60, 3600]. If the true wait exceeds 3600s, schedule 3600 and re-check on wake (hop until the reset has actually passed — verify by dispatching once; if it dies again, the limit is not back yet, so schedule the next reset window).
   - `prompt`: the literal sentinel `<<autonomous-loop-dynamic>>` (this re-enters continuous mode on wake).
   - `reason`: e.g. "usage limit hit; resuming 5 min after 3am Bangkok reset".

On wake, start again at step 1.

> **`ScheduleWakeup` is in-session only.** It dies when the process exits, so in a headless
> `claude -p` resume (the launchd path, `auto_resume.sh`) the wake never fires — the launchd tick
> *is* the durable layer there, and it will relaunch this command on its own schedule. Scheduling a
> wake in that context is harmless but does nothing; the durable resume is the cron/launchd job.

## Guardrails

- Never fake evidence to get past the `done` gate. A cycle that cannot be evidenced is not done — leave it `red`/`blocked` and say why.
- Never make a repo public or merge without the user asking.
- If a cycle is `blocked` (two rework rounds failed), stop the loop and report — that needs a human, not another wakeup.
- Push after every green cycle. Work that only exists on this laptop did not happen.
