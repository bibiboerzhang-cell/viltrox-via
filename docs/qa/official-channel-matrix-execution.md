# Official Channel Matrix Execution

## Goal

Build a precise traffic and account operations view for Viltrox-owned accounts, external KOL activity, and staff-owned execution progress.

The UI must answer:

- Where did views come from?
- Which platform, account, post, KOL, project, and staff owner contributed them?
- Which records have real media, source URLs, and sync evidence?

## Execution Rules

- Keep each round small and recoverable.
- Back up before each round.
- Do not grow `ChannelsPage.tsx` into a large page file.
- New UI components should usually stay under 250 lines.
- Existing large files should only do data wiring and composition.
- Prefer shared typed contracts over ad hoc string parsing in UI.
- Do not show fake empty states when snapshot or Apify data exists.
- Verify with `py_compile`, `npm run build`, and `/health`.

## Current R0 Data Snapshot

Verified on 2026-05-16 from `vkpi_employee_channels` plus `vkpi_channel_metrics`.

| Platform | Accounts | Followers | Posts | Views |
| --- | ---: | ---: | ---: | ---: |
| Facebook | 4 | 214,858 | 8 | 0 |
| Instagram | 6 | 179,112 | 1,109 | 280,239 |
| Reddit | 1 | 4,590 | 3 | 0 |
| TikTok | 5 | 163,283 | 13 | 2,649,186 |
| X | 1 | 3,464 | 2,021 | 1,165 |
| YouTube | 1 | 33,600 | 808 | 19,721,791 |

Totals:

- Accounts: 18
- Platforms: 6
- Posts: 3,962
- Account-level views: 22,652,381

## Display Contract

### Level 1 Platform Matrix

Each platform card must show:

- platform key and label
- official platform icon or platform mark
- account count
- total followers
- total posts
- total views
- sync health

### Level 2 Account Cards

Each account card must show:

- account avatar
- display name
- handle
- account URL
- owner staff id/name when available
- followers
- posts count
- total views
- engagement rate
- last sync status/time

### Level 3 Content Cards

Each content card must show:

- concise title/caption
- media thumbnail or video cover on the right
- views
- likes
- comments
- shares
- posted time
- sync/source status
- original post link

## Backend Contract

Current official-owned account source:

- `GET /api/marketing/channels/official-matrix`
- `GET /api/marketing/channels/official-views-evidence`

Stable fields expected by UI:

- `platforms[].platform`
- `platforms[].label`
- `platforms[].total_views`
- `platforms[].total_posts`
- `platforms[].total_followers`
- `platforms[].accounts[]`
- `accounts[].staff_id`
- `accounts[].staff_name`
- `accounts[].staff_email`
- `accounts[].staff_avatar_url`
- `accounts[].staff_role`
- `accounts[].avatar_url`
- `accounts[].account_url`
- `accounts[].followers`
- `accounts[].posts_count`
- `accounts[].total_views`
- `accounts[].engagement_rate`
- `accounts[].posts[]`
- `posts[].title`
- `posts[].url`
- `posts[].media_url`
- `posts[].views`
- `posts[].likes`
- `posts[].comments`
- `posts[].shares`
- `posts[].posted_at`

`official-views-evidence.total_views` must remain the account-level total and must not shrink because the returned evidence rows are limited.

## Current Data Gaps

- Facebook has followers and post counts, but content-level views are zero and extracted post cards are mostly unavailable.
- Some accounts lack `avatar_url`: Facebook Flash/US, Instagram Thailand/US, Reddit, X, TikTok Store.
- TikTok media currently often points to post URLs rather than stable thumbnail assets.
- Instagram and YouTube already have usable content thumbnails.
- Staff ownership is present as `staff_id=84` for current official accounts. R5 now exposes staff name, email, avatar, and role in the matrix contract.

## Planned Rounds

### R1 Main Dashboard Views Attribution

Refine the main dashboard views drilldown so it clearly separates:

- Viltrox-owned accounts
- external KOL content
- staff-owned/account execution
- unattributed content

### R2 Employee Platform Level 1

Replace the flat platform-binding table with platform matrix cards.

### R3 Employee Platform Level 2

Clicking a platform shows account cards with avatar, owner, account snapshot, and sync state.

### R4 Employee Platform Level 3

Clicking an account shows content cards with concise title, right-side media thumbnail, and metrics.

### R5 Staff And KOL Progress

