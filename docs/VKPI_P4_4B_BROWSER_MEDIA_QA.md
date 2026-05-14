# V-KPI P4.4B Browser Media QA

Date: 2026-05-14
Build: 51b2842a
Scope: Data Analysis account detail media UX, real browser path.

## Baseline

- Worktree was clean before QA.
- Backend `/health` returned `client_matches_server=true`.
- Frontend and backend build hash both showed `51b2842a`.
- QA path used `http://127.0.0.1:8102/#dataAnalysis`.

## Verified Path

1. Opened Data Analysis page.
2. Opened account detail for `Godox Global`.
3. Switched to `Content` tab.
4. Verified real content list displayed `5 / 5` items.
5. Verified media surface:
   - image elements present
   - video element present
   - no fake placeholder-only state
6. Opened first post detail drawer through `单帖详情 / 分析`.
7. Verified drawer actions:
   - `打开原帖` points to `www.instagram.com`
   - profile home link points to `www.instagram.com`
   - `运行单帖分析` is enabled only when a real original URL exists
8. Ran one real single-post analysis from the drawer.

## Result

- Image proxy: PASS.
- Avatar/profile media: PASS.
- Video proxy: PARTIAL PASS.
- Original post link: PASS.
- Single-post detail drawer: PASS.
- Real single-post analysis: PASS.

## Observed Real-World Edge Case

Some Instagram CDN video URLs returned `403` from `/api/admin/vkpi/media/video-proxy`.
The UI did not leave a broken player in the drawer; it showed the fallback copy and kept `打开原帖` available.
Other Instagram video URLs returned `206`, so the video proxy path itself is functional.

## Notes

- The single-post analysis endpoint returned HTTP 200 and displayed a Chinese analysis result.
- The current analysis result is functionally real, but the presentation is still raw. Later UX work can format `status`, `quality_score`, summary, and recommendations into structured cards.
- No code change was required in this round.

## Progress Impact

- P4.4A static/media contract hardening: done.
- P4.4B real browser media QA: done.
- Remaining media work should focus on UX polish and broader multi-account media coverage, not basic wiring.
