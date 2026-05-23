# V-KPI Data Trust Report

Date: 2026-05-23 UTC

Scope: P0 sync-governance closeout plus the first P1 data-trust pass. This report is read-only except for the code/docs commits listed below. It does not run 1012 deep scan, Gemini/LLM batch, or legacy full KOL refresh.

## Current Deployment

- Live domain: `https://viltroxtest.com`
- Latest deployed commit: `137c45d3 fix(vkpi): order sync guard runs by stable timestamp`
- Health: `/health` returns `ok`
- Client/server hash: matched at `137c45d3ea701f2b7c0c6b3b77fb758e2b304bc2`
- Backup before latest deploy: `runtime/prod-sync/20260523T015349Z`
- Previous code deploy backup: `runtime/prod-sync/20260523T014534Z`
- Previous deploy backup: `runtime/prod-sync/20260523T014215Z`

## Sync Governance

- Legacy KOL daily refresh is not the active timer path.
- Current timer command is official-only: `scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol`.
- `vkpi-sync-daily.timer` remains scheduled for natural verification at `2026-05-23 04:04:09 UTC`.
- Latest old full-KOL run remains intentionally classified as strategy-interrupted and acked; it should not be interpreted as an unexplained production accident.
- Preflight at `2026-05-23 01:55 UTC`: service was inactive, next timer remained `2026-05-23 04:04:09 UTC`, `/health` was `ok`, `guard_allowed=true`, and `ack_required=false`.
- Guard/status queries now order `vkpi_sync_runs` by `started_at DESC NULLS LAST, created_at DESC NULLS LAST`, matching the production schema where `vkpi_sync_runs` has no synthetic `id` column.

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

- `docs/vkpi/adr/2026-05-23-media-cache-policy.md`
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
- Media cache policy now separates official-account prewarm, future qualified KOL lazy cache, and legacy cold KOL no-daily-prewarm behavior.
- Video cache policy remains explicit/on-demand; YouTube uses embeds, and poster images must not be presented as playable video.
- P0 recheck at `2026-05-23 01:58 UTC`: official matrix returned 18 active channels; every official channel had an avatar URL. The YouTube official channel had 12 sampled posts, 12/12 with YouTube embed IDs and cached image thumbnails.
- Public cache spot checks passed: sampled official image-cache URLs returned `200 image/jpeg`; sampled video-cache URLs returned `206 Partial Content` with `video/mp4`.
- Separate legacy `vkpi_industry_posts` live-source audit remains blocked for old Instagram source URLs: sampled stored external image/video URLs returned source `403`. This is not a green media state; UI must continue to rely on cached assets where present and expose original links/cache status instead of implying expired source URLs are playable.

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

## Canonical Post Identity

Artifacts:

- `docs/vkpi/adr/2026-05-23-canonical-post-identity.md`

Acceptance:

- Identity rules are frozen for `youtube`, `instagram`, `tiktok`, `facebook`, `reddit`, and `x`.
- The ADR defines `platform`, `canonical_post_uid`, `provider_post_id`, and `canonical_url`.
- It explicitly connects current metrics `post_uid`, comments `external_post_id`, media URL matching, and frontend post actions to the same platform-scoped identity contract.
- It does not run a migration or rewrite crawlers; implementation is deferred to a shared helper after timer and P1 gates remain stable.

## Silent Exceptions

Acceptance:

- `scripts/check_silent_exception_baseline.py` before first pass: 83 silent handlers.
- After bounded V-KPI service passes: 40 silent handlers.
- Reduction: 43.
- Covered high-risk paths include channel cache clearing, channel media JSON parsing, official refill cache clearing, sync-status metadata parsing, audit fallback queries, budget/settings JSON parsing, media package parsing, task active-lock index creation, data-quality audit logging, decision aggregates, learning snapshot, reports, product-analysis evidence, KOL claim audit, KPI ledger, team feedback, LLM env lookup, reconciliation stats, outcome JSON parsing, KOL pool cache/schema guards, industry timestamp parsing, and recommendation rollback paths.
- Remaining findings are outside `backend/app/services/vkpi` and sit mostly in DB/runtime compatibility, scraping, and non-V-KPI service layers. They should be handled in later bounded passes, not as one large cleanup.

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
