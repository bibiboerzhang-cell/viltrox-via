# V-KPI Data Trust Report

Date: 2026-05-23 UTC

Scope: P0 sync-governance closeout plus the first P1 data-trust pass. This report is read-only except for the code/docs commits listed below. It does not run 1012 deep scan, Gemini/LLM batch, or legacy full KOL refresh.

## Current Deployment

- Live domain: `https://viltroxtest.com`
- Latest deployed commit: `cb2a0a82 chore(vkpi): log second silent exception batch`
- Health: `/health` returns `ok`
- Client/server hash: matched at `cb2a0a826b9e0f7a617853a0c26a07b248a969ae`
- Backup before latest deploy: `runtime/prod-sync/20260523T014215Z`
- Previous deploy backup: `runtime/prod-sync/20260523T013459Z`

## Sync Governance

- Legacy KOL daily refresh is not the active timer path.
- Current timer command is official-only: `scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol`.
- `vkpi-sync-daily.timer` remains scheduled for natural verification at `2026-05-23 04:04:09 UTC`.
- Latest old full-KOL run remains intentionally classified as strategy-interrupted and acked; it should not be interpreted as an unexplained production accident.

## Baseline Protection

Artifacts:

- `runtime/qa/20260523-p1-baseline/official-matrix-baseline-api-summary.json`
- `runtime/qa/20260523-p1-baseline/channels-baseline-protected-summary.json`
- `runtime/qa/20260523-p1-baseline/channels-baseline-protected.png`

Acceptance:

- API reports 6 platforms and 18 official accounts.
- All 18 official accounts currently carry baseline protection.
- Protected fields include `posts_count`, `total_comments`, `total_likes`, `total_shares`, and `total_views`.
- UI smoke confirmed staff progress and platform matrix render baseline-protected state instead of presenting `+0` as organic no-growth.

## Post-Level Delta

Artifacts:

- `docs/vkpi/adr/2026-05-23-post-level-delta.md`
- `runtime/qa/20260523-p1-baseline/post-level-delta-prod-readonly-summary.json`

Acceptance:

- `vkpi_channel_post_metrics` exists in production.
- 18 official accounts have post-metric rows.
- Current read-only sample: 886 post metric rows across 18 channels.
- Aggregate deltas in the sample: `views_delta=109851`, `likes_delta=6817`, `comments_delta=223`.
- The ADR defines first-seen and missing-post semantics so old posts are not converted into fake growth spikes.

## Official Full-Scope Policy

Artifacts:

- `docs/vkpi/adr/2026-05-23-official-full-scope-refresh.md`
- `runtime/qa/20260523-p1-baseline/official-baseline-plan-prod.json`

Acceptance:

- Daily official refresh stays recent-window only.
- Full baseline refresh remains a manual backup-first operation.
- Dry-run plan covers 18 accounts and 6 platforms.
- Estimated target scope: 5,722 baseline items; 16 accounts need full unlock.
- Latest 30/50 sampling is explicitly forbidden from lowering historical baseline.

## Media And Video Truth

Artifacts:

- `docs/audits/2026-05-23-official-media-source-audit.md`
- `runtime/qa/20260523-p1-baseline/official-media-source-audit.json`
- `runtime/qa/20260523-p1-baseline/official-media-image-dimensions.json`
- `runtime/qa/20260523-p1-baseline/official-media-postfix-summary.json`

Acceptance:

- 18 accounts and 860 official posts audited.
- 860/860 posts have original URLs and media candidates.
- 471/860 posts have cached image candidates.
- 139 posts expose video URLs.
- 50 YouTube posts have embed candidates.
- Image dimension sample found 12/30 cached images under 480x270 in at least one dimension, so remaining blur is mostly source/cache-size related rather than CSS cropping.
- Code now prefers renderable high-resolution official media while keeping cached candidates first when the external high-res candidate is not yet cacheable.

## Comment Contract

Artifacts:

- `docs/vkpi/adr/2026-05-23-comment-data-contract.md`
- Live read-only sample on `facebook / viltrox.cine / 1167036662310068`

Acceptance:

- Channel comment API now returns:
  - `declared_count`
  - `cached_count`
  - `comment_cap`
  - `coverage_status`
  - `comment_contract`
- Live sample: `declared_count=1`, `cached_count=0`, `comment_cap=50`, `coverage_status=not_cached`.
- UI now displays cached bodies, platform-declared count, and cap separately. It no longer implies cached comment bodies are complete.

## Silent Exceptions

Acceptance:

- `scripts/check_silent_exception_baseline.py` before first pass: 83 silent handlers.
- After two bounded passes: 46 silent handlers.
- Reduction: 37.
- Covered high-risk paths include channel cache clearing, channel media JSON parsing, official refill cache clearing, sync-status metadata parsing, audit fallback queries, budget/settings JSON parsing, media package parsing, task active-lock index creation, data-quality audit logging, decision aggregates, learning snapshot, reports, product-analysis evidence, KOL claim audit, KPI ledger, team feedback, LLM env lookup, reconciliation stats, and outcome JSON parsing.
- Remaining findings are still above baseline and should be handled in later bounded passes, not as one large cleanup.

## Verification Commands

- `./.venv/bin/python -m pytest -q tests/test_vkpi_channel_comments_contract.py tests/test_vkpi_channel_media_mapping.py`
- `./.venv/bin/python -m pytest -q tests/test_vkpi_channel_comments_contract.py tests/test_vkpi_channel_media_mapping.py tests/test_vkpi_sync_status.py tests/test_vkpi_audit_firewall_decorators.py tests/test_vkpi_p4_p12_hardening.py`
- `./.venv/bin/python scripts/smoke_vkpi_channel_matrix_frontend.py`
- `./.venv/bin/python scripts/check_silent_exception_baseline.py`
- `npm run build`
- `curl -fsS https://viltroxtest.com/health`

## Remaining Gates

1. Wait for `vkpi-sync-daily.timer` natural run at `2026-05-23 04:04:09 UTC`.
2. Confirm the timer run is `completed` or explainable without re-enabling legacy full KOL refresh.
3. Continue silent exception cleanup in smaller passes until the baseline is realistic.
4. Only after timer and data-trust gates remain stable, start P1.X.A selector work for qualified KOL refresh tiers.
