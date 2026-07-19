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

0. **Check the usage brake first.** Before starting any cycle, read the live 5-hour usage from the statusline snapshot:

   > The snapshot is `~/.claude/state/usage.json`, and **the harness does not create it** — the rate-limit figures exist only in the statusline command's stdin, so a statusline that tees them there is a prerequisite the user configures once, outside this plugin. Without it the guard returns exit 2 forever and the loop runs unbraked on the reactive path alone. Say so rather than treating a permanent `unknown` as normal.

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py"    # exit 0 go · 10 brake · 2 unknown
   ```

   - **exit 10 (>= 95%)** — do NOT start a new cycle; you would die mid-cycle. Pause cleanly at this boundary: compute the wake and schedule it, then stop.

     ```bash
     python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/usage_guard.py" --eta   # seconds to reset + 5min
     ```

     `ScheduleWakeup` with that `delaySeconds` (runtime clamps to [60, 3600]; if the true wait is longer, schedule 3600 and re-check on wake), `prompt` = `<<autonomous-loop-dynamic>>`, `reason` = "5h usage >= 95%; resuming after reset". Then stop — do not dispatch.
   - **exit 2 (unknown/stale)** — no fresh snapshot (headless run, or statusline hasn't rendered). Proceed, but rely on the reactive path below if a subagent then dies.
   - **exit 0 (go)** — under threshold; continue to step 1.

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

4. After the cycle closes, refresh `harness.py status --write` and `harness.py handoff`, then **loop back to step 1** for the next cycle.

## When a subagent dies from a usage limit

The failure message names a reset time, e.g. "resets 3am (Asia/Bangkok)". Do not retry immediately — the limit resets at a fixed clock time, so an immediate retry just burns another partial cycle. Instead:

1. Recover the interrupted cycle (step 2 above) so the tree is clean and restartable.
2. Compute the wait:

   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/scripts/reset_eta.py" <reset-time>   # e.g. 3am
   ```

   This returns seconds until 5 minutes past the reset.
3. Schedule the resume with `ScheduleWakeup`:
   - `delaySeconds`: the value above, but the runtime clamps to [60, 3600]. If the true wait exceeds 3600s, schedule 3600 and re-check on wake (hop until the reset has actually passed — verify by dispatching once; if it dies again, the limit is not back yet, so schedule the next reset window).
   - `prompt`: the literal sentinel `<<autonomous-loop-dynamic>>` (this re-enters continuous mode on wake).
   - `reason`: e.g. "usage limit hit; resuming 5 min after 3am Bangkok reset".

On wake, start again at step 1.

## Guardrails

- Never fake evidence to get past the `done` gate. A cycle that cannot be evidenced is not done — leave it `red`/`blocked` and say why.
- Never make a repo public or merge without the user asking.
- If a cycle is `blocked` (two rework rounds failed), stop the loop and report — that needs a human, not another wakeup.
- Push after every green cycle. Work that only exists on this laptop did not happen.
