# V-KPI Domain OS Worktree Convergence Manifest

Date: 2026-05-24 PT

## Purpose

The first real Domain PoC must not carry the existing dirty worktree with it. This manifest classifies the current modified and untracked files so the next step can converge the worktree into reviewable buckets before `domains/data-quality` work starts.

This is a classification artifact only. It does not stage, stash, delete, deploy, migrate, or run providers.

## Summary

| Bucket | Count | Proposed treatment |
|---|---:|---|
| `backend_legacy_split` | 44 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `channels_ui_split` | 7 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `css_split_inventory` | 14 | Audit with CSS split standards before claiming completed refactor credit. |
| `data_analysis_ui_split` | 7 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `discover_ui_split` | 11 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `domain_os_governance` | 7 | Keep first; guardrail and measurement artifacts. |
| `domain_platform_shared_skeleton` | 6 | Keep with governance only after confirming these remain skeletons, not business migration credit. |
| `frontend_api_split` | 25 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `frontend_legacy_split` | 35 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `market_signal_and_llm_preflight` | 36 | Hold separate from Domain PoC; do not run providers or migrations. |
| `other_review_required` | 2 | Manual review before any commit. |
| `projects_ui_split` | 2 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `repair_center_freeze_inventory` | 37 | Keep separate; freeze means no new feature expansion. |
| `scripts` | 2 | Attach to the bucket they verify; otherwise hold separate. |
| `settings_ui_split` | 9 | Review as refactor bucket; do not mix into Data Quality PoC unless directly required. |
| `tests` | 4 | Attach to the bucket they verify; otherwise hold separate. |

## Convergence Rules

1. `domain_os_governance` should be reviewed first because it defines the execution rules.
2. `domain_platform_shared_skeleton` can travel with governance, but it does not count as Domain business migration.
3. `market_signal_and_llm_preflight` must remain separate from the first Domain PoC because it has external-data and provider semantics.
4. `repair_center_freeze_inventory` must remain separate and cannot add new product behavior.
5. CSS and UI split buckets require audit before they are called complete.
6. The first Domain PoC should have a small diff and should not import unrelated files from these buckets.

## Bucket Definitions

### `domain_os_governance`

Guardrail and measurement documents:

- `ENGINEERING_GUARDRAILS.md`
- `docs/vkpi/domain-os-architecture.md`
- `docs/vkpi/domain-os-d2-api-split.md`
- `docs/vkpi/domain-os-line-guard-diff-2026-05-24.md`
- `docs/vkpi/domain-os-line-guard-report-2026-05-24.md`
- `docs/vkpi/domain-os-ownership.md`
- `docs/vkpi/domain-os-split-roadmap.md`

### `domain_platform_shared_skeleton`

Directory skeletons only:

- `backend/app/domains/`
- `backend/app/platform/`
- `backend/app/shared/`
- `frontend/src/domains/`
- `frontend/src/platform/`
- `frontend/src/shared/`

### `repair_center_freeze_inventory`

Repair Center code, migrations, tests, and docs. These should be kept separate and only receive extraction, deletion, and bug-fix work until the freeze is lifted.

### `market_signal_and_llm_preflight`

Market signal, Reddit, industry automation, LLM quality, provider preflight, related scripts, migration drafts, and tests. This bucket prepares the first real intelligence signal but must not be mixed into the first Domain PoC.

### `frontend_api_split`

`frontend/src/services/vkpi.ui-api.ts` plus the new domain API client files under `frontend/src/services/vkpi/`. This is not the final Domain migration yet; the next pass should move the relevant API slice into the owning `frontend/src/domains/<domain>/api.ts`.

### `css_split_inventory`

CSS files and split style directories. This bucket must pass the CSS split audit before it is marked complete.

### UI split buckets

`discover_ui_split`, `channels_ui_split`, `data_analysis_ui_split`, `settings_ui_split`, `projects_ui_split`, and `frontend_legacy_split` are legacy UI refactor inventory. Future work should move business-owned slices into `frontend/src/domains/*` instead of adding more legacy subfolders.

### `backend_legacy_split`

Backend service/router refactor inventory that still lives outside `backend/app/domains/*`. Future extraction should land in explicit domains or platform/shared modules.

## Next Action

Before the first `domains/data-quality` PoC:

1. Review and keep the `domain_os_governance` bucket.
2. Decide whether `domain_platform_shared_skeleton` is accepted as structure-only.
3. Hold `repair_center_freeze_inventory` and `market_signal_and_llm_preflight` out of the PoC.
4. Run the CSS audit for `css_split_inventory`.
5. Start Data Quality PoC from a clean, minimal diff.
