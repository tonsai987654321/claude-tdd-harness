# Lesson 0001: "verified end to end" described the path we designed, not the surface we shipped

**Status:** active
**Date:** 2026-07-17
**Trigger:** you are about to call something ready to publish because an end-to-end run passed.

## Expected

The end-to-end run (scaffold → `init.sh` → RED → GREEN → evidence) covered the plugin, so it was
ready to publish.

## Happened

A later review traced four paths that run had never touched: `link_projects.sh` (never executed
once), the copied scaffolder, a malformed runner definition, and the installed cache. Three of the
four held a real bug.

## Why it got past us

The e2e was written alongside the feature, so it inherited the same blind spots. It proved the
design worked, not that the surface was sound.

## Next time

Before publishing, list the entry points a *stranger* will hit — including hand-edited config,
scripts nobody ran, and the copy in the cache — and check them off explicitly. Coverage of the flow
you built is not coverage of the surface you exposed.