Add staff/KOL progress view for owned accounts, external KOLs, active projects, published content, and missing data.

### R6 Apify Refill

Use Apify without quota restriction for missing avatars, covers, and content-level data. Prioritize YouTube, TikTok, Instagram, then X, Facebook, Reddit.

R6 must still run in bounded slices:

- R6a: expose a missing-data report before live crawling.
- R6b: wire live refill for one platform/account path at a time.
- R6c: expand to remaining platforms only after the first refill writes correct snapshots.

## R0 Changes

- Restored `wip-three-tier-matrix-2026-05-15` before this execution sequence.
- Added official matrix and official views evidence backend routes.
- Added frontend types for official platform/account/post hierarchy.
- Added UI API client function `getOfficialChannelMatrix`.
- Corrected official evidence summary so `total_views` represents account-level total views.

## R2/R3 Frontend Progress

- Added `pages/channels/channelTypes.ts` for platform, account, and content contracts.
- Added `pages/channels/useOfficialChannelMatrix.ts` to load and normalize the official channel matrix.
- Added `pages/channels/ChannelPlatformMatrix.tsx` for Level 1 platform cards.
- Added `pages/channels/ChannelAccountList.tsx` for Level 2 account cards.
- Added `pages/channels/ChannelContentList.tsx` for Level 3 content cards.
- Added split channel CSS files instead of growing `VkpiDashboard.css`.
- `ChannelsPage.tsx` now composes the matrix and keeps the legacy table as a filtered fallback below it.
- Added `scripts/smoke_vkpi_channel_matrix_frontend.py` to guard the new employee-platform matrix entry.

## R5 Frontend Progress

- Added staff owner fields to the official account matrix query through `staff -> users`.
- Added `ChannelStaffProgress.tsx` and `channelStaff.css` as a separate owner-progress layer.
- `ChannelsPage.tsx` now supports staff filtering in the new matrix and the legacy platform-binding table.
- The staff layer currently covers owned official accounts; external KOL/project progress remains the next bounded slice.

## R6a Gap Report Progress

- Added `backend/app/services/vkpi/channel_gaps.py` so gap detection is separate from the large channel service.
- Added `GET /api/admin/vkpi/channels/official-gap-report`.
- Added `ChannelGapPanel.tsx`, `useOfficialChannelGaps.ts`, and `channelGaps.css`.
- The employee platform now shows missing avatars, missing content lists, missing media covers, and missing content-level views.
- The gap panel is filtered by selected platform and selected staff owner.
- This round does not spend Apify quota; it prepares the exact refill targets for R6b.

## R6b Refill Progress

- Added `backend/app/services/vkpi/channel_refill.py` for provider-backed employee channel sync.
- `sync-now` now routes through the refill service instead of only marking `not_configured`.
- YouTube uses the existing YouTube Data API crawler.
- Instagram and TikTok use the existing Apify-backed industry crawlers.
- The refill writes `vkpi_channel_metrics`, updates account display URL/avatar fields, and writes `vkpi_channel_audit`.
- Unsupported platforms still return `not_supported` instead of fake metrics; Facebook/X/Reddit remain queued for later bounded platform slices.

## R7 Layout And Media Progress

- Moved the staff owner progress layer into the upper-right team matrix card so the standalone lower owner section does not duplicate the platform/account flow.
- Simplified the missing-data panel into a compact `素材与证据缺口` summary with aggregated issue chips and only the top refill targets visible.
- Tightened content cards so title, metrics, date, source link, and the right-side media block have fixed bounds and do not overlap.
- The right-side media block now opens the original post when a source URL exists.
- Instagram and TikTok external media URLs are not treated as reliable thumbnails until a local cache/proxy is added; those cards show `待缓存` instead of broken images.

R7 intentionally does not download or cache Instagram/TikTok media locally. That should be handled as a separate refill/cache slice after the layout is stable.

## R8 Media Proxy Progress

- Reused the existing admin media proxy instead of adding a second media-serving stack.
- Added a shared frontend `mediaProxy` helper so employee-platform cards and data-analysis drawers use the same image/video proxy rules.
- Extended image proxy allowlisting for TikTok/Apify CDN hosts so official avatars and cover images can render through same-origin cache.
- Platform avatar stacks, account avatars, staff avatars, and content-layer avatars now resolve through `proxiedImageUrl`.
- Content cards now choose image proxy or video proxy based on platform/media URL; TikTok videos can render through `/api/admin/vkpi/media/video-proxy` while the media tile still opens the original post.
- If a URL is not allowlisted or is missing, the card keeps the explicit `待缓存` state rather than rendering a broken image.

