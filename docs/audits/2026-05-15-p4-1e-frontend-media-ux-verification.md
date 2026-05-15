# P4.1E Frontend Media UX Verification

Date: 2026-05-15 07:55 Asia/Shanghai
Workspace: `/Users/bibiboer/Documents/V-KPI——marketing`
Branch: `codex/vkpi-cleanup-d7`

## Scope

This pass verifies the frontend Data Analysis / media UX batch only. No feature code was changed in this verification pass.

Tracked frontend scope:

- `frontend/src/components/vkpi/VkpiDashboard.css`
- `frontend/src/components/vkpi/pages/DataQualityPage.tsx`
- `frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/*`
- `frontend/src/services/vkpi.ui-api.ts`
- `frontend/vite.config.ts`

Contract smoke scope:

- `scripts/smoke_vkpi_p3_13c_post_detail_contract.py`
- `scripts/smoke_vkpi_p4_4_media_ux_contract.py`
- `scripts/smoke_vkpi_p4_33_media_full_content_contract.py`
- `scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py`

New files in this batch:

- `frontend/src/components/vkpi/pages/data-analysis/shared/SourceTooltip.tsx`
- `scripts/smoke_vkpi_p4_33_media_full_content_contract.py`
- `scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py`

## Backup

Created before verification:

`/Users/bibiboer/Documents/V-KPI-backups/before-p4-1e-frontend-media-ux-20260515-074754.tar.gz`

## Static Checks

- `git diff --check`: PASS
- `py_compile` for the 4 smoke files: PASS
- Frontend diff size in scoped batch: 18 tracked files, `+532 / -71`
- Current dirty entry count remains: `43`

## Build

Command:

```bash
cd frontend && npm run build
```

Result: PASS

Observed build output included:

- `tsc --noEmit && vite build`
- `167 modules transformed`
- `dist/build-info.json`
- build completed successfully

## Contract Smokes

Commands:

```bash
./scripts/run_smoke.sh smoke_vkpi_p3_13c_post_detail_contract.py
./scripts/run_smoke.sh smoke_vkpi_p4_4_media_ux_contract.py
./scripts/run_smoke.sh smoke_vkpi_p4_33_media_full_content_contract.py
./scripts/run_smoke.sh smoke_vkpi_p4_34_media_loaded_count_contract.py
```

Result:

- `smoke_vkpi_p3_13c_post_detail_contract.py`: PASS 1/1
- `smoke_vkpi_p4_4_media_ux_contract.py`: PASS 1/1
- `smoke_vkpi_p4_33_media_full_content_contract.py`: PASS 1/1
- `smoke_vkpi_p4_34_media_loaded_count_contract.py`: PASS 1/1

## Backend Runtime Check

Endpoint:

```bash
curl -sS http://127.0.0.1:8102/health
```

Result: PASS

Observed:

- `status=ok`
- `service=admin-web`
- `git_short_sha=20cd80db`
- `git_branch=codex/vkpi-cleanup-d7`
- `client_matches_server=true`

## Browser QA

Browser target: `http://127.0.0.1:8102/`

Result: PASS for scoped read-only media path.

Verified path:

1. Opened local app through backend-served build.
2. Confirmed authenticated dashboard loaded, not login page.
3. Confirmed version strip shows `FE 20cd80db` and `BE 20cd80db`.
4. Opened `数据分析`.
5. Confirmed profile detail view for `Godox Global` under Instagram.
6. Confirmed profile avatar loaded through media image proxy with non-zero dimensions.
7. Opened `Content` tab.
8. Confirmed content list text: `显示 5 / 5 条内容 · 已加载 5 条内容`.
9. Confirmed 5 post-level `打开平台` links exist and point to Instagram post URLs.
10. Confirmed all inspected images had non-zero `naturalWidth/naturalHeight`; broken image count was `0`.
11. Opened first `单帖详情 / 分析` drawer.
12. Confirmed drawer shows caption, metrics, `打开原帖`, and `运行单帖分析` entry.
13. Confirmed video element exists with controls and playable metadata:
    - `readyState=4`
    - `duration=77.530023`
    - `videoWidth=1280`
    - `videoHeight=720`
14. Browser console error/warning count for this pass: `0`.

Not executed intentionally:

- `运行单帖分析` was not clicked in this verification pass because it can trigger real URL analysis and provider cost. This needs a separate live-analysis QA step with explicit budget/gate scope.
- `刷新该账号` / `关闭账号抓取` were not clicked because they are mutation actions and not part of this read-only media UX verification.

## Findings

PASS:

- Frontend build is green.
- Contract smoke coverage for post detail and media loaded/full-content behavior is green.
- Backend-served build is healthy and FE/BE hash is consistent.
- Data Analysis profile avatar loads through proxy.
- Content tab is no longer a placeholder for the tested account: it renders 5 real posts.
- Post cards include original platform links.
- Single post detail drawer opens and shows real caption + metrics.
- Video metadata loads successfully in browser for the first tested post.

Residual issues for later rounds:

- The profile-level gate message still says the account switch is on but platform switch is not enabled. This is a configuration/state issue, not a media-rendering failure.
- The filter/customize drawer was already open during browser QA and remains visually crowded. This belongs to the Settings/DataAnalysis UX cleanup lane, not P4.1E verification.
- Live `运行单帖分析` endpoint QA remains pending and should be isolated because it can consume LLM/provider budget.

## Decision

P4.1E can be marked verified for frontend/media UX read path.

Next recommended module: P4.1F smoke-script batch verification, then P4.1G unit-test batch verification, before any cleanup commit or broader P4.2 work.
