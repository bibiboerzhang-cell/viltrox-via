# P4 Step27 Media Browser QA

Date: 2026-05-14
Scope: Data Analysis account detail / Posts / Content / single-post drawer / media and AI analysis path
Target: http://127.0.0.1:5173/#/dataAnalysis
Account observed: Godox Global (Instagram)

## Summary

This QA used the running local frontend and backend, not static inspection. The page is not fake: real account metrics, real post rows, real original-post links, and real single-post AI analysis are present. However, the current media UX is still not at the expected product quality. The main gap is not backend absence; it is UX completeness, state consistency, and media preview/playback quality.

## Verified Working

- Runtime health preflight passed before browser QA.
- Data Analysis page loaded directly while logged in.
- Account detail page rendered real account identity and metrics.
- Source tooltips / lineage indicators are visible on KPI cards.
- Posts tab lists 5 real posts with views, likes, comments, engagement, and original post links.
- Single-post drawer opens from the Posts table.
- Single-post drawer shows post text, metrics, original post link, and media section.
- Single-post AI analysis completed successfully.
- AI analysis result reported status `done`, score `60`, and method `gemini_fileapi_Instagram_gemini-2.5-flash`.
- Analysis provider chain showed OpenAI + Gemini + Claude.
- Content tab lists 5 / 5 content records with platform links and detail/analyze actions.
- Media-related smoke contracts passed.
- Frontend build passed.
- Full backend pytest passed.

## Findings

### P1: Runtime status indicator mismatch

The `/health` and runtime smoke both pass, but the visible frontend banner still shows backend status as checking. This means the page-level status widget can mislead the user even when the backend is actually healthy.

Impact: user trust issue; not a data-loss risk.

### P1: Crawl gate/action state inconsistency

The account detail page showed `抓取链路未通过` and the blocker indicated system/platform setting not enabled, but the account header button showed `关闭抓取`.

Impact: user cannot confidently know whether the account is enabled, disabled, blocked, or partially configured.

### P1: Video playback fallback works, but in-app playback failed

The drawer displayed the media area and fallback message: video link invalid, open original post. The original-post fallback is acceptable for internal use, but the expected Socialinsider-like media experience is not complete.

Impact: demo/usage quality issue; users will see data but not always watch media in-place.

### P2: Posts table has no thumbnails

Posts tab currently behaves like an analytics table. It is real, but visually weak. It does not show thumbnail/video preview in the row, so users cannot quickly select content by visual identity.

Impact: selection and review workflow is slower.

### P2: Content tab is list/table-like, not media-card grid

Content tab lists 5 / 5 records and exposes actions, but it is still not a full media-card grid with playable previews, hover metrics, and quick drilldown.

Impact: not fake, but below target UX.

### P2: Single-post AI analysis latency needs better UX

The analysis path completed, but it stayed in a long `分析中...` state before returning. There is no visible staged progress like fetching media, uploading file, running Gemini, fallback provider, or finalizing.

Impact: users may think it is stuck during real analysis.

## Data Lineage Observed

- Account KPIs displayed real values and source hints.
- Posts tab values came from account posts/snapshot data exposed to frontend.
- Single-post analysis wrote/returned a real provider result.
- LLM and audit tables had existing rows after the run (`vkpi_llm_calls`, `vkpi_business_audit_logs`).

## Test Evidence

- `./scripts/run_smoke.sh smoke_vkpi_p4_25_runtime_health_preflight.py` passed before browser QA.
- `./scripts/run_smoke.sh smoke_vkpi_p4_4_media_ux_contract.py smoke_vkpi_p3_13c_post_detail_contract.py` passed.
- `npm run build` passed in frontend.
- `PYTHONPATH=backend .venv/bin/pytest tests/ -q` passed: 85 tests.

## Recommended Next Module

P4 Step28 should be a targeted Media UX fix round, not another broad feature round.

Minimum scope:

1. Fix frontend runtime status widget so it agrees with `/health`.
2. Normalize crawl gate state and account action text.
3. Add thumbnail/media preview to Posts and Content list rows.
4. Add staged progress text for single-post analysis.
5. Keep original-post fallback for failed video playback, but show it as an intentional fallback, not a broken state.