R8 image proxy caches bytes under the existing backend media cache on first request. Video remains a bounded same-origin proxy stream, not a full local video download, to avoid large-file storage and timeout risk in this slice.

## R9 Apify Single-Account Refill Progress

- Provider readiness was verified without printing secrets: Apify and YouTube providers are configured in the local runtime.
- Instagram gap-account test: `viltrox.us` returned an Apify `not_found` payload. The refill quality gate now marks this as `no_results` instead of a false `synced`.
- TikTok gap-account test: `viltrox.store` also returned empty data and is now marked `no_results`, not a successful sync.
- Instagram positive-control test: `viltrox.cine` synced successfully through Apify; followers changed `34,524 -> 34,521`, total views changed `278,174 -> 278,178`, and 12 content samples with media were present in the raw sample.
- TikTok positive-control test: `viltrox.global` synced successfully through Apify; followers changed `128,500 -> 128,700`, total views changed `2,148,290 -> 2,149,301`, and 3 content samples with media were present.
- TikTok matrix media extraction now uses `videoMeta.coverUrl` / `originalCoverUrl` before falling back to the original post URL, so cover images render through the image proxy rather than being mistaken for video URLs.
- Current gap report after the TikTok cover mapping fix: 18 accounts, 10 gap accounts, issue counts `missing_posts=7`, `missing_avatar=7`, `missing_media=3`.

R9 proves the provider path can write real snapshots, but it also separates invalid/bound-only accounts from successful syncs. The next cleanup should either correct or remove invalid handles such as `viltrox.us` and `viltrox.store`, instead of repeatedly spending Apify runs on accounts that return empty payloads.

## R10 Playback Evidence Attribution Progress

- Official views evidence rows now carry attribution fields in addition to the old label/source/amount shape: `attributionType`, account id/name/handle/url, staff id/name/email/role, post id, and media URL.
- The evidence drawer maps official-account rows so `ownerName` represents the responsible staff owner when available, while account name and handle remain separate fields.
- The playback evidence drawer now renders Viltrox owned traffic as a platform -> account -> content hierarchy with account owner visible at the account and post rows.
- Official post rows show platform, account handle, responsible staff owner, views, likes, comments, shares, source status, original-post link, and a right-side media tile.
- The right-side media tile reuses the shared media proxy from R8 so Instagram/TikTok covers do not hotlink directly from blocked CDN URLs.
- Playback total display prefers the official matrix account-level total when the matrix is present, so limited evidence rows do not shrink the headline attribution total.

R10 still keeps external KOL/project traffic separate from Viltrox-owned official-account traffic. The next bounded slice should clean invalid bound-only account records and then tighten KOL/project playback attribution if needed.

## R11 Channel State Cleanup Progress

- Official account matrix rows now include `last_sync_error`, so no-result provider responses can be shown without pretending the account has a valid snapshot.
- Gap reporting separates provider readiness from one-click refill support. YouTube/Instagram/TikTok can use the current refill path; Facebook/X/Reddit no longer display a misleading `apify 已就绪` refill state.
- Accounts marked `no_results` now receive a higher-priority `抓取无结果` issue and a recommendation to verify the handle/homepage before spending another crawl.
- Account cards and the legacy platform binding table translate sync states into user-facing labels such as `已同步`, `抓取无结果`, `待配置`, and `未接入补抓`.
- Missing metric cells for `no_results` accounts show `无快照` instead of implying a pending normal sync.

R11 is a state-labeling and data-quality slice only. It does not delete invalid accounts or run another Apify crawl.

## R12 Official Media Cache Progress

- Added a small local image cache service for allowlisted official-channel media hosts.
- Provider refill now prewarms account avatars and recent post covers while Apify/YouTube URLs are still fresh.
- The platform/account/content matrix now prefers `/api/vkpi-media/image-cache/{digest}` when a local cached image exists, falling back to the original external URL/proxy path otherwise.
- Added a bounded cached-image route that serves only existing cache files by SHA-256 digest; it does not proxy arbitrary URLs.
- Video files are still streamed/proxied rather than downloaded as full local files, to avoid uncontrolled storage growth.

R12 fixes the stale signed-image problem for future syncs and any current media that can still be fetched. Invalid `no_results` accounts remain a separate cleanup decision.

