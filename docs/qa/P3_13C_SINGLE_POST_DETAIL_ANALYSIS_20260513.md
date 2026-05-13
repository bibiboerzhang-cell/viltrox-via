# P3.13C Single Post Detail / Analysis QA

## Scope

P3.13C closes the first layer of media UX after P3.13A/B:

- Post cards now expose a real `单帖详情` action.
- Home / Posts / profile drawer content can open the same post detail drawer.
- The drawer uses the existing media proxy fallback chain for video/image rendering.
- `打开原帖` opens the normalized platform URL when the post has one.
- `运行单帖分析` calls the real backend endpoint `/api/admin/kol/tools/analyze-url` through `analyzeDataAnalysisPostUrl`.

## What This Does Not Claim

- It does not claim every Instagram/TikTok CDN video is playable; P3.13B already proved some CDN URLs return 403 and must fall back to thumbnail/open-original.
- It does not run paid LLM/video analysis automatically. The user must click `运行单帖分析`, and the endpoint spends quota only then.
- It does not implement bulk analysis of all posts. That remains a later media intelligence step.

## Acceptance

- No post action is decorative: detail opens a drawer, original opens the platform URL, analysis calls a real endpoint or shows a real error.
- Missing post URL disables real analysis and explains why.
- Build passes.
- Contract smoke passes.

## Verification

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p3_13c_post_detail_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_13c_post_detail_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_13a_media_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_1c_media_proxy.py
cd frontend && npm run build
```

## Manual Browser QA

1. Open 数据分析.
2. Open an account with real posts.
3. Click `单帖详情` on a post card.
4. Confirm the drawer shows media, metrics, text, and original-post action.
5. Click `打开原帖` and confirm a platform page opens.
6. Click `运行单帖分析` only when API/LLM spend is acceptable.
