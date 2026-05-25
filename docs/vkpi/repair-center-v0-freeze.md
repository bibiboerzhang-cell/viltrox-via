# Repair Center v0 Freeze

Date: 2026-05-24

Repair Center v0 is frozen as a dry-run governance prototype. It must not become the main execution lane before V-KPI has at least one end-to-end business intelligence output that uses real external signals, evidence, and user feedback.

## Current Decision

- Keep the runtime source page as a small read-only frozen screen.
- Move the previous 7,535-line prototype out of `frontend/src` so it no longer violates the 800-line source guard.
- Do not add new Repair Center stages, write modes, or side workflows.
- Only allow bug fixes, deletion, extraction, or documentation updates related to this freeze.

## Explicitly Frozen

- evidence reference persistence beyond preview mode
- cancel persistence beyond preview mode
- R102+ Repair Center expansion rounds
- direct business-table writes from Repair Center
- provider calls, DB migrations, or task execution from Repair Center UI

## Unfreeze Conditions

Repair Center can be reconsidered only after all of these are true:

- at least one real market or KOL intelligence signal has been ingested from an external source
- at least one Intelligence Card has evidence links and a human feedback action
- the result of that action is visible in a business workflow, not only in a governance queue
- the implementation plan fits the 800-line guard and domain ownership rules

Until then, Repair Center is not product value. It is archived context.