## R13 Official Gap Refill Plan

- Facebook is now treated as an auto-refill platform for official employee channels.
- Reddit is now treated as an auto-refill platform for official employee channels.
- Facebook refill writes page profile plus recent posts into `vkpi_channel_metrics.raw_payload_json`.
- Reddit refill writes subreddit profile plus recent posts into `vkpi_channel_metrics.raw_payload_json`.
- Both refill paths attach a small `quality` summary with post count, URL coverage, media coverage, and engagement coverage so Reddit can be judged as platform/post quality instead of only an account row.

R13 uses the exact account URLs supplied by the operator for the missing official accounts.

## R13 Refill Execution Results

- Corrected Facebook Flash to `https://www.facebook.com/profile.php?id=61574876342398`.
- Corrected Facebook USA to `https://www.facebook.com/viltrox.usa`.
- Corrected Instagram USA to `https://www.instagram.com/viltrox.usa/`.
- Corrected Instagram Official to `https://www.instagram.com/viltrox.official/`.
- Corrected Instagram Thailand to `https://www.instagram.com/viltrox_thailand/`.
- Instagram USA, Official, and Thailand synced real followers/posts/views and now expose cached content media in the matrix.
- Facebook USA and Flash synced profiles plus 12 recent post samples each. The current Apify source returned followers, likes, comments, and media, but no content-level view counts.
- Reddit synced as a platform/community feed: 6 post samples, followers, upvotes, comments, and media. Comments are excluded from the content-post layer. Reddit views remain 0 because the current source does not provide play/view counts.
- Image cache prewarm now prioritizes recent content media and allows Reddit image hosts, so matrix cards prefer `/api/vkpi-media/image-cache/{digest}` instead of stale external signed URLs.

## R14 Baseline Refresh Planning

- Added a dry-run baseline plan script at `scripts/vkpi_official_baseline_plan.py`.
- Added the baseline and refresh strategy doc at `docs/qa/official-channel-baseline-refresh-plan.md`.
- The plan separates one-time `baseline_full_backfill` from daily `daily_recent_sync`, hot recent-post refresh, and weekly backfill.
- The dry run does not call Apify or YouTube. It only reads the current official matrix and reports first-batch limits, target item counts, and accounts that need pagination or special actor support before a true full baseline.
- Current dry-run output: 18 official accounts, 6 platforms, 975 first-batch items, 3,848 baseline target items, 510 daily recent-refresh items, and 8 accounts that need pagination or a special actor to finish the deeper baseline.
- Verification passed: `py_compile`, `scripts/smoke_vkpi_channel_matrix_frontend.py`, `git diff --check`, and JSON output validation for the dry-run plan.
- Current conclusion: do not run a full paid baseline until the execution gate can distinguish current safe first-batch crawling from deeper pagination/special actor work.

## R15 Team Matrix Metrics And Full Baseline Gate

- Fixed the platform-summary and responsible-owner summary headers so right-side metric values wrap into bounded metric pills instead of overlapping when the card is narrow.
- Added more computed summary metrics to the UI: synced accounts, platform count, follower total, playback total, and average views per content item.
- Extended manual channel sync so callers can pass a higher `max_posts` value without changing the default page button behavior.
- Added an `official_full_baseline` cron/manual job gate with per-platform baseline limits: YouTube 1000, Instagram 1000, TikTok 300, Facebook 250, Reddit 150, X 200.
- Added X to official-channel refill support so the first full company-account baseline can include the currently bound X account instead of leaving it as unsupported.
- Raised the real crawler caps to match baseline mode: Instagram 1000, TikTok 300, X 200, and YouTube now paginates search/video detail calls up to 1000 videos instead of stopping at the first 50.

### R16 Instagram Playback Correction

- Root cause: the Instagram profile actor only returned `latestPosts=12`, while account cards displayed the historical `postsCount`; this made playback look like an account total even though it was only a recent 12-post sample.
- Switched the Instagram content layer to `apify/instagram-scraper` post mode for deeper playback totals while keeping the profile actor for followers, avatar, and total post count.
- Positive control: `viltrox.official` returned 50 post rows with `255,297` summed video views, versus the old 12-post profile sample at `13,154`.
- First live baseline attempt was stopped because the Facebook posts actor had no local wait limit and kept polling; Facebook Apify calls now use a bounded run/wait timeout so one slow account cannot block the full baseline.
