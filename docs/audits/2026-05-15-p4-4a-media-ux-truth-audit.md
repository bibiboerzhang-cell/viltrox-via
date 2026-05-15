# P4.4A Media UX Truth Audit

Generated: `2026-05-15T06:51:57.175446+00:00`
Scope: Data Analysis media read path, original-post links, media proxy contract, single-post analysis UI contract, and local DB media lineage counts.

This is a read-only audit. It does not call external platforms, run LLM analysis, or change business data.

## Result

- Checks: `9/9 PASS`, `0` GAP
- Accounts: `3` total, `2` with avatar, `0` crawl-enabled, `2` with crawl errors
- Posts: `10` total, `10` with original URL, `10` with thumbnail, `4` with video, `10` with metrics
- Account sync status: `not_configured=2, queued=1`
- Post platform distribution: `instagram=10`

## Matrix

| Area | Item | Status | Evidence | Risk | Next Action |
|---|---|---|---|---|---|
| frontend | media field extraction | PASS | 7/7 markers present | raw payload media may not surface if extractor regresses | Keep this as a contract smoke gate for crawler field changes. |
| frontend | post card real actions | PASS | 6/6 markers present | cards may become visual-only or lose original-post fallback | P4.4B should add browser spot checks for card actions, not more static checks. |
| frontend | single post drawer | PASS | 6/6 markers present | single-post analysis can look stuck or fake if busy/result states regress | P4.4B should live-click one low-risk post and record provider output. |
| backend | authenticated media proxy | PASS | 7/7 markers present | open proxy or broken Range playback would affect media trust | Keep host allowlist and auth checks; P4.4B should only test one known video URL. |
| data | account avatar coverage | PASS | 2/3 accounts have avatar_url | blank avatars make selection hard even when data exists | P4.4B should prioritize missing-avatar accounts only if they are active/synced. |
| data | original post link coverage | PASS | 10/10 posts have post_url | users cannot verify or open platform source for posts missing URLs | Backfill post_url during crawler normalization before adding more UI. |
| data | thumbnail coverage | PASS | 10/10 posts have thumbnail_url | tables and cards become hard to choose from without visual preview | P4.4B should render thumbnail fallback reason, not initials only. |
| data | video coverage | PASS | 4/10 posts have video_url | in-app playback cannot be expected for non-video or missing-video rows | Treat video playback as best-effort; original post remains required fallback. |
| data | metric coverage | PASS | 10/10 posts have non-zero metrics | analytics looks empty if crawlers store posts without metrics | P4.4B should compare displayed top cards against these stored counts. |

## P4.4B Recommended Fix/QA Scope

1. Run one browser live path for a synced account: Content tab -> post card -> original post link -> single-post drawer.
2. Run exactly one low-risk `运行单帖分析` call and record provider/status/latency; do not batch-call LLM.
3. For active synced accounts with missing avatar or media, diagnose data source before changing UI.
4. Keep video playback best-effort: proxy first, then explicit original-post fallback; do not imply all platform videos are locally playable.
5. Any unsupported action must be disabled with a reason tooltip; no decorative controls.
